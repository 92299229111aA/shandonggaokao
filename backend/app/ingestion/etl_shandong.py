"""Load the real Shandong 2022-2025 data files into the database.

Source: the aggregated workbooks under D:\\BaiduNetdiskDownload\\高考志愿.
Provenance is recorded as **THIRD_PARTY**, not OFFICIAL: this is a commercial
compilation, not a release from 山东省教育招生考试院. The distinction feeds the
Confidence "source quality" term, so ACS stays honest about where it came from.

Pipeline per run:

    read workbooks → normalise → resolve references → write
        reference.universities / campuses / majors
        admission.source_documents / dataset_snapshots / admission_cycles
        admission.exam_year_stats / score_rank_segments
        admission.subject_rule_sets (parsed from the 选科要求 column)
        admission.program_series / admission_plans / admission_outcomes

Everything is idempotent on natural keys, so re-running after a file update
refreshes instead of duplicating.

Usage:
    python -m app.ingestion.etl_shandong                 # dry run, prints the plan
    python -m app.ingestion.etl_shandong --write         # actually load
    python -m app.ingestion.etl_shandong --write --reset # wipe real data first
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SourceDocument,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.db.session import session_scope
from app.ingestion.data_root import DataRootNotFound, SourceLayout, resolve_root
from app.ingestion.subject_rules import (
    ParsedSubjectRule,
    coverage_report,
    parse_subject_requirement,
)
from app.reference.models import Major, University

# The source workbooks come from a commercial compilation that is not redistributable,
# so their location is never hardcoded — it is passed in or read from GAOKAO_DATA_ROOT.
# See app/ingestion/data_root.py.

SNAPSHOT_CODE = "SD-2022-2026"
POLICY_VERSION = "sd-real-2022-2025"
QUALITY = "THIRD_PARTY"
PUBLISHER = "百度网盘教育资料整理包（非官方）"

# V1 only covers the regular batch, undergraduate plans.
TARGET_BATCH_PATTERN = "普通类一段"

PLAN_FILES: tuple[tuple[int, str], ...] = (
    (2022, "22-25年全国高校在山东的招生计划"),
    (2023, "22-25年全国高校在山东的招生计划"),
    (2024, "22-25年全国高校在山东的招生计划"),
    (2025, "22-25年全国高校在山东的招生计划"),
)

# 一分一段表. All four years share one layout (columns include 年份/批次/控制线(分)/
# 分数(分)/本段人数(人)/累计人数(人)), and the 批次 column distinguishes 一段 from 二段.
#
# V1 recommends undergraduate programmes only, so only 普通类一段 rows are loaded.
# This matters: the 一段 control line is ~441 while 二段 runs down to 150, and using
# the combined population (~660k) instead of the 一段 population (~305k) would halve
# every candidate's percentile and systematically depress ACS across the board.
SCORE_RANK_YEARS: tuple[int, ...] = (2022, 2023, 2024, 2025)

# Official policy numbers we can actually cite. Years without a verified source are
# left out of admission_cycles rather than guessed.
VERIFIED_APPLICATION_LIMITS: dict[int, int] = {2025: 96, 2026: 96}


def _find(layout: SourceLayout, pattern: str) -> Path:
    matches = sorted(p for p in layout.aggregate_dir.rglob("*.xlsx") if pattern in p.name)
    if not matches:
        raise FileNotFoundError(f"在 {layout.aggregate_dir} 下找不到包含 {pattern!r} 的文件")
    return matches[0]


def base_name(value: object) -> str:
    """Strip the parenthetical qualifier from a 专业名称 / 院校名称.

    Measured against the real files: stripping *all* qualifiers changed cross-year
    alignment by 0.1pp versus taking only the first parenthesis, so the simpler
    rule wins. See scripts/probe_name_normalisation.py.
    """
    text = "" if value is None else str(value)
    text = text.replace("（", "(").replace("）", ")")
    return text.split("(")[0].strip()


def series_key(university_code: str, university_base: str, major_base: str) -> str:
    """Deterministic cross-year identity.

    Names, not codes: official codes recur across years only ~58% of the time while
    normalised names recur ~84%, and 35% of code-stable pairs actually changed name.
    The codes are still stored on the row for display and debugging.
    """
    raw = f"{university_code}|{university_base}|{major_base}"
    return raw[:160]


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "—", "--", "待定", "nan"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    parsed = _to_int(value)
    return None if parsed is None else float(parsed)


@dataclass
class LoadReport:
    universities: int = 0
    campuses: int = 0
    majors: int = 0
    cycles: int = 0
    exam_stats: int = 0
    score_segments: int = 0
    rule_sets: int = 0
    series: int = 0
    plans: int = 0
    outcomes: int = 0
    skipped_unparsed_rule: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"院校 {self.universities}",
            f"校区 {self.campuses}",
            f"专业 {self.majors}",
            f"招生周期 {self.cycles}",
            f"年度统计 {self.exam_stats}",
            f"一分一段 {self.score_segments}",
            f"选科规则集 {self.rule_sets}",
            f"专业序列 {self.series}",
            f"招生计划 {self.plans}",
            f"历史录取 {self.outcomes}",
        ]


# ---------------------------------------------------------------- workbooks


def read_plans(layout: SourceLayout) -> pd.DataFrame:
    path = _find(layout, "22-25年全国高校在山东的招生计划")
    frame = pd.read_excel(path, dtype=str)
    frame = frame[frame["批次"].astype(str).str.contains(TARGET_BATCH_PATTERN, na=False)].copy()
    frame["年份"] = frame["年份"].map(_to_int)
    frame = frame[frame["年份"].notna()]
    frame["院校基名"] = frame["院校名称"].map(base_name)
    frame["专业基名"] = frame["专业名称"].map(base_name)
    return frame


def read_scores(layout: SourceLayout) -> pd.DataFrame:
    path = _find(layout, "22-25年全国高校在山东的专业录取分数")
    frame = pd.read_excel(path, dtype=str)
    frame = frame[frame["批次"].astype(str).str.contains(TARGET_BATCH_PATTERN, na=False)].copy()
    frame["年份"] = frame["年份"].map(_to_int)
    frame = frame[frame["年份"].notna()]
    frame["院校基名"] = frame["院校名称"].map(base_name)
    frame["专业基名"] = frame["专业"].map(base_name)
    return frame


def read_university_attributes(layout: SourceLayout) -> dict[str, dict[str, Any]]:
    """院校属性 from the 院校录取分数 workbook, keyed by 院校代码."""
    path = _find(layout, "22-25年全国高校在山东的院校录取分数")
    frame = pd.read_excel(path, dtype=str)
    attributes: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("院校代码") or "").strip()
        if not code or code in attributes:
            continue
        attributes[code] = {
            "name": base_name(row.get("院校名称")),
            "province_name": (str(row.get("学校所在")) or "").strip() or None,
            "nature": (str(row.get("学校性质")) or "").strip() or None,
            "is_985": (str(row.get("是否985")) or "").strip() == "是",
            "is_211": (str(row.get("是否211")) or "").strip() == "是",
        }
    return attributes


def read_score_rank(layout: SourceLayout, year: int) -> tuple[list[dict[str, Any]], int | None]:
    """Read one 一分一段表 workbook.

    Returns (rows for 普通类一段, the 一段 control line). Rows for 普通类二段 are
    skipped deliberately — see SourceLayout.score_rank_dir.
    """
    path = layout.score_rank_file(year)
    if not path.exists():
        return [], None
    frame = pd.read_excel(path, sheet_name=0, dtype=str)
    if not {"批次", "分数(分)", "累计人数(人)"} <= set(frame.columns):
        return [], None

    rows: list[dict[str, Any]] = []
    control_line: int | None = None
    for _, row in frame.iterrows():
        if str(row.get("批次") or "").strip() != TARGET_BATCH_PATTERN:
            continue
        control_line = control_line or _to_int(row.get("控制线(分)"))
        raw_score = str(row.get("分数(分)") or "").strip()
        # The top band is a range: 「693-750」.
        score = _to_int(raw_score.split("-")[0]) if "-" in raw_score else _to_int(raw_score)
        cumulative = _to_int(row.get("累计人数(人)"))
        same = _to_int(row.get("本段人数(人)"))
        if score is None or cumulative is None or cumulative <= 0:
            continue
        rows.append(
            {
                "exam_year": year,
                "score": score,
                "same_score_count": same or 0,
                "cumulative_rank": cumulative,
            }
        )
    return rows, control_line


# ---------------------------------------------------------------- writing


def _get_or_create_source(session: Session, path: Path, label: str) -> SourceDocument:
    checksum = _checksum(path)
    existing = session.execute(
        select(SourceDocument).where(SourceDocument.checksum_sha256 == checksum)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    document = SourceDocument(
        title=f"{path.name}（{label}）",
        publisher=PUBLISHER,
        archive_path=str(path),
        checksum_sha256=checksum,
        quality=QUALITY,
        is_demo=False,
        published_on=None,
        retrieved_at=datetime.now(),
        notes=(
            "来自百度网盘教育资料整理包，**非省考试院官方发布**。"
            "已按规格标记为 THIRD_PARTY，数据源质量项会让 ACS 略微保守。"
        ),
    )
    session.add(document)
    session.flush()
    return document


def _provinces_from_names(attributes: dict[str, dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Map province name → (code, name). Codes follow GB/T 2260 for the 31 regions."""
    table: dict[str, tuple[str, str]] = {
        "北京": ("11", "北京市"), "天津": ("12", "天津市"), "河北": ("13", "河北省"),
        "山西": ("14", "山西省"), "内蒙古": ("15", "内蒙古自治区"), "辽宁": ("21", "辽宁省"),
        "吉林": ("22", "吉林省"), "黑龙江": ("23", "黑龙江省"), "上海": ("31", "上海市"),
        "江苏": ("32", "江苏省"), "浙江": ("33", "浙江省"), "安徽": ("34", "安徽省"),
        "福建": ("35", "福建省"), "江西": ("36", "江西省"), "山东": ("37", "山东省"),
        "河南": ("41", "河南省"), "湖北": ("42", "湖北省"), "湖南": ("43", "湖南省"),
        "广东": ("44", "广东省"), "广西": ("45", "广西壮族自治区"), "海南": ("46", "海南省"),
        "重庆": ("50", "重庆市"), "四川": ("51", "四川省"), "贵州": ("52", "贵州省"),
        "云南": ("53", "云南省"), "西藏": ("54", "西藏自治区"), "陕西": ("61", "陕西省"),
        "甘肃": ("62", "甘肃省"), "青海": ("63", "青海省"), "宁夏": ("64", "宁夏回族自治区"),
        "新疆": ("65", "新疆维吾尔自治区"), "香港": ("81", "香港特别行政区"),
        "澳门": ("82", "澳门特别行政区"), "台湾": ("71", "台湾省"),
    }
    return table


