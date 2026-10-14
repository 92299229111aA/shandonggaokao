"""Regression tests for the candidate-list query.

The bug these guard against was a performance cliff, not a wrong answer: `list_items`
read every item row and joined plan details for all of them, then filtered, sorted and
paginated in Python. A real session is 19,019 rows, so every request cost ~7.7 s
*regardless of `limit`* — which is why the UI could only ever show ~1% of the pool.

Worse, `search` ran after the slice, so a school outside the first page was unfindable.

The key assertion here is `test_page_query_is_bounded_by_limit`: it counts the ORM
instances actually loaded, which is the property that makes the endpoint fast. Timing
alone would be a flaky test; this fails deterministically if the old code returns.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.admission.models import (
    AdmissionCycle,
    AdmissionPlan,
    DatasetSnapshot,
    ProgramSeries,
)
from app.candidate.models import PreferenceProfile, Profile
from app.recommendation.models import ModelVersion, RecommendationItem
from app.recommendation.models import Session as RunSession
from app.recommendation.service import RecommendationService
from app.recommendation.sorts import ItemSort
from app.reference.models import University

ITEM_COUNT = 300
PAGE_SIZE = 50


@pytest.fixture()
def seeded(db_session):
    """A session with 300 candidates spread over 6 universities."""
    cycle = db_session.execute(select(AdmissionCycle).limit(1)).scalars().first()
    if cycle is None:
        cycle = AdmissionCycle(
            province_code="37",
            exam_year=2026,
            exam_category="SUMMER_REGULAR",
            batch_code="REGULAR_BATCH_1",
            application_limit=96,
        )
        db_session.add(cycle)
        db_session.flush()

    model = db_session.execute(select(ModelVersion).limit(1)).scalars().first()
    if model is None:
        model = ModelVersion(code="test-v1", label="测试", parameters={})
        db_session.add(model)
        db_session.flush()

    profile = Profile(cycle_id=cycle.id, exam_year=2026, rank=1000, subjects=["PHY", "CHM", "BIO"])
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=1000, target_count=96
    )
    db_session.add(preference)
    db_session.flush()
    snapshot = db_session.execute(select(DatasetSnapshot).limit(1)).scalars().first()
    if snapshot is None:
        snapshot = DatasetSnapshot(code="T-LIST", label="测试快照", status="PUBLISHED")
        db_session.add(snapshot)
        db_session.flush()

    run = RunSession(
        profile_id=profile.id,
        preference_profile_id=preference.id,
        cycle_id=cycle.id,
        snapshot_id=snapshot.id,
        model_version_id=model.id,
        profile_version=1,
        preference_version=1,
        policy_version="test",
        model_version=model.code,
        status="SUCCEEDED",
    )
    db_session.add(run)
    db_session.flush()

    universities = [
        University(
            ministry_code=f"T{i:04d}",
            name=name,
            province_code=province_code,
            province_name=province_name,
        )
        for i, (name, province_code, province_name) in enumerate(
            [
                ("甲大学", "37", "山东省"),
                ("乙大学", "11", "北京市"),
                ("丙大学", "31", "上海市"),
                ("丁大学", "44", "广东省"),
                ("戊大学", "32", "江苏省"),
                ("己大学", "51", "四川省"),
            ]
        )
    ]
    db_session.add_all(universities)
    db_session.flush()

    counters = {"plan": 0}
    # Grouped by university rather than interleaved, so the default ordering makes page 1
    # hold only one school. That is the shape which made the old search-after-slice bug
    # observable: a school present in the pool but outside page 1 could not be found.
    per_university = ITEM_COUNT // len(universities)
    for index in range(ITEM_COUNT):
        university = universities[index // per_university]
        series = ProgramSeries(
            series_key=f"T|{index}",
            canonical_name="计算机科学与技术",
            university_id=university.id,
            first_year=2022,
            last_year=2025,
        )
        db_session.add(series)
        db_session.flush()
        counters["plan"] += 1
        plan = AdmissionPlan(
            cycle_id=cycle.id,
            series_id=series.id,
            snapshot_id=snapshot.id,
            plan_name=f"专业{index:03d}",
            plan_count=10 + index,
            tuition_fee=5000 + index,
            duration_years=4,
            is_demo=False,
        )
        db_session.add(plan)
        db_session.flush()
        db_session.add(
            RecommendationItem(
                session_id=run.id,
                plan_id=plan.id,
                eligibility="PASS",
                acs=float(index % 100),
                confidence=0.9,
                risk_bucket="STABLE",
                preference_score=float(99 - (index % 100)),
                in_candidate_pool=True,
            )
        )
    db_session.flush()
    return {"session_id": run.id, "universities": universities}


def test_total_counts_the_whole_pool_not_the_page(db_session, seeded) -> None:
    service = RecommendationService(db_session)
    page = service.list_items(session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0)
    assert page.total == ITEM_COUNT
    assert len(page.items) == PAGE_SIZE


def test_page_query_is_bounded_by_limit(db_session, seeded) -> None:
    """The service must not materialise the whole pool to serve one page.

    Counted via SQLAlchemy's own instance bookkeeping (`Session.new`/`identity_map`), so
    it measures what the ORM actually loaded rather than wall-clock time.
    """
    session = db_session
    session.expunge_all()
    service = RecommendationService(session)
    page = service.list_items(session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0)
    assert len(page.items) == PAGE_SIZE

    loaded = [obj for obj in session.identity_map.values() if isinstance(obj, RecommendationItem)]
    assert len(loaded) <= PAGE_SIZE, (
        f"加载了 {len(loaded)} 个 RecommendationItem，"
        f"应为 ≤ {PAGE_SIZE}（说明分页没有下推到数据库）"
    )


def test_search_finds_a_school_outside_the_first_page(db_session, seeded) -> None:
    """Search ran after the slice before the fix, so this case could not be found."""
    service = RecommendationService(db_session)
    first = service.list_items(session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0)
    loaded_names = {item.university_name for item in first.items}

    target = "己大学"
    assert target not in loaded_names, "构造前提失败：目标院校本应不在第一页"

    found = service.list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, search=target
    )
    assert found.total == ITEM_COUNT // len(seeded["universities"])
    assert all(item.university_name == target for item in found.items)


def test_offline_page_returns_the_remainder(db_session, seeded) -> None:
    service = RecommendationService(db_session)
    offset = ITEM_COUNT - 10
    page = service.list_items(session_id=seeded["session_id"], limit=PAGE_SIZE, offset=offset)
    assert len(page.items) == 10
    assert page.total == ITEM_COUNT


@pytest.mark.parametrize(
    ("sort", "attribute", "reverse"),
    [
        (ItemSort.PREFERENCE, "preference_score", True),
        (ItemSort.ACS_DESC, "acs", True),
        (ItemSort.ACS_ASC, "acs", False),
        (ItemSort.TUITION_ASC, "tuition_fee", False),
        (ItemSort.PLAN_COUNT_DESC, "plan_count", True),
    ],
)
def test_each_sort_is_monotonic(db_session, seeded, sort, attribute, reverse) -> None:
    page = RecommendationService(db_session).list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, sort=sort.value
    )
    values = [getattr(item, attribute) for item in page.items]
    assert all(value is not None for value in values)
    if reverse:
        assert values == sorted(values, reverse=True), f"{sort} 不是降序: {values[:6]}"
    else:
        assert values == sorted(values), f"{sort} 不是升序: {values[:6]}"


def test_filters_combine(db_session, seeded) -> None:
    service = RecommendationService(db_session)
    province = service.list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, province_name="北京市"
    )
    assert province.total == ITEM_COUNT // len(seeded["universities"])
    assert all(item.province_name == "北京市" for item in province.items)

    level = service.list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, is_985=True
    )
    assert level.total == 0


def test_count_query_matches_filtered_total(db_session, seeded) -> None:
    """`total` must describe the filtered set, not the unfiltered pool."""
    service = RecommendationService(db_session)
    page = service.list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, risk_bucket="SAFE"
    )
    assert page.total == 0

    pool = db_session.execute(
        select(func.count())
        .select_from(RecommendationItem)
        .where(RecommendationItem.in_candidate_pool.is_(True))
    ).scalar_one()
    assert pool == ITEM_COUNT


def test_unknown_sort_falls_back_to_default(db_session, seeded) -> None:
    """An unrecognised key must not raise or return an arbitrary order."""
    page = RecommendationService(db_session).list_items(
        session_id=seeded["session_id"], limit=PAGE_SIZE, offset=0, sort="NOT_A_SORT"
    )
    values = [item.preference_score for item in page.items]
    assert values == sorted(values, reverse=True)


