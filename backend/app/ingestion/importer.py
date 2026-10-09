"""Official-data import interface.

This is the seam where **real** data enters the system. The DEMO fixtures use the
same tables and the same column shapes, so switching over means importing files —
no pipeline changes.

Design rules (spec §10):

1. Nothing is written unless validation passes. `dry_run=True` gives the full
   report without touching the database.
2. Every imported fact is linked to a `source_documents` row identified by the
   file's SHA-256, so provenance survives re-imports (re-uploading the same file
   updates instead of duplicating).
3. Values that cannot be resolved deterministically are reported as UNKNOWN and
   the import refuses rather than guessing — a wrong cut-off rank is worse than a
   missing one.
4. Importers are idempotent on their natural key (year+score, series+year,
   cycle+series+rule), so a corrected file can be re-imported safely.

Supported CSV shapes are documented in docs/DATA_IMPORT.md and can be dumped with
`GET /api/v1/ingestion/templates`.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
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
)
from app.common.errors import NotFoundError, RuleViolationError
from app.reference.models import University

# The quality tag a real, official file must carry. DEMO is deliberately excluded:
# this endpoint is for official data.
OFFICIAL_QUALITY = "OFFICIAL"


class ImportKind(StrEnum):
    SCORE_RANK = "score-rank"
    OUTCOMES = "outcomes"
    PLANS = "plans"


# ---------------------------------------------------------------- reporting


@dataclass(slots=True)
class ImportIssue:
    row: int | None
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "code": self.code, "message": self.message, "detail": self.detail}


@dataclass(slots=True)
class ImportReport:
    kind: str
    source_document_id: int | None = None
    checksum: str = ""
    rows_total: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    created: int = 0
    updated: int = 0
    dry_run: bool = False
    errors: list[ImportIssue] = field(default_factory=list)
    warnings: list[ImportIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, row: int | None, code: str, message: str, **detail: Any) -> None:
        self.errors.append(ImportIssue(row, code, message, detail))
        self.rows_rejected += 1

    def warn(self, row: int | None, code: str, message: str, **detail: Any) -> None:
        self.warnings.append(ImportIssue(row, code, message, detail))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "checksum": self.checksum,
            "source_document_id": self.source_document_id,
            "rows_total": self.rows_total,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "created": self.created,
            "updated": self.updated,
            # A bounded sample keeps the response readable on a 30k-row file.
            "errors": [issue.as_dict() for issue in self.errors[:50]],
            "error_count": len(self.errors),
            "warnings": [issue.as_dict() for issue in self.warnings[:50]],
            "warning_count": len(self.warnings),
        }


# ---------------------------------------------------------------- parsing


def _to_int(raw: str | None, *, field_name: str, row: int, report: ImportReport) -> int | None:
    if raw is None or raw.strip() == "":
        report.error(row, "MISSING_VALUE", f"缺少必填字段 {field_name}")
        return None
    cleaned = raw.strip().replace(",", "").replace("，", "")
    try:
        return int(float(cleaned))
    except ValueError:
        report.error(
            row, "NOT_A_NUMBER", f"{field_name} 不是数字: {raw!r}", field=field_name, raw=raw
        )
        return None


def _optional_int(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    cleaned = raw.strip().replace(",", "").replace("，", "")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _read_csv(content: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Decode a CSV into (fieldnames, [(1-based row number, row)]).

    Tries UTF-8 with BOM first (what Excel and most official exports produce),
    then GBK, which older Shandong files use.
    """
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuleViolationError("无法识别文件编码，请另存为 UTF-8 或 GBK 编码的 CSV")

    # Sniff the delimiter: official exports are comma- or tab-separated.
    sample = text[:4096]
    # `sniff` returns a Dialect *instance*; the except branch falls back to the
    # excel dialect *class*, which DictReader accepts just as well.
    dialect: Any
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise RuleViolationError("CSV 没有表头行，无法识别列名")
    fieldnames = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
    rows: list[tuple[int, dict[str, str]]] = []
    for index, raw_row in enumerate(reader, start=2):  # row 1 is the header
        normalised = {
            (key or "").strip().lstrip("\ufeff"): (value or "").strip()
            for key, value in raw_row.items()
        }
        if not any(normalised.values()):
            continue  # skip blank lines rather than reporting them as errors
        rows.append((index, normalised))
    return fieldnames, rows