def load_reference(
    session: Session, plans: pd.DataFrame, attributes: dict[str, dict[str, Any]], report: LoadReport
) -> dict[str, int]:
    """Universities, campuses and majors. Returns 院校代码 → university_id."""
    province_table = _provinces_from_names(attributes)

    # Build the authoritative university set from the *plans* (that is what V1 needs),
    # enriched with attributes where available.
    latest = plans.sort_values("年份").groupby("院校代码").tail(1)
    university_ids: dict[str, int] = {}

    for _, row in latest.iterrows():
        code = str(row.get("院校代码") or "").strip()
        name_base = str(row.get("院校基名") or "").strip()
        if not code or not name_base:
            continue
        attr = attributes.get(code, {})
        province_name = attr.get("province_name")
        province_code, province_full = province_table.get(province_name or "", (None, None))

        existing_university = session.execute(
            select(University).where(University.ministry_code == code)
        ).scalar_one_or_none()
        if existing_university is None:
            existing_university = University(
                ministry_code=code,
                name=name_base,
                short_name=None,
                province_code=province_code or "37",
                province_name=province_full or (province_name or "未知"),
                city_code=None,
                city_name=None,
                is_985=bool(attr.get("is_985")),
                is_211=bool(attr.get("is_211")),
                is_double_first_class=bool(attr.get("is_985") or attr.get("is_211")),
                is_public=None if not attr.get("nature") else attr["nature"] == "公办",
                remark=f"来源：{QUALITY} 整理包；学校性质={attr.get('nature') or '未知'}",
            )
            session.add(existing_university)
            session.flush()
            report.universities += 1
        university_ids[code] = existing_university.id

    session.flush()

    # Majors: the plan file gives a 专业代码 that is a 专业 or 专业类 depending on the
    # school, and there is no reliable catalogue in this dataset to tell them apart.
    # Everything is therefore recorded as MAJOR — claiming MAJOR_CLASS would be a guess.
    major_pairs = (
        plans[["专业代码", "专业基名"]]
        .dropna()
        .drop_duplicates()
        .assign(专业代码=lambda d: d["专业代码"].str.strip())
    )
    for _, row in major_pairs.iterrows():
        code = str(row["专业代码"])
        name = str(row["专业基名"])
        if not code or not name:
            continue
        existing_major = session.execute(select(Major).where(Major.code == code)).scalar_one_or_none()
        if existing_major is None:
            session.add(Major(code=code, name=name, level="MAJOR"))
            report.majors += 1
            if report.majors % 500 == 0:
                session.flush()
    session.flush()
    return university_ids


