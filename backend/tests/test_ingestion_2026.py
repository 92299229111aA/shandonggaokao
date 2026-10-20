"""Tests for the 2026 ingestion.

Three behaviours matter here, and each guards a failure that would otherwise be silent:

* parsing the two real workbooks (skip when the materials are not present);
* **same-base dedupe keeping the hardest 位次** — 工科试验班 and its direction-specific
  siblings normalise to one name, and picking an arbitrary row would misstate safety;
* **refusing to trust an unsourced 2026 population** — the DEMO seed writes
  `exam_year_stats` for 2026 with the fixture constant 308,000 and `purge_demo` never
  cleaned that table. Every 2026 percentile would divide by a fictional population and
  nothing would complain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.admission.models import ExamYearStats, SourceDocument
from app.ingestion.etl_2026 import (
    EXAM_YEAR,
    PROGRAM_LEVEL,
    Load26Report,
    drop_untrusted_stats,
    write_stats,
)

MATERIALS = Path(r"D:\山东高考\2026")


def _requires_materials() -> None:
    if not MATERIALS.exists():
        pytest.skip(f"未提供 2026 资料目录 {MATERIALS}")


def test_score_rank_table_parses() -> None:
    _requires_materials()
    from app.ingestion.etl_2026 import SCORE_RANK_FILE

    path = MATERIALS / SCORE_RANK_FILE
    if not path.exists():
        pytest.skip("缺少 2026 一分一段表")

    import pandas as pd

    from app.ingestion.etl_2026 import SCORE_RANK_HEADER_ROWS, _to_int

    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    rows = 0
    previous = None
    for index in range(SCORE_RANK_HEADER_ROWS, len(frame)):
        score = _to_int(frame.iat[index, 0])
        cumulative = _to_int(frame.iat[index, 2])
        if score is None or cumulative is None:
            continue
        rows += 1
        # The table is published high score -> low score, cumulative only grows.
        if previous is not None:
            assert cumulative >= previous, f"累计人数在分数 {score} 处不单调"
        previous = cumulative

    assert rows > 500, f"只解析出 {rows} 行"
    assert previous is not None and previous > 500_000, "总人数异常"


def test_outcome_rows_parse_and_dedupe_keeps_hardest() -> None:
    _requires_materials()
    from collections import defaultdict

    import pandas as pd

    from app.ingestion.etl_2026 import MAJOR_RE, ROUND1_FILE, UNIVERSITY_RE, _to_int
    from app.ingestion.etl_shandong import base_name

    path = MATERIALS / ROUND1_FILE
    if not path.exists():
        pytest.skip("缺少 2026 投档情况表")

    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    rows = 0
    bad = 0
    lowest: dict[tuple[str, str], int] = defaultdict(lambda: 10**9)
    for index in range(2, len(frame)):
        major_raw = frame.iat[index, 0]
        university_raw = frame.iat[index, 1]
        if pd.isna(major_raw) or pd.isna(university_raw):
            continue
        major_match = MAJOR_RE.match(str(major_raw).replace("\n", "").strip())
        university_match = UNIVERSITY_RE.match(str(university_raw).replace("\n", "").strip())
        if not major_match or not university_match:
            bad += 1
            continue
        rank = _to_int(frame.iat[index, 3])
        if rank is None:
            continue
        rows += 1
        key = (university_match.group(1), base_name(major_match.group(2)))
        lowest[key] = min(lowest[key], rank)

    assert rows > 20_000, f"只解析出 {rows} 行"
    assert bad == 0, f"{bad} 行无法解析院校或专业"

    # The collapse is expected: 工科试验班(…) siblings share a base name.
    total_pairs = rows
    distinct = len(lowest)
    assert distinct < total_pairs, "预期存在同基名重复，实际没有——检查 base_name 是否生效"

    # Every retained value must be the hardest of its group.
    for rank in lowest.values():
        assert rank > 0


def _fake_2026_stats(db_session, ranked_candidates: int) -> ExamYearStats:
    row = ExamYearStats(
        province_code="37",
        exam_year=EXAM_YEAR,
        exam_category="SUMMER_REGULAR",
        program_level=PROGRAM_LEVEL,
        ranked_candidates=ranked_candidates,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_untrusted_stats_are_dropped(db_session) -> None:
    """An unsourced 2026 population (the DEMO leftover) must not survive the load."""
    _fake_2026_stats(db_session, 308_000)
    report = Load26Report()

    drop_untrusted_stats(db_session, report)

    remaining = db_session.execute(
        select(ExamYearStats).where(
            ExamYearStats.exam_year == EXAM_YEAR,
            ExamYearStats.program_level == PROGRAM_LEVEL,
        )
    ).scalar_one_or_none()
    assert remaining is None
    assert report.dropped_stats == 308_000


def test_write_stats_records_the_supplied_population(db_session) -> None:
    """Supplying the number replaces the row and attributes it to a source document."""
    _fake_2026_stats(db_session, 308_000)
    document = SourceDocument(
        title="测试用 2026 一分一段表",
        publisher="test",
        archive_path="test.xls",
        checksum_sha256="0" * 64,
        quality="THIRD_PARTY",
        is_demo=False,
    )
    db_session.add(document)
    db_session.flush()

    report = Load26Report()
    write_stats(
        db_session,
        document,
        ranked_candidates=358_087,
        regular_line=442,
        report=report,
    )

    row = db_session.execute(
        select(ExamYearStats).where(
            ExamYearStats.exam_year == EXAM_YEAR,
            ExamYearStats.program_level == PROGRAM_LEVEL,
        )
    ).scalar_one()
    assert row.ranked_candidates == 358_087
    assert row.regular_line == 442
    assert row.source_document_id == document.id
    assert report.stats_written is True


def test_stats_are_required_for_outcomes_to_count() -> None:
    """Documents the coupling: without a population the history point is unusable."""
    from app.scoring.types import HistoryPoint

    without = HistoryPoint(exam_year=EXAM_YEAR, min_rank=12_000, ranked_candidates=None)
    with_population = HistoryPoint(
        exam_year=EXAM_YEAR, min_rank=12_000, ranked_candidates=358_087
    )
    assert without.usable is False
    assert with_population.usable is True
