"""Regression tests for the real-data ingestion path.

Guards the two things that broke during development and would silently corrupt
results if they regressed:

1. The 选科要求 parser. 「物/化/生(3选1)」 must mean *any one of three*, not all
   three — reading it backwards flips a plan between eligible and ineligible.
2. 一分一段表 loading. The rank percentile denominator must be the 一段 population
   (~305-333k), not the combined 一段+二段 population (~660k); using the wrong one
   halves every percentile and depresses ACS across the board.
"""

from __future__ import annotations

import pytest

from app.ingestion.subject_rules import (
    coverage_report,
    parse_subject_requirement,
)

# ---------------------------------------------------------------- parser


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("不限", "不限"),
        ("物理", "物理"),
        ("物理和化学", "物理+化学"),
        ("物理和化学和生物", "物理+化学+生物"),
        ("物理 或 化学", "物理 或 化学"),
        # abbreviated list forms — the majority of the 2022-2024 data
        ("物/化/生(3选1)", "物理 或 化学 或 生物"),
        ("物/化/地(3选1)", "物理 或 化学 或 地理"),
        ("史/政/地(3选1)", "历史 或 思想政治 或 地理"),
        ("物、化、生(3科必选)", "物理+化学+生物"),
        ("物化生(3科必选)", "物理+化学+生物"),
        ("史政地(3科必选)", "历史+思想政治+地理"),
        # full-name list forms
        ("物理、化学(2科必选)", "物理+化学"),
        ("思想政治和历史", "思想政治+历史"),
        ("化学,生物", "化学+生物"),
        ("物理化学", "物理+化学"),
        ("物理必选", "物理"),
    ],
)
def test_parser_maps_real_values(raw: str, expected: str) -> None:
    parsed = parse_subject_requirement(raw)
    assert parsed is not None, f"{raw!r} 应当可以解析"
    assert parsed.as_text() == expected


@pytest.mark.parametrize(
    "raw",
    [
        "物化生(3选2)",      # choosing 2 of 3 is not expressible as plain DNF
        "物理或历史且化学",   # nested expression
        "详见招生章程",       # prose, no subject information
        "面试合格",
        "",
        None,
    ],
)
def test_parser_refuses_what_it_cannot_model(raw: str | None) -> None:
    """Refusing keeps the plan at UNKNOWN; guessing would mark it eligible."""
    assert parse_subject_requirement(raw) is None


def test_three_choose_one_is_disjunctive_not_conjunctive() -> None:
    """The bug that mattered: 「3选1」 must be OR, not AND."""
    parsed = parse_subject_requirement("物/化/生(3选1)")
    assert parsed is not None
    assert parsed.mode == "ANY"
    assert len(parsed.groups) == 3, "三个单科组，任选其一"
    assert all(len(group) == 1 for group in parsed.groups)
    assert parsed.required_count == 1


def test_three_must_choose_all_is_conjunctive() -> None:
    parsed = parse_subject_requirement("物、化、生(3科必选)")
    assert parsed is not None
    assert parsed.mode == "ALL"
    assert parsed.groups == (("PHY", "CHM", "BIO"),)


def test_plain_he_and_forms_are_conjunctive() -> None:
    parsed = parse_subject_requirement("物理和化学")
    assert parsed is not None
    assert parsed.mode == "ALL"
    assert parsed.groups == (("PHY", "CHM"),)


def test_unrestricted_has_no_groups() -> None:
    parsed = parse_subject_requirement("不限")
    assert parsed is not None
    assert parsed.unrestricted is True
    assert parsed.groups == ()


def test_coverage_treats_blank_cells_as_data_gaps() -> None:
    report = coverage_report(["物理", "不限", None, float("nan"), "物理和化学"])
    assert report["total"] == 5
    assert report["unparsed"] == 2
    assert report["coverage"] == pytest.approx(60.0)
    assert report["unparsed_values"] == {"<空>": 2}


@pytest.mark.integration
def test_real_dataset_rank_percentile_uses_first_tier_population(db_session) -> None:
    """ranked_candidates 必须是「普通类一段」达线人数，不是一段+二段总人数。

    2025 年官方一段达线 333,469；若误用一分一段表末行的 681,127，
    所有考生的位次百分位会被压低一半，ACS 整体失真。
    """
    from sqlalchemy import select

    from app.admission.models import ExamYearStats

    rows = db_session.execute(
        select(ExamYearStats.exam_year, ExamYearStats.ranked_candidates).where(
            ExamYearStats.program_level == "UNDERGRAD"
        )
    ).all()
    if not rows:
        pytest.skip("未载入真实一分一段数据")

    for year, population in rows:
        assert 250_000 < population < 400_000, (
            f"{year} 年 ranked_candidates={population:,} 不在合理区间；"
            "疑似把普通类二段也算进了分母"
        )