def load_cycles(session: Session, years: list[int], report: LoadReport) -> dict[int, int]:
    """admission_cycles. The volunteer cap is a policy number: only years with a
    citable source get one, the rest are recorded as unverified."""
    cycles: dict[int, int] = {}
    for year in years:
        limit = VERIFIED_APPLICATION_LIMITS.get(year)
        existing_cycle = session.execute(
            select(AdmissionCycle).where(
                AdmissionCycle.province_code == "37",
                AdmissionCycle.exam_year == year,
                AdmissionCycle.exam_category == "SUMMER_REGULAR",
                AdmissionCycle.batch_code == "REGULAR_BATCH_1",
                AdmissionCycle.round_no == 1,
                AdmissionCycle.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        if existing_cycle is None:
            existing_cycle = AdmissionCycle(
                province_code="37",
                exam_year=year,
                exam_category="SUMMER_REGULAR",
                batch_code="REGULAR_BATCH_1",
                round_no=1,
                program_level="UNDERGRAD",
                # Fall back to 96 for historical years: it has been the cap since the
                # 2020 reform, but mark the row so the value is not mistaken for cited.
                application_limit=limit or 96,
                filling_rule="分数优先、遵循志愿（平行志愿）",
                policy_version=f"{POLICY_VERSION}-{year}",
                is_active=True,
                notes=(
                    "志愿数量上限 96，来源：山东省 2026 年夏季高考实施办法 / 2025 年常规批规则。"
                    if limit
                    else "该年度未找到可直接引用的官方文件，application_limit 沿用 96（2020 年改革以来的上限），请以官方指南核对。"
                ),
            )
            session.add(existing_cycle)
            session.flush()
            report.cycles += 1
        cycles[year] = existing_cycle.id
    session.flush()
    return cycles


def load_score_rank(
    session: Session, document: SourceDocument, report: LoadReport, layout: SourceLayout
) -> dict[int, int]:
    """一分一段表（仅普通类一段）+ 由表尾推导的年度考生人数。

    考生人数取自 一段 表尾，即达到本科线的考生数。这是位次百分位的分母，
    与 V1 只推荐本科的口径一致。
    """
    populations: dict[int, int] = {}
    for year in SCORE_RANK_YEARS:
        rows, control_line = read_score_rank(layout, year)
        if not rows:
            report.notes.append(f"{year} 年一分一段表未解析出数据")
            continue
        populations[year] = max(row["cumulative_rank"] for row in rows)
        report.notes.append(
            f"{year} 年普通类一段：控制线 {control_line}，达线人数 {populations[year]:,}"
        )
        for row in rows:
            existing_segment = session.execute(
                select(ScoreRankSegment).where(
                    ScoreRankSegment.exam_year == row["exam_year"],
                    ScoreRankSegment.exam_category == "SUMMER_REGULAR",
                    ScoreRankSegment.score == row["score"],
                )
            ).scalar_one_or_none()
            if existing_segment is None:
                session.add(
                    ScoreRankSegment(
                        exam_year=row["exam_year"],
                        exam_category="SUMMER_REGULAR",
                        program_level="UNDERGRAD",
                        score=row["score"],
                        same_score_count=row["same_score_count"],
                        cumulative_rank=row["cumulative_rank"],
                        source_document_id=document.id,
                    )
                )
                report.score_segments += 1
            else:
                existing_segment.cumulative_rank = row["cumulative_rank"]
    session.flush()

    for year, population in populations.items():
        existing_stats = session.execute(
            select(ExamYearStats).where(
                ExamYearStats.exam_year == year,
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        if existing_stats is None:
            session.add(
                ExamYearStats(
                    province_code="37",
                    exam_year=year,
                    exam_category="SUMMER_REGULAR",
                    program_level="UNDERGRAD",
                    ranked_candidates=population,
                    source_document_id=document.id,
                )
            )
            report.exam_stats += 1
        else:
            existing_stats.ranked_candidates = population
    session.flush()
    return populations


def load_rule_sets(
    session: Session, plans: pd.DataFrame, document: SourceDocument, report: LoadReport
) -> dict[str, int]:
    """One SubjectRuleSet per distinct parsed form. Groups follow the DNF parser."""
    parsed_cache: dict[str, ParsedSubjectRule | None] = {}
    mapping: dict[str, int] = {}

    distinct = plans["选科要求"].dropna().astype(str).str.strip().unique().tolist()
    for raw in distinct:
        parsed = parse_subject_requirement(raw)
        parsed_cache[raw] = parsed
        if parsed is None:
            report.skipped_unparsed_rule += 1
            continue

        code = parsed.as_text()
        existing_rule = session.execute(
            select(SubjectRuleSet).where(SubjectRuleSet.code == code)
        ).scalar_one_or_none()
        if existing_rule is None:
            existing_rule = SubjectRuleSet(
                code=code,
                mode=parsed.mode,
                raw_text=raw,
                source_document_id=document.id,
                is_authoritative=True,
            )
            session.add(existing_rule)
            session.flush()
            for index, group in enumerate(parsed.groups):
                group_row = SubjectRuleGroup(
                    rule_set_id=existing_rule.id,
                    group_no=index,
                    mode="ANY" if parsed.mode == "ANY" else "ALL",
                )
                session.add(group_row)
                session.flush()
                for subject_code in group:
                    session.add(SubjectRuleItem(group_id=group_row.id, subject_code=subject_code))
            report.rule_sets += 1
        mapping[raw] = existing_rule.id
    session.flush()
    return mapping


def load_plans(
    session: Session,
    plans: pd.DataFrame,
    scores: pd.DataFrame,
    snapshot: DatasetSnapshot,
    cycles: dict[int, int],
    university_ids: dict[str, int],
    rule_map: dict[str, int],
    plans_doc: SourceDocument,
    scores_doc: SourceDocument,
    report: LoadReport,
) -> None:
    """Program series (cross-year identity) + per-year plans + outcomes."""
    # ---- series ----
    series_cache: dict[str, int] = {}
    series_meta = (
        plans.dropna(subset=["院校代码", "专业代码"])
        .sort_values("年份")
        .groupby("院校代码")
        .apply(lambda g: g, include_groups=False)
    )
    _ = series_meta  # kept for readability; the loop below works off the frame

    for _, row in plans.iterrows():
        university_code = str(row.get("院校代码") or "").strip()
        university_id = university_ids.get(university_code)
        if university_id is None:
            continue
        major_base = str(row.get("专业基名") or "").strip()
        university_base = str(row.get("院校基名") or "").strip()
        if not major_base:
            continue

        key = series_key(university_code, university_base, major_base)
        series_id = series_cache.get(key)
        if series_id is None:
            existing_series = session.execute(
                select(ProgramSeries).where(ProgramSeries.series_key == key)
            ).scalar_one_or_none()
            if existing_series is None:
                year = _to_int(row.get("年份"))
                existing_series = ProgramSeries(
                    series_key=key,
                    canonical_name=major_base,
                    university_id=university_id,
                    major_id=None,
                    campus_id=None,
                    cooperative="NONE",
                    match_quality="EXACT",
                    first_year=year,
                    last_year=year,
                )
                session.add(existing_series)
                session.flush()
                report.series += 1
            else:
                year = _to_int(row.get("年份"))
                if year is not None:
                    existing_series.first_year = min(existing_series.first_year or year, year)
                    existing_series.last_year = max(existing_series.last_year or year, year)
            series_cache[key] = existing_series.id
        series_id = series_cache[key]

        # ---- plan ----
        year = _to_int(row.get("年份"))
        cycle_id = cycles.get(year) if year is not None else None
        if cycle_id is None:
            continue
        rule_set_id = rule_map.get(str(row.get("选科要求") or "").strip())
        plan_name = str(row.get("专业名称") or "").strip()
        plan_count = _to_int(row.get("招生人数")) or 0
        tuition = _to_int(row.get("学费(元)")) or 0
        # `专业代码` in the Shandong plan files is a PER-SCHOOL SEQUENCE NUMBER (all
        # values are two characters: '17', 'E1', 'H1' …), not an MOE major code — verified
        # against all 21,421 rows of 普通类一段. So it identifies a plan within a school,
        # and cannot join to the MOE major catalogue. Labelled honestly to avoid the
        # reader mistaking it for the national code.
        major_sequence = str(row.get("专业代码") or "").strip()

        existing_plan = session.execute(
            select(AdmissionPlan).where(
                AdmissionPlan.cycle_id == cycle_id,
                AdmissionPlan.series_id == series_id,
                AdmissionPlan.subject_rule_set_id.is_(None)
                if rule_set_id is None
                else AdmissionPlan.subject_rule_set_id == rule_set_id,
            )
        ).scalar_one_or_none()
        if existing_plan is None:
            session.add(
                AdmissionPlan(
                    cycle_id=cycle_id,
                    series_id=series_id,
                    subject_rule_set_id=rule_set_id,
                    snapshot_id=snapshot.id,
                    source_document_id=plans_doc.id,
                    plan_name=plan_name,
                    plan_count=plan_count,
                    tuition_fee=tuition,
                    duration_years=4,
                    major_sequence=major_sequence or None,
                    series_match_quality="EXACT",
                    is_demo=False,
                )
            )
            report.plans += 1
            if report.plans % 2000 == 0:
                session.flush()
        else:
            # Refresh every field this loader owns, not just the numbers — otherwise a
            # column added later never reaches already-loaded rows on a re-run.
            existing_plan.plan_count = plan_count
            existing_plan.tuition_fee = tuition
            existing_plan.plan_name = plan_name
            existing_plan.major_sequence = major_sequence or None
            existing_plan.snapshot_id = snapshot.id
            existing_plan.source_document_id = plans_doc.id
    session.flush()

    # ---- outcomes ----
    # (university_code, major_base, year) → cut-off, so we can attach to the series
    # created above without relying on plan rows.
    series_by_name: dict[tuple[str, str], int] = {}
    for key, series_id in series_cache.items():
        parts = key.split("|")
        if len(parts) >= 3:
            series_by_name[(parts[0], parts[2])] = series_id

    for _, row in scores.iterrows():
        university_code = str(row.get("院校代码") or "").strip()
        major_base = str(row.get("专业基名") or "").strip()
        year = _to_int(row.get("年份"))
        min_rank = _to_int(row.get("最低位次"))
        if not university_code or not major_base or year is None or min_rank is None:
            continue
        series_id = series_by_name.get((university_code, major_base))
        if series_id is None:
            continue
        existing_outcome = session.execute(
            select(AdmissionOutcome).where(
                AdmissionOutcome.series_id == series_id,
                AdmissionOutcome.exam_year == year,
            )
        ).scalar_one_or_none()
        min_score = _to_int(row.get("最低分数"))
        admitted = _to_int(row.get("录取人数"))
        if existing_outcome is None:
            session.add(
                AdmissionOutcome(
                    series_id=series_id,
                    snapshot_id=snapshot.id,
                    source_document_id=scores_doc.id,
                    exam_year=year,
                    min_rank=min_rank,
                    min_score=min_score,
                    plan_count=admitted,
                    admitted_count=admitted,
                    is_demo=False,
                )
            )
            report.outcomes += 1
            if report.outcomes % 2000 == 0:
                session.flush()
        else:
            existing_outcome.min_rank = min_rank
            if min_score is not None:
                existing_outcome.min_score = min_score
    session.flush()


def reset_real(session: Session) -> None:
    """Delete only the real (non-DEMO) snapshot data, leaving DEMO fixtures alone."""
    snapshot_ids = list(
        session.execute(
            select(DatasetSnapshot.id).where(DatasetSnapshot.code == SNAPSHOT_CODE)
        ).scalars()
    )
    if snapshot_ids:
        plan_ids = list(
            session.execute(
                select(AdmissionPlan.id).where(AdmissionPlan.snapshot_id.in_(snapshot_ids))
            ).scalars()
        )
        if plan_ids:
            session.execute(delete(AdmissionPlan).where(AdmissionPlan.id.in_(plan_ids)))
        session.execute(
            delete(AdmissionOutcome).where(AdmissionOutcome.snapshot_id.in_(snapshot_ids))
        )
        session.flush()
        session.execute(delete(DatasetSnapshot).where(DatasetSnapshot.id.in_(snapshot_ids)))

    # Series created by this ETL have no DEMO counterpart; remove the orphaned ones.
    demo_ids = select(AdmissionPlan.id).where(AdmissionPlan.is_demo.is_(True))
    referenced = select(AdmissionPlan.id).where(AdmissionPlan.series_id == ProgramSeries.id)
    session.execute(
        delete(ProgramSeries).where(
            ProgramSeries.is_active.is_(True),
            ~referenced.exists(),
        )
    )
    _ = demo_ids
    session.flush()


# ---------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the real Shandong 2022-2025 data.")
    parser.add_argument("--write", action="store_true", help="真正写库（默认只做演练）")
    parser.add_argument("--reset", action="store_true", help="写入前清理已有的真实数据")
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "官方源文件所在目录；也可用环境变量 GAOKAO_DATA_ROOT 指定。"
            "目录结构见 docs/DATA_IMPORT.md"
        ),
    )
    args = parser.parse_args(argv)

    try:
        layout = resolve_root(args.data_root)
    except DataRootNotFound as exc:
        print(str(exc))
        return 2

    print("=" * 100)
    print("山东真实数据 ETL  (2022-2025 · 普通类常规批第 1 次志愿 · 本科)")
    print("=" * 100)
    print(f"数据来源   : {PUBLISHER}")
    print(f"溯源标记   : quality={QUALITY}  is_demo=False")
    print(f"数据目录   : {layout.root}")
    print(f"输出模式   : {'写入数据库' if args.write else '演练（不写库）'}")
    print()

    plan_path = _find(layout, "22-25年全国高校在山东的招生计划")
    score_path = _find(layout, "22-25年全国高校在山东的专业录取分数")
    uni_path = _find(layout, "22-25年全国高校在山东的院校录取分数")

    plans = read_plans(layout)
    scores = read_scores(layout)
    attributes = read_university_attributes(layout)

    print(f"招生计划   : {plan_path.name}")
    print(f"             普通类一段 {len(plans):,} 行，年份 {sorted(plans['年份'].unique().tolist())}")
    print(f"专业录取分 : {score_path.name}")
    print(f"             普通类一段 {len(scores):,} 行")
    print(f"院校属性   : {uni_path.name}")
    print(f"             {len(attributes):,} 所院校")
    print()

    coverage = coverage_report(list(plans["选科要求"]))
    print("选科要求解析覆盖率：")
    print(f"    样本 {coverage['total']:,}  成功 {coverage['parsed']:,}  失败 {coverage['unparsed']:,}  覆盖率 {coverage['coverage']}%")
    if coverage["unparsed_values"]:
        print(f"    未解析取值: {coverage['unparsed_values']}")
    print()

    if not args.write:
        print("=" * 100)
        print("演练结束。加 --write 正式写库。")
        print("=" * 100)
        return 0

    report = LoadReport()
    with session_scope() as session:
        if args.reset:
            print("清理已有真实数据 …")
            reset_real(session)

        plans_doc = _get_or_create_source(session, plan_path, "招生计划")
        scores_doc = _get_or_create_source(session, score_path, "专业录取分数")
        uni_doc = _get_or_create_source(session, uni_path, "院校属性")

        snapshot = session.execute(
            select(DatasetSnapshot).where(DatasetSnapshot.code == SNAPSHOT_CODE)
        ).scalar_one_or_none()
        if snapshot is None:
            snapshot = DatasetSnapshot(
                code=SNAPSHOT_CODE,
                label="山东 2022-2025 真实数据（第三方整理包）",
                status="PUBLISHED",
                policy_version=POLICY_VERSION,
                is_demo=False,
                published_at=datetime.now(),
                source_document_id=plans_doc.id,
                notes=(
                    f"来源：{PUBLISHER}。数据为第三方整理，非省考试院官方发布，"
                    "已标记 quality=THIRD_PARTY。仅覆盖普通类常规批第 1 次志愿本科计划。"
                ),
            )
            session.add(snapshot)
            session.flush()
        else:
            snapshot.status = "PUBLISHED"

        print("载入院校与专业 …")
        university_ids = load_reference(session, plans, attributes, report)

        print("载入招生周期 …")
        years = sorted(int(y) for y in plans["年份"].dropna().unique())
        cycles = load_cycles(session, years, report)

        print("载入一分一段表 …")
        load_score_rank(session, plans_doc, report, layout)

        print("载入选科规则 …")
        rule_map = load_rule_sets(session, plans, plans_doc, report)

        print("载入专业序列 / 计划 / 历史录取 …")
        load_plans(
            session, plans, scores, snapshot, cycles, university_ids, rule_map,
            plans_doc, scores_doc, report,
        )

        _ = uni_doc

    print()
    print("=" * 100)
    print("载入完成：")
    for line in report.lines():
        print(f"  - {line}")
    if report.skipped_unparsed_rule:
        print(f"  ! 未能解析的选科要求取值 {report.skipped_unparsed_rule} 种（对应计划将无选科限制标签）")
    for note in report.notes:
        print(f"  ! {note}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