def _require_columns(fieldnames: list[str], required: dict[str, tuple[str, ...]]) -> None:
    """`required` maps a logical name to acceptable header aliases."""
    missing = [
        logical
        for logical, aliases in required.items()
        if not any(alias in fieldnames for alias in aliases)
    ]
    if missing:
        raise RuleViolationError(
            "CSV 缺少必需的列：" + "、".join(missing),
            details={
                "missing": missing,
                "required": {k: list(v) for k, v in required.items()},
                "found": fieldnames,
            },
        )


def _pick(row: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in row and row[alias] != "":
            return row[alias]
    return None


# Header aliases: Chinese official column names first, then common English ones.
# REQUIRED_COLUMNS must be present or the file is rejected outright; OPTIONAL_COLUMNS
# are used when present and simply skipped when absent.
REQUIRED_COLUMNS: dict[ImportKind, dict[str, tuple[str, ...]]] = {
    ImportKind.SCORE_RANK: {
        "exam_year": ("exam_year", "年份", "年度"),
        "score": ("score", "分数", "成绩"),
        "same_score_count": ("same_score_count", "本段人数", "同分人数"),
        "cumulative_rank": ("cumulative_rank", "累计人数", "累计位次", "位次"),
    },
    ImportKind.OUTCOMES: {
        "university_name": ("university_name", "院校名称", "学校名称", "院校"),
        "plan_name": ("plan_name", "专业名称", "专业", "专业（专业类）"),
        "exam_year": ("exam_year", "年份", "年度"),
        "min_rank": ("min_rank", "最低位次", "投档最低位次"),
    },
    ImportKind.PLANS: {
        "university_name": ("university_name", "院校名称", "学校名称", "院校"),
        "plan_name": ("plan_name", "专业名称", "专业", "专业（专业类）"),
        "plan_count": ("plan_count", "计划数", "招生计划", "计划人数"),
    },
}

OPTIONAL_COLUMNS: dict[ImportKind, dict[str, tuple[str, ...]]] = {
    ImportKind.SCORE_RANK: {},
    ImportKind.OUTCOMES: {
        "min_score": ("min_score", "最低分", "投档最低分"),
        "plan_count": ("plan_count", "计划数", "招生计划"),
        "admitted_count": ("admitted_count", "录取人数"),
    },
    ImportKind.PLANS: {
        "tuition_fee": ("tuition_fee", "学费", "学费(元/年)", "收费标准"),
        "subject_requirement": ("subject_requirement", "选科要求", "选考科目要求"),
        "duration_years": ("duration_years", "学制"),
        "campus_name": ("campus_name", "校区", "办学地点"),
        "special_requirements": ("special_requirements", "特殊要求", "备注"),
    },
}

# Backwards-compatible alias used by the templates/docs.
COLUMNS = REQUIRED_COLUMNS

TEMPLATES: dict[str, str] = {
    ImportKind.SCORE_RANK.value: (
        "exam_year,score,same_score_count,cumulative_rank\n2025,700,3,3\n2025,699,5,8\n"
    ),
    ImportKind.OUTCOMES.value: (
        "university_name,plan_name,exam_year,min_rank\n演示大学,计算机类,2025,12000\n"
    ),
    ImportKind.PLANS.value: (
        "university_name,plan_name,plan_count,tuition_fee,subject_requirement\n"
        "演示大学,计算机类,45,5800,物理和化学\n"
    ),
}


# ---------------------------------------------------------------- validation


def validate_score_rank(content: bytes, *, report: ImportReport) -> list[dict[str, Any]]:
    fieldnames, rows = _read_csv(content)
    _require_columns(fieldnames, REQUIRED_COLUMNS[ImportKind.SCORE_RANK])
    report.rows_total = len(rows)

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for row_number, row in rows:
        exam_year = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.SCORE_RANK]["exam_year"]),
            field_name="exam_year", row=row_number, report=report,
        )
        score = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.SCORE_RANK]["score"]),
            field_name="score", row=row_number, report=report,
        )
        same = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.SCORE_RANK]["same_score_count"]),
            field_name="same_score_count", row=row_number, report=report,
        )
        cumulative = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.SCORE_RANK]["cumulative_rank"]),
            field_name="cumulative_rank", row=row_number, report=report,
        )
        if exam_year is None or score is None or same is None or cumulative is None:
            continue
        if not 0 <= score <= 750:
            report.error(row_number, "SCORE_OUT_OF_RANGE", f"分数 {score} 超出 0~750", score=score)
            continue
        if same < 0 or cumulative <= 0:
            report.error(
                row_number, "INVALID_COUNTS",
                "本段人数必须 >= 0 且累计人数必须 > 0",
                same_score_count=same, cumulative_rank=cumulative,
            )
            continue
        key = (exam_year, score)
        if key in seen:
            report.error(
                row_number, "DUPLICATE_KEY",
                f"{exam_year} 年 {score} 分重复出现", exam_year=exam_year, score=score,
            )
            continue
        seen.add(key)
        parsed.append(
            {
                "exam_year": exam_year,
                "score": score,
                "same_score_count": same,
                "cumulative_rank": cumulative,
            }
        )
    report.rows_accepted = len(parsed)
    return parsed


