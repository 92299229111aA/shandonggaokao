"""Ingest the 2026 material: 一分一段表 and 常规批投档情况表.

The 2026 folder is a different shape from the 2022-2025 set: it is a post-hoc publication
carrying **already-decided cutoffs** (`最低位次`), not a plan book. So it contributes
*history*, and deliberately does not create a 2026 admission cycle:

  * `admission_cycles` drives the recommendation target, and the frontend defaults to the
    newest cycle. A 2026 cycle with no 2026 招生计划 would offer an empty pool.
  * The 2025 cycle therefore stays recommendable, with 2026 added as the fifth history
    year — "latest plan catalogue + most recent cutoffs", which is the best model the
    available data supports.

**The 一段 population is a required input, not a guess.** Every rank percentile is divided
by it (`q_y = min_rank / ranked_candidates`), and `HistoryPoint.usable` drops rows whose
year has no population. Without `--ranked-candidates` the 2026 outcomes are stored but
inert. The web page says so instead of inventing a number: `exam_year_stats` is a policy
figure and must come from the user.

Run:
    python -m app.ingestion.etl_2026 --write                       # 只导分段与录取位次
    python -m app.ingestion.etl_2026 --write --ranked-candidates 358087 --regular-line 442
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.admission.models import (
    AdmissionOutcome,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SourceDocument,
)
from app.db.session import session_scope
from app.ingestion.data_root import ENV_VAR
from app.ingestion.etl_shandong import SNAPSHOT_CODE, base_name
from app.reference.models import University

EXAM_YEAR = 2026
PROVINCE_CODE = "37"
EXAM_CATEGORY = "SUMMER_REGULAR"
PROGRAM_LEVEL = "UNDERGRAD"

QUALITY = "THIRD_PARTY"
PUBLISHER = "山东省教育招生考试院（经用户提供，非官方渠道核验）"
POLICY_VERSION = "sd-real-2022-2026"

SCORE_RANK_FILE = "2026一分一段表.xls"
ROUND1_FILE = "山东省2026年普通类常规批第1次志愿投档情况表.xls"

# "17 文科试验班类(不限选考科目类专业)" -> seq + name
MAJOR_RE = re.compile(r"^(\S{1,4})\s+(.+)$")
# "A001北京大学" -> code + name
UNIVERSITY_RE = re.compile(r"^([A-Z]\d{2,4})(.+)$")

# Rows 0..N of the 一分一段 sheet are title + two header rows.
SCORE_RANK_HEADER_ROWS = 3
COL_SCORE = 0
COL_SAME_COUNT = 1
COL_CUMULATIVE = 2


@dataclass
class Load26Report:
    segments: int = 0
    outcomes: int = 0
    outcomes_updated: int = 0
    rows_parsed: int = 0
    unmatched_series: int = 0
    unmatched_unknown_university: int = 0
    no_rank: int = 0
    collapsed: int = 0
    matched_rows: int = 0
    dropped_stats: int | None = None
    stats_written: bool = False
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"一分一段分段 {self.segments:,} 行",
            f"投档表解析 {self.rows_parsed:,} 行",
            f"录取位次新增 {self.outcomes:,} 行，更新 {self.outcomes_updated:,} 行",
            f"未能匹配专业序列 {self.unmatched_series:,} 行"
            f"（其中院校代码在现有数据中不存在 {self.unmatched_unknown_university:,} 行）",
        ]
        if self.no_rank:
            out.append(f"位次缺失跳过 {self.no_rank:,} 行")
        series_written = self.outcomes + self.outcomes_updated
        duplicates = self.matched_rows - series_written
        if duplicates > 0:
            out.append(
                f"同一专业基名合并 {duplicates:,} 行：{self.matched_rows:,} 行落到 "
                f"{series_written:,} 个序列，取其中最难的位次"
                "（工科试验班等方向班归一到同一序列，见 D-033）"
            )
        if self.dropped_stats is not None:
            out.append(
                f"已清除不可信的 2026 考生人数 {self.dropped_stats:,}"
                "（DEMO 种子留下的占位值，会让整年位次除以一个虚构人口）"
            )
        out.append(
            "2026 考生人数已写入（参与计算）"
            if self.stats_written
            else "!! 未提供 2026 考生人数：录取位次已入库，但在补上之前不会参与 ACS"
        )
        return out


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _to_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).replace(",", "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def source_document(session: Session, path: Path, label: str) -> SourceDocument:
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
            "2026 年山东省普通类常规批投档/一分一段数据，由用户提供。"
            "未能从官方渠道核验，按规格标记为 THIRD_PARTY。"
        ),
    )
    session.add(document)
    session.flush()
    return document


def load_score_rank(session: Session, path: Path, document: SourceDocument, report: Load26Report) -> None:
    """Load the 全体 columns of the 一分一段表.

    The sheet also carries per-subject columns (选考物理/化学/…), which this system does not
    model: percentiles use the whole-population ranking, so only 全体本段人数 / 全体累计人数
    are read. Keeping the rest would be unused weight.
    """
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    session.execute(
        delete(ScoreRankSegment).where(
            ScoreRankSegment.exam_year == EXAM_YEAR,
            ScoreRankSegment.exam_category == EXAM_CATEGORY,
            ScoreRankSegment.program_level == PROGRAM_LEVEL,
        )
    )

    for index in range(SCORE_RANK_HEADER_ROWS, len(frame)):
        score = _to_int(frame.iat[index, COL_SCORE])
        same = _to_int(frame.iat[index, COL_SAME_COUNT])
        cumulative = _to_int(frame.iat[index, COL_CUMULATIVE])
        if score is None or same is None or cumulative is None:
            continue
        session.add(
            ScoreRankSegment(
                exam_year=EXAM_YEAR,
                exam_category=EXAM_CATEGORY,
                program_level=PROGRAM_LEVEL,
                score=score,
                same_score_count=same,
                cumulative_rank=cumulative,
                source_document_id=document.id,
            )
        )
        report.segments += 1
        if report.segments % 200 == 0:
            session.flush()
    session.flush()


LEGACY_SNAPSHOT_CODE = "SD-2022-2025"


def ensure_snapshot(session: Session) -> DatasetSnapshot:
    """Find the dataset snapshot, renaming it to the 2022-2026 code if needed.

    The code is user-visible (`dataset_meta`), so labelling a set that now contains 2026
    data as 2022-2025 would be misleading. Renaming in place rather than inserting a second
    snapshot keeps every plan pointing at the same row and avoids two PUBLISHED snapshots.
    """
    snapshot = session.execute(
        select(DatasetSnapshot).where(DatasetSnapshot.code == SNAPSHOT_CODE)
    ).scalar_one_or_none()
    if snapshot is not None:
        return snapshot

    legacy = session.execute(
        select(DatasetSnapshot).where(DatasetSnapshot.code == LEGACY_SNAPSHOT_CODE)
    ).scalar_one_or_none()
    if legacy is None:
        raise SystemExit(
            f"找不到数据集快照（{SNAPSHOT_CODE} 或 {LEGACY_SNAPSHOT_CODE}），请先跑 2022-2025 的 ETL"
        )

    legacy.code = SNAPSHOT_CODE
    legacy.label = "山东 2022-2026 真实数据"
    legacy.notes = (
        "2022-2025 招生计划与录取来自第三方资料包；"
        "2026 录取位次与一分一段表来自用户提供的当年公示文件。"
    )
    session.flush()
    return legacy


def load_outcomes(
    session: Session,
    path: Path,
    document: SourceDocument,
    snapshot: DatasetSnapshot,
    report: Load26Report,
) -> None:
    """Attach 2026 cutoffs to existing cross-year series.

    Matching is by (院校代码, 专业基名) — the same rule the 2022-2025 loader uses, because
    official codes recur across years only ~58% of the time while normalised names recur
    ~84%. Rows whose university does not exist in the loaded catalogue cannot become a
    series: a series needs a plan to be reachable by the pipeline, and there are no 2026
    plans here.

    **Same-base duplicates are expected.** `base_name` normalises 「工科试验班(智造重器国卓班)」
    and fifteen sibling variants to 「工科试验班」, so several distinct 2026 programmes map to
    one series (measured: 2,318 of 23,853 rows collapse). The series model simply cannot tell
    them apart. When that happens the **smallest 位次 (the hardest variant)** is kept:
    the index must never claim a programme is safer than its hardest sibling, and `min` is
    deterministic whereas "last row wins" depends on file order.
    """
    series_by_key: dict[tuple[str, str], int] = {}
    for series_id, series_key_value in session.execute(
        select(ProgramSeries.id, ProgramSeries.series_key)
    ).all():
        parts = str(series_key_value).split("|")
        if len(parts) >= 3:
            series_by_key.setdefault((parts[0], parts[2]), series_id)

    known_codes = {
        str(row[0]) for row in session.execute(select(University.ministry_code)).all()
    }

    # (series_id, min_rank) after in-file dedupe.
    chosen: dict[int, int] = {}
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    for index in range(2, len(frame)):
        major_raw = frame.iat[index, 0]
        university_raw = frame.iat[index, 1]
        rank_raw = frame.iat[index, 3]
        if pd.isna(major_raw) or pd.isna(university_raw):
            continue
        major_match = MAJOR_RE.match(str(major_raw).replace("\n", "").strip())
        university_match = UNIVERSITY_RE.match(str(university_raw).replace("\n", "").strip())
        if not major_match or not university_match:
            continue
        min_rank = _to_int(rank_raw)
        if min_rank is None:
            report.no_rank += 1
            continue

        report.rows_parsed += 1
        university_code = university_match.group(1)
        major_base = base_name(major_match.group(2))
        if not major_base:
            continue

        series_id = series_by_key.get((university_code, major_base))
        if series_id is None:
            report.unmatched_series += 1
            if university_code not in known_codes:
                report.unmatched_unknown_university += 1
            continue

        report.matched_rows += 1
        previous = chosen.get(series_id)
        if previous is None:
            chosen[series_id] = min_rank
        else:
            if min_rank < previous:
                report.collapsed += 1
            chosen[series_id] = min(min_rank, previous)

    # One bulk read of the existing rows, then decide insert vs update in memory.
    existing_rows = {
        row.series_id: row
        for row in session.execute(
            select(AdmissionOutcome).where(
                AdmissionOutcome.exam_year == EXAM_YEAR,
                AdmissionOutcome.series_id.in_(list(chosen)),
            )
        ).scalars()
    }
    for series_id, min_rank in chosen.items():
        existing = existing_rows.get(series_id)
        if existing is None:
            session.add(
                AdmissionOutcome(
                    series_id=series_id,
                    snapshot_id=snapshot.id,
                    source_document_id=document.id,
                    exam_year=EXAM_YEAR,
                    batch_code="REGULAR_BATCH_1",
                    round_no=1,
                    min_rank=min_rank,
                    is_demo=False,
                )
            )
            report.outcomes += 1
        else:
            existing.min_rank = min_rank
            existing.source_document_id = document.id
            report.outcomes_updated += 1
    session.flush()


def write_stats(
    session: Session,
    document: SourceDocument,
    *,
    ranked_candidates: int,
    regular_line: int | None,
    report: Load26Report,
) -> None:
    """Record the 2026 population that every rank percentile divides by."""
    existing = session.execute(
        select(ExamYearStats).where(
            ExamYearStats.exam_year == EXAM_YEAR,
            ExamYearStats.program_level == PROGRAM_LEVEL,
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ExamYearStats(
                province_code=PROVINCE_CODE,
                exam_year=EXAM_YEAR,
                exam_category=EXAM_CATEGORY,
                program_level=PROGRAM_LEVEL,
                regular_line=regular_line,
                ranked_candidates=ranked_candidates,
                source_document_id=document.id,
            )
        )
    else:
        existing.ranked_candidates = ranked_candidates
        if regular_line is not None:
            existing.regular_line = regular_line
        existing.source_document_id = document.id
    session.flush()
    report.stats_written = True


def drop_untrusted_stats(session: Session, report: Load26Report) -> None:
    """Remove a 2026 population row that this loader cannot vouch for.

    Found in practice: the DEMO seed writes `exam_year_stats` for 2026 with the fixture
    constant 308,000, and `purge_demo` never cleaned that table — so a demo number survived
    into "real" data. Left in place it would divide every 2026 位次 by a fictional
    population, biasing the whole year in a way nothing would flag.

    Once `--ranked-candidates` is supplied this row is replaced by a sourced one, so the
    deletion only ever removes an unsourced row.
    """
    existing = session.execute(
        select(ExamYearStats).where(
            ExamYearStats.exam_year == EXAM_YEAR,
            ExamYearStats.program_level == PROGRAM_LEVEL,
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    session.delete(existing)
    session.flush()
    report.dropped_stats = existing.ranked_candidates
    _ = report


def _resolve_materials(explicit: str | Path | None) -> Path:
    """Locate the 2026 folder without baking a machine-specific path into the code.

    Order: the explicit flag, then `$GAOKAO_DATA_ROOT/2026`, then `./2026` — matching the
    convention the 2022-2025 loader already uses.
    """
    if explicit is not None:
        return Path(explicit)

    from_env = os.environ.get(ENV_VAR)
    if from_env:
        candidate = Path(from_env) / str(EXAM_YEAR)
        if candidate.exists():
            return candidate

    local = Path.cwd() / str(EXAM_YEAR)
    if local.exists():
        return local

    return Path(from_env) / str(EXAM_YEAR) if from_env else local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真正写库（默认演练）")
    parser.add_argument(
        "--data-root",
        default=None,
        help=f"2026 资料目录。缺省时依次尝试 ${{{ENV_VAR}}}/2026 与当前目录下的 2026/",
    )
    parser.add_argument(
        "--ranked-candidates",
        type=int,
        default=None,
        help="2026 普通类一段达线人数（位次百分位的分母）。缺省则不写 exam_year_stats，"
        "录取数据保持惰性。",
    )
    parser.add_argument("--regular-line", type=int, default=None, help="2026 普通类一段线")
    args = parser.parse_args(argv)

    root = _resolve_materials(args.data_root)
    score_path = root / SCORE_RANK_FILE
    round1_path = root / ROUND1_FILE
    for path in (score_path, round1_path):
        if not path.exists():
            print(f"找不到 {path}")
            print(f"用 --data-root 指定目录，或设置环境变量 {ENV_VAR}（其下的 2026/ 子目录）")
            return 2

    print("=" * 100)
    print("2026 数据导入")
    print("=" * 100)
    print(f"一分一段 : {score_path.name}")
    print(f"投档情况 : {round1_path.name}")
    print(f"写入模式 : {'写库' if args.write else '演练'}")
    print()

    report = Load26Report()
    with session_scope() as session:
        snapshot = ensure_snapshot(session)
        score_doc = source_document(session, score_path, "2026 一分一段表")
        outcome_doc = source_document(session, round1_path, "2026 常规批第1次投档")

        load_score_rank(session, score_path, score_doc, report)
        load_outcomes(session, round1_path, outcome_doc, snapshot, report)

        if args.ranked_candidates is not None:
            write_stats(
                session,
                score_doc,
                ranked_candidates=args.ranked_candidates,
                regular_line=args.regular_line,
                report=report,
            )
        else:
            # No population supplied: make sure no unsourced 2026 row is left that the
            # scorer would silently divide by.
            drop_untrusted_stats(session, report)

        if not args.write:
            session.rollback()
            print("演练结束，未写库。加 --write 正式写入。")
            print()
        for line in report.lines():
            print(f"  - {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
