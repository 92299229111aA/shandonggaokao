"""Regression tests for the 专业 → 门类 mapping.

The failure these guard against was silent: `program_series.major_id` was NULL for all
25,280 series, so `MajorSelector.discipline_code` could never match and the 学科 selector
in the 35%-weighted 专业匹配 dimension did nothing at all. Nothing errored — the score
just never moved.

Most tests build their own inputs rather than reading the loaded dataset, so they fail
loudly instead of skipping when a database happens to be empty. The coverage check at the
end is explicitly about the loaded data and skips with a clear reason when there is none.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.admission.models import AdmissionPlan, ProgramSeries
from app.reference.models import Major, University

# The structured 2023 catalogue carries 12 门类; it has no 军事学 section. Asserted so a
# future source that does include it is noticed rather than silently accepted.
EXPECTED_DISCIPLINES = {
    "哲学", "经济学", "法学", "教育学", "文学", "历史学",
    "理学", "工学", "农学", "医学", "管理学", "艺术学",
}


def _catalogue():
    from app.ingestion.data_root import resolve_root
    from app.ingestion.enrich_majors import find_catalogue, parse_catalogue

    try:
        path = find_catalogue(resolve_root(None))
    except FileNotFoundError:
        pytest.skip("未提供结构化专业目录工作簿（见 docs/DATA_IMPORT.md）")
    return parse_catalogue(path)


def test_catalogue_parses_disciplines_and_major_classes() -> None:
    catalogue = _catalogue()
    assert len(catalogue) > 800, f"解析条目过少: {len(catalogue)}"

    disciplines = {entry.discipline_name for entry in catalogue.values()}
    assert disciplines >= EXPECTED_DISCIPLINES, (
        f"缺少门类: {EXPECTED_DISCIPLINES - disciplines}"
    )

    # 专业类 names must resolve too: the plan files list 「计算机类」 as a 志愿单位, and a
    # 专业类 never appears as its own row — it is a value in the 专业类 column.
    for class_name, expected in (
        ("计算机类", "08"),
        ("数学类", "07"),
        ("临床医学类", "10"),
    ):
        assert class_name in catalogue, f"专业类未收录: {class_name}"
        assert catalogue[class_name].discipline_code == expected


def test_catalogue_rows_carry_codes_and_categories() -> None:
    catalogue = _catalogue()
    entry = catalogue["计算机科学与技术"]
    assert entry.discipline_code == "08"
    assert entry.discipline_name == "工学"
    assert entry.category_code == "0809"
    assert entry.category_name == "计算机类"
    assert entry.major_code == "080901"


def test_every_catalogue_entry_has_a_valid_discipline_code() -> None:
    catalogue = _catalogue()
    bad = {
        name: entry.discipline_code
        for name, entry in catalogue.items()
        if not (entry.discipline_code.isdigit() and 1 <= int(entry.discipline_code) <= 14)
    }
    assert not bad, f"门类代码越界: {list(bad.items())[:10]}"


def test_mapping_fills_every_row_sharing_a_name(db_session) -> None:
    """同名专业必须全部填门类 —— 这正是第一次尝试失败的地方。

    原先每个名称只填了一行，于是 25,280 个序列全部指向同一个 major_id。
    """
    catalogue = _catalogue()

    shared = "计算机类"
    majors = [
        Major(code="0809", name=shared, level="MAJOR_CLASS"),
        Major(code="0Z", name=shared, level="MAJOR_CLASS"),
        Major(code="080901", name="计算机科学与技术", level="MAJOR"),
    ]
    for major in majors:
        entry = catalogue.get(major.name)
        assert entry is not None, major.name
        major.discipline_code = entry.discipline_code
        major.discipline_name = entry.discipline_name
        major.category_code = entry.category_code
        major.category_name = entry.category_name
    db_session.add_all(majors)
    db_session.flush()

    filled = db_session.execute(
        select(func.count())
        .select_from(Major)
        .where(Major.name == shared, Major.discipline_code.is_not(None))
    ).scalar_one()
    assert filled == 2, f"同名专业仅 {filled} 行填了门类，应为 2"

    university = University(
        ministry_code="TEST001",
        name="测试大学",
        province_code="37",
        province_name="山东省",
    )
    db_session.add(university)
    db_session.flush()

    by_name = {major.name: major for major in majors if major.discipline_code}
    series = [
        ProgramSeries(
            series_key=f"TEST|{index}",
            canonical_name=name,
            university_id=university.id,
            first_year=2022,
            last_year=2025,
        )
        for index, name in enumerate((shared, "计算机科学与技术"))
    ]
    db_session.add_all(series)
    db_session.flush()

    for row in series:
        major = by_name.get(row.canonical_name)
        if major is not None:
            row.major_id = major.id
    db_session.flush()

    linked = db_session.execute(
        select(func.count())
        .select_from(ProgramSeries)
        .join(Major, Major.id == ProgramSeries.major_id)
        .where(Major.discipline_code.is_not(None))
    ).scalar_one()
    assert linked == 2, f"仅 {linked}/2 序列关联到有门类的专业"


def test_discipline_selector_changes_the_major_dimension() -> None:
    """偏好工学必须让工学候选的专业匹配分高于其他候选。

    这是整条链路的最终验收：映射存在但打分不生效，等于没做。
    """
    from app.common.enums import PreferenceDimension
    from app.scoring.preference import (
        MajorSelector,
        PreferenceInput,
        PreferenceService,
        PreferenceWeights,
    )

    def candidate(discipline: str | None) -> PreferenceInput:
        return PreferenceInput(
            major_id=None,
            discipline_code=discipline,
            major_names=("计算机科学与技术",),
            major_text="计算机科学与技术",
            interest_keywords=(),
            university_id=1,  # the scorer never touches the DB, so any id works here
            university_name="某大学",
            university_traits={},
            province_code="37",
            city_code=None,
            tuition_fee=5000,
            tuition_budget=None,
            tuition_floor=None,
            academic_score=None,
            career_score=None,
            personal_score=None,
        )

    service = PreferenceService()
    weights = PreferenceWeights(
        major_includes=(MajorSelector(discipline_code="08", weight=100.0),)
    )

    def major_score(discipline: str | None) -> float:
        evaluation = service.evaluate(candidate=candidate(discipline), weights=weights)
        dimension = next(
            d for d in evaluation.dimensions if d.dimension == PreferenceDimension.MAJOR.value
        )
        assert dimension.available
        return dimension.score

    engineering = major_score("08")
    medicine = major_score("10")
    assert engineering > medicine, (
        f"偏好工学却未提高工学候选的专业匹配分（工学 {engineering} vs 医学 {medicine}）"
    )


def test_unset_major_preference_is_dropped_not_scored_zero() -> None:
    """未填专业偏好时该维度必须从分母剔除，而不是给所有候选 0 分。"""
    from app.common.enums import PreferenceDimension
    from app.scoring.preference import PreferenceInput, PreferenceService, PreferenceWeights

    candidate = PreferenceInput(
        major_id=None,
        discipline_code="08",
        major_names=("计算机科学与技术",),
        major_text="计算机科学与技术",
        interest_keywords=(),
        university_id=1,
        university_name="某大学",
        university_traits={},
        province_code="37",
        city_code=None,
        tuition_fee=5000,
        tuition_budget=None,
        tuition_floor=None,
        academic_score=None,
        career_score=None,
        personal_score=None,
    )
    evaluation = PreferenceService().evaluate(
        candidate=candidate, weights=PreferenceWeights()
    )
    dimension = next(
        d for d in evaluation.dimensions if d.dimension == PreferenceDimension.MAJOR.value
    )
    assert not dimension.available, "未配置该维度时不应标记为可用（否则会按 0 分计入分母）"


@pytest.mark.integration
def test_loaded_dataset_is_filterable_by_discipline(db_session) -> None:
    """真实数据下的覆盖率 —— 直接决定学科筛选有多少实用价值。"""
    total = db_session.execute(
        select(func.count()).select_from(AdmissionPlan).where(AdmissionPlan.is_demo.is_(False))
    ).scalar_one()
    if total == 0:
        pytest.skip("测试库未载入真实招生计划")

    covered = db_session.execute(
        select(func.count())
        .select_from(AdmissionPlan)
        .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
        .join(Major, Major.id == ProgramSeries.major_id)
        .where(AdmissionPlan.is_demo.is_(False), Major.discipline_code.is_not(None))
    ).scalar_one()
    assert covered / total > 0.7, f"仅 {covered}/{total} 计划可按学科筛选"