def validate_plans(
    content: bytes, *, session: Session, report: ImportReport, cycle: AdmissionCycle
) -> list[dict[str, Any]]:
    fieldnames, rows = _read_csv(content)
    _require_columns(fieldnames, REQUIRED_COLUMNS[ImportKind.PLANS])
    report.rows_total = len(rows)

    universities = {
        name: (uid, code, province_code, province_name)
        for name, uid, code, province_code, province_name in session.execute(
            select(
                University.name,
                University.id,
                University.ministry_code,
                University.province_code,
                University.province_name,
            )
        ).all()
    }

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    unknown_subject_rule = 0

    for row_number, row in rows:
        university_name = _pick(row, REQUIRED_COLUMNS[ImportKind.PLANS]["university_name"])
        plan_name = _pick(row, REQUIRED_COLUMNS[ImportKind.PLANS]["plan_name"])
        if not university_name or not plan_name:
            report.error(row_number, "MISSING_VALUE", "院校名称与专业名称为必填项")
            continue

        match = universities.get(university_name)
        if match is None:
            # Never invent a university: it must come from the reference catalogue.
            report.error(
                row_number, "UNKNOWN_UNIVERSITY",
                f"院校「{university_name}」不在院校库中，请先导入院校基础数据",
                university_name=university_name,
            )
            continue

        key = (university_name, plan_name)
        if key in seen:
            report.error(
                row_number, "DUPLICATE_PLAN",
                f"同一院校同一专业重复出现：{university_name} / {plan_name}",
            )
            continue
        seen.add(key)

        plan_count = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.PLANS]["plan_count"]),
            field_name="plan_count", row=row_number, report=report,
        )
        tuition = _optional_int(_pick(row, OPTIONAL_COLUMNS[ImportKind.PLANS]["tuition_fee"]))
        if plan_count is None:
            continue
        if plan_count < 0:
            report.error(row_number, "INVALID_PLAN_COUNT", "计划数不能为负", plan_count=plan_count)
            continue
        if tuition is None:
            report.warn(row_number, "MISSING_TUITION", "缺少学费，将按 0 记录并需要人工补齐")
            tuition = 0

        subject_text = _pick(row, OPTIONAL_COLUMNS[ImportKind.PLANS]["subject_requirement"]) or ""
        parsed.append(
            {
                "row": row_number,
                "university_id": match[0],
                "university_code": match[1],
                "province_code": match[2],
                "province_name": match[3],
                "university_name": university_name,
                "plan_name": plan_name,
                "plan_count": plan_count,
                "tuition_fee": tuition,
                "subject_requirement_raw": subject_text,
            }
        )
        if not subject_text:
            unknown_subject_rule += 1

    if unknown_subject_rule:
        report.warn(
            None, "SUBJECT_REQUIREMENT_MISSING",
            f"{unknown_subject_rule} 条计划没有选科要求文本，"
            "这些计划将不带选科限制；若官方文件确有该列，请检查列名是否匹配",
            affected=unknown_subject_rule,
        )

    report.rows_accepted = len(parsed)
    return parsed


def validate_outcomes(
    content: bytes, *, session: Session, report: ImportReport
) -> list[dict[str, Any]]:
    fieldnames, rows = _read_csv(content)
    _require_columns(fieldnames, REQUIRED_COLUMNS[ImportKind.OUTCOMES])
    report.rows_total = len(rows)

    # Resolve (university_name, plan_name) -> series_id using what is already in
    # the database. An outcome whose series is missing is an error, not a guess.
    series_map: dict[tuple[str, str], int] = {}
    for university_name, plan_name, series_id in session.execute(
        select(University.name, ProgramSeries.canonical_name, ProgramSeries.id).join(
            ProgramSeries, ProgramSeries.university_id == University.id
        )
    ).all():
        series_map[(university_name, plan_name)] = series_id

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    for row_number, row in rows:
        university_name = _pick(row, REQUIRED_COLUMNS[ImportKind.OUTCOMES]["university_name"])
        plan_name = _pick(row, REQUIRED_COLUMNS[ImportKind.OUTCOMES]["plan_name"])
        if not university_name or not plan_name:
            report.error(row_number, "MISSING_VALUE", "院校名称与专业名称为必填项")
            continue

        series_id = series_map.get((university_name, plan_name))
        if series_id is None:
            report.error(
                row_number, "UNKNOWN_SERIES",
                f"找不到对应的招生计划：{university_name} / {plan_name}，请先导入招生计划",
            )
            continue

        exam_year = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.OUTCOMES]["exam_year"]),
            field_name="exam_year", row=row_number, report=report,
        )
        min_rank = _to_int(
            _pick(row, REQUIRED_COLUMNS[ImportKind.OUTCOMES]["min_rank"]),
            field_name="min_rank", row=row_number, report=report,
        )
        if exam_year is None or min_rank is None:
            continue
        if min_rank <= 0:
            report.error(row_number, "INVALID_MIN_RANK", "最低位次必须为正整数", min_rank=min_rank)
            continue

        key = (series_id, exam_year)
        if key in seen:
            report.error(
                row_number, "DUPLICATE_KEY",
                f"同一专业序列 {exam_year} 年重复出现",
                series_id=series_id, exam_year=exam_year,
            )
            continue
        seen.add(key)
        parsed.append(
            {
                "series_id": series_id,
                "exam_year": exam_year,
                "min_rank": min_rank,
                # Optional columns are accepted when present.
                "min_score": _optional_int(_pick(row, ("min_score", "最低分", "投档最低分"))),
                "plan_count": _optional_int(_pick(row, ("plan_count", "计划数", "招生计划"))),
                "admitted_count": _optional_int(_pick(row, ("admitted_count", "录取人数"))),
            }
        )
    report.rows_accepted = len(parsed)
    return parsed


# ---------------------------------------------------------------- writing


def _source_document(
    session: Session,
    *,
    content: bytes,
    kind: str,
    filename: str,
    publisher: str | None,
    url: str | None,
    published_on: date | None,
) -> SourceDocument:
    """Find-or-create the provenance row, keyed by the file checksum."""
    checksum = hashlib.sha256(content).hexdigest()
    existing = session.execute(
        select(SourceDocument).where(SourceDocument.checksum_sha256 == checksum)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    document = SourceDocument(
        title=f"{filename}（{kind}）",
        publisher=publisher,
        url=url,
        archive_path=f"data/raw/{filename}",
        checksum_sha256=checksum,
        quality=OFFICIAL_QUALITY,
        is_demo=False,
        published_on=published_on,
        retrieved_at=datetime.now(),
        notes=f"通过 /api/v1/ingestion/{kind} 导入",
    )
    session.add(document)
    session.flush()
    return document


def _write_score_rank(
    session: Session, rows: list[dict[str, Any]], document: SourceDocument, report: ImportReport
) -> None:
    for row in rows:
        existing = session.execute(
            select(ScoreRankSegment).where(
                ScoreRankSegment.exam_year == row["exam_year"],
                ScoreRankSegment.exam_category == "SUMMER_REGULAR",
                ScoreRankSegment.score == row["score"],
            )
        ).scalar_one_or_none()
        if existing is None:
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
            report.created += 1
        else:
            existing.same_score_count = row["same_score_count"]
            existing.cumulative_rank = row["cumulative_rank"]
            existing.source_document_id = document.id
            report.updated += 1

    # The yearly candidate population is derived from the table's last row, which is
    # how the official 一分一段表 defines it.
    by_year: dict[int, int] = {}
    for row in rows:
        by_year[row["exam_year"]] = max(by_year.get(row["exam_year"], 0), row["cumulative_rank"])
    for exam_year, ranked in by_year.items():
        stats = session.execute(
            select(ExamYearStats).where(
                ExamYearStats.exam_year == exam_year,
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        if stats is None:
            session.add(
                ExamYearStats(
                    province_code="37",
                    exam_year=exam_year,
                    exam_category="SUMMER_REGULAR",
                    program_level="UNDERGRAD",
                    ranked_candidates=ranked,
                    source_document_id=document.id,
                )
            )
        else:
            stats.ranked_candidates = ranked
    session.flush()


def _write_plans(
    session: Session,
    rows: list[dict[str, Any]],
    document: SourceDocument,
    snapshot: DatasetSnapshot,
    cycle: AdmissionCycle,
    report: ImportReport,
) -> None:
    for row in rows:
        series_key = f"{row['university_code']}|{row['plan_name']}"
        series = session.execute(
            select(ProgramSeries).where(ProgramSeries.series_key == series_key)
        ).scalar_one_or_none()
        if series is None:
            series = ProgramSeries(
                series_key=series_key,
                canonical_name=row["plan_name"],
                university_id=row["university_id"],
                first_year=cycle.exam_year,
                last_year=cycle.exam_year,
            )
            session.add(series)
            session.flush()

        plan = session.execute(
            select(AdmissionPlan).where(
                AdmissionPlan.cycle_id == cycle.id,
                AdmissionPlan.series_id == series.id,
                AdmissionPlan.subject_rule_set_id.is_(None),
            )
        ).scalar_one_or_none()
        if plan is None:
            plan = AdmissionPlan(
                cycle_id=cycle.id,
                series_id=series.id,
                snapshot_id=snapshot.id,
                source_document_id=document.id,
                plan_name=row["plan_name"],
                plan_count=row["plan_count"],
                tuition_fee=row["tuition_fee"],
                subject_rule_set_id=None,
                is_demo=False,
            )
            session.add(plan)
            session.flush()
            report.created += 1
        else:
            plan.plan_count = row["plan_count"]
            plan.tuition_fee = row["tuition_fee"]
            plan.snapshot_id = snapshot.id
            plan.source_document_id = document.id
            report.updated += 1
    session.flush()


def _write_outcomes(
    session: Session,
    rows: list[dict[str, Any]],
    document: SourceDocument,
    snapshot: DatasetSnapshot,
    report: ImportReport,
) -> None:
    for row in rows:
        existing = session.execute(
            select(AdmissionOutcome).where(
                AdmissionOutcome.series_id == row["series_id"],
                AdmissionOutcome.exam_year == row["exam_year"],
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AdmissionOutcome(
                    series_id=row["series_id"],
                    snapshot_id=snapshot.id,
                    source_document_id=document.id,
                    exam_year=row["exam_year"],
                    min_rank=row["min_rank"],
                    min_score=row.get("min_score"),
                    plan_count=row.get("plan_count"),
                    admitted_count=row.get("admitted_count"),
                    is_demo=False,
                )
            )
            report.created += 1
        else:
            existing.min_rank = row["min_rank"]
            if row.get("min_score") is not None:
                existing.min_score = row["min_score"]
            if row.get("plan_count") is not None:
                existing.plan_count = row["plan_count"]
            existing.snapshot_id = snapshot.id
            existing.source_document_id = document.id
            report.updated += 1
    session.flush()


# ---------------------------------------------------------------- service


class IngestionService:
    """Router → Service → Repository → DB. This is the service layer for imports."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def import_file(
        self,
        *,
        kind: ImportKind,
        content: bytes,
        filename: str,
        snapshot_id: int | None,
        cycle_id: int | None,
        publisher: str | None = None,
        url: str | None = None,
        published_on: date | None = None,
        dry_run: bool = True,
    ) -> ImportReport:
        report = ImportReport(kind=kind.value, dry_run=dry_run)
        report.checksum = hashlib.sha256(content).hexdigest()

        if not content:
            report.error(None, "EMPTY_FILE", "文件为空")
            return report

        cycle = self._resolve_cycle(kind, cycle_id)
        snapshot = self._resolve_snapshot(kind, snapshot_id)

        if kind is ImportKind.SCORE_RANK:
            rows = validate_score_rank(content, report=report)
        elif kind is ImportKind.PLANS:
            assert cycle is not None
            rows = validate_plans(content, session=self.session, report=report, cycle=cycle)
        else:
            rows = validate_outcomes(content, session=self.session, report=report)

        if not report.ok or dry_run or not rows:
            if dry_run and report.ok:
                report.warn(None, "DRY_RUN", "校验通过；未写入数据库（dry_run=true）")
            return report

        document = _source_document(
            self.session,
            content=content,
            kind=kind.value,
            filename=filename,
            publisher=publisher,
            url=url,
            published_on=published_on,
        )
        report.source_document_id = document.id

        if kind is ImportKind.SCORE_RANK:
            _write_score_rank(self.session, rows, document, report)
        elif kind is ImportKind.PLANS:
            assert cycle is not None and snapshot is not None
            _write_plans(self.session, rows, document, snapshot, cycle, report)
        else:
            assert snapshot is not None
            _write_outcomes(self.session, rows, document, snapshot, report)

        self.session.flush()
        return report

    # ---------------- helpers ----------------

    def _resolve_cycle(self, kind: ImportKind, cycle_id: int | None) -> AdmissionCycle | None:
        if kind is ImportKind.SCORE_RANK:
            return None  # the 一分一段表 spans many years and needs no cycle
        if cycle_id is not None:
            cycle = self.session.get(AdmissionCycle, cycle_id)
            if cycle is None:
                raise NotFoundError(f"招生周期 {cycle_id} 不存在")
            return cycle
        cycle = self.session.execute(
            select(AdmissionCycle)
            .where(AdmissionCycle.is_active.is_(True))
            .order_by(AdmissionCycle.exam_year.desc())
            .limit(1)
        ).scalar_one_or_none()
        if cycle is None:
            raise RuleViolationError("没有可用的招生周期，请先创建 admission_cycles 记录")
        return cycle

    def _resolve_snapshot(self, kind: ImportKind, snapshot_id: int | None) -> DatasetSnapshot | None:
        if kind is ImportKind.SCORE_RANK:
            return None
        if snapshot_id is not None:
            snapshot = self.session.get(DatasetSnapshot, snapshot_id)
            if snapshot is None:
                raise NotFoundError(f"数据快照 {snapshot_id} 不存在")
            return snapshot
        snapshot = self.session.execute(
            select(DatasetSnapshot)
            .where(DatasetSnapshot.status == "PUBLISHED")
            .order_by(DatasetSnapshot.published_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snapshot is None:
            raise RuleViolationError(
                "没有已发布的数据快照。请先创建 dataset_snapshots 记录并置为 PUBLISHED，"
                "再导入招生计划或录取结果"
            )
        return snapshot


__all__ = [
    "OFFICIAL_QUALITY",
    "OPTIONAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "TEMPLATES",
    "ImportIssue",
    "ImportKind",
    "ImportReport",
    "IngestionService",
]
