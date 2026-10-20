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


# ---------------- editor pool: risk interleave, exclusion, filter-aware counts ----------------

BUCKETS = ("HIGH_REACH", "REACH", "BORDER", "STABLE", "SAFE")
PER_BUCKET = 40
# Distinct ACS band per bucket so ordering assertions are unambiguous. ACS would normally
# determine the bucket; here both are set explicitly so either can be varied independently.
ACS_BASE = {"HIGH_REACH": 10.0, "REACH": 35.0, "BORDER": 50.0, "STABLE": 65.0, "SAFE": 85.0}


@pytest.fixture()
def mixed(db_session):
    """A session with an equal number of items in every risk bucket.

    The editor's problem was that `limit` returned a single bucket, so the fixture needs
    several buckets with the same size to make an interleave observable.
    """
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
        model = ModelVersion(code="mix-v1", label="测试", parameters={})
        db_session.add(model)
        db_session.flush()

    snapshot = db_session.execute(select(DatasetSnapshot).limit(1)).scalars().first()
    if snapshot is None:
        snapshot = DatasetSnapshot(code="MIX", label="测试", status="PUBLISHED")
        db_session.add(snapshot)
        db_session.flush()

    profile = Profile(cycle_id=cycle.id, exam_year=2026, rank=1000, subjects=["PHY", "CHM", "BIO"])
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=1000, target_count=96
    )
    db_session.add(preference)
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

    university = University(
        ministry_code="MIX001", name="混合大学", province_code="37", province_name="山东省"
    )
    db_session.add(university)
    db_session.flush()

    indexes: dict[str, list[int]] = {bucket: [] for bucket in BUCKETS}
    counter = 0
    for bucket in BUCKETS:
        for offset in range(PER_BUCKET):
            series = ProgramSeries(
                series_key=f"MIX|{counter}",
                canonical_name="计算机科学与技术",
                university_id=university.id,
                first_year=2022,
                last_year=2025,
            )
            db_session.add(series)
            db_session.flush()
            plan = AdmissionPlan(
                cycle_id=cycle.id,
                series_id=series.id,
                snapshot_id=snapshot.id,
                plan_name=f"{bucket}-{offset:03d}",
                plan_count=10,
                tuition_fee=5000,
                duration_years=4,
                is_demo=False,
            )
            db_session.add(plan)
            db_session.flush()
            item = RecommendationItem(
                session_id=run.id,
                plan_id=plan.id,
                eligibility="PASS",
                acs=ACS_BASE[bucket] + offset * 0.1,  # decreasing within a bucket
                confidence=0.9,
                risk_bucket=bucket,
                preference_score=50.0,  # identical in every bucket: forces the tiebreaker
                in_candidate_pool=True,
            )
            db_session.add(item)
            db_session.flush()
            indexes[bucket].append(item.id)
            counter += 1
    db_session.flush()
    return {"session_id": run.id, "indexes": indexes}


def test_risk_mix_interleaves_all_buckets_on_the_first_page(db_session, mixed) -> None:
    """The editor needs every bucket visible immediately, not one bucket per page."""
    page = RecommendationService(db_session).list_items(
        session_id=mixed["session_id"], limit=10, offset=0, sort=ItemSort.RISK_MIX.value
    )
    seen = [item.risk_bucket for item in page.items]
    assert set(seen) == set(BUCKETS), f"首页只出现了 {set(seen)}"
    # One full round means the first five are one of each, in bucket order.
    assert seen[:5] == list(BUCKETS)


def test_risk_mix_pages_without_gaps_or_repeats(db_session, mixed) -> None:
    """Round-robin must stay stable across pages, otherwise paging loses items."""
    service = RecommendationService(db_session)
    total = PER_BUCKET * len(BUCKETS)
    collected: list[int] = []
    page_size = 15
    offset = 0
    while offset < total:
        page = service.list_items(
            session_id=mixed["session_id"],
            limit=page_size,
            offset=offset,
            sort=ItemSort.RISK_MIX.value,
        )
        collected.extend(item.item_id for item in page.items)
        offset += page_size
    assert len(collected) == total, f"收集 {len(collected)} 条，应为 {total}"
    assert len(set(collected)) == total, "存在重复条目"


def test_preference_tiebreak_prefers_the_safer_candidate(db_session, mixed) -> None:
    """With equal preference scores the tiebreaker must show 稳 before 高冲.

    Ascending ACS was the bug: it put the hardest programmes first and made the editor's
    pool look like it contained only 高冲. Every item here shares `preference_score`, so
    the tiebreaker alone decides the order and the bucket bands become visible.
    """
    page = RecommendationService(db_session).list_items(
        session_id=mixed["session_id"],
        limit=PER_BUCKET * len(BUCKETS),
        offset=0,
        sort=ItemSort.PREFERENCE.value,
    )
    seen = [item.risk_bucket for item in page.items]
    # Safest band first, hardest last: one contiguous run per bucket.
    expected = [bucket for bucket in reversed(BUCKETS) for _ in range(PER_BUCKET)]
    assert seen == expected


def test_exclude_scheme_id_drops_items_already_in_the_scheme(db_session, mixed) -> None:
    """The editor must not offer candidates it already holds."""
    from app.recommendation.models import Scheme, SchemeItem, SchemeVersion

    run_id = mixed["session_id"]
    scheme = Scheme(session_id=run_id, risk_mode="BALANCED", target_count=96, current_version_no=1)
    db_session.add(scheme)
    db_session.flush()
    version = SchemeVersion(scheme_id=scheme.id, version_no=1, operation="OPTIMIZE", item_count=3)
    db_session.add(version)
    db_session.flush()
    chosen = [mixed["indexes"]["SAFE"][0], mixed["indexes"]["SAFE"][1], mixed["indexes"]["STABLE"][0]]
    for position, item_id in enumerate(chosen, start=1):
        db_session.add(
            SchemeItem(
                scheme_version_id=version.id,
                recommendation_item_id=item_id,
                position=position,
                locked=False,
                fixed_position=False,
            )
        )
    db_session.flush()

    service = RecommendationService(db_session)
    page = service.list_items(
        session_id=run_id, limit=500, offset=0, exclude_scheme_id=scheme.id
    )
    assert page.total == PER_BUCKET * len(BUCKETS) - len(chosen)
    returned = {item.item_id for item in page.items}
    assert returned.isdisjoint(chosen), "排除后仍返回了方案内已存在的志愿"


def test_bucket_counts_reflect_filters_but_not_the_bucket_filter(db_session, mixed) -> None:
    """Tab counts must match what the list will contain, per bucket."""
    service = RecommendationService(db_session)
    plain = service.list_items(session_id=mixed["session_id"], limit=1, offset=0)
    assert plain.bucket_counts == {bucket: PER_BUCKET for bucket in BUCKETS}

    # Selecting a bucket must not zero the other tabs.
    only_safe = service.list_items(
        session_id=mixed["session_id"], limit=1, offset=0, risk_bucket="SAFE"
    )
    assert only_safe.total == PER_BUCKET
    assert only_safe.bucket_counts == {bucket: PER_BUCKET for bucket in BUCKETS}

    # A non-bucket filter must narrow every tab.
    no_match = service.list_items(
        session_id=mixed["session_id"], limit=1, offset=0, search="不存在的专业"
    )
    assert no_match.bucket_counts == {}


def test_bucket_counts_shrink_after_excluding_a_scheme(db_session, mixed) -> None:
    """Adding candidates to the scheme must lower the counts the editor shows."""
    from app.recommendation.models import Scheme, SchemeItem, SchemeVersion

    scheme = Scheme(
        session_id=mixed["session_id"], risk_mode="BALANCED", target_count=96, current_version_no=1
    )
    db_session.add(scheme)
    db_session.flush()
    version = SchemeVersion(scheme_id=scheme.id, version_no=1, operation="OPTIMIZE", item_count=2)
    db_session.add(version)
    db_session.flush()
    for position, item_id in enumerate(mixed["indexes"]["BORDER"][:2], start=1):
        db_session.add(
            SchemeItem(
                scheme_version_id=version.id,
                recommendation_item_id=item_id,
                position=position,
                locked=False,
                fixed_position=False,
            )
        )
    db_session.flush()

    page = RecommendationService(db_session).list_items(
        session_id=mixed["session_id"], limit=1, offset=0, exclude_scheme_id=scheme.id
    )
    assert page.bucket_counts["BORDER"] == PER_BUCKET - 2
    assert page.bucket_counts["SAFE"] == PER_BUCKET


# ---------------- grouped-by-university view ----------------
#
# The joins in `_build_filters` are conditional for speed. That makes it possible to write
# a grouped query that references `University` without joining it — SQLAlchemy then infers
# FROM from the column reference and silently produces a cartesian product. These tests
# fail loudly on that, because a cross join multiplies `item_count` while `total` may still
# look plausible.


def test_university_groups_sum_matches_the_flat_total(db_session, seeded) -> None:
    """Σ item_count over all groups must equal the number of items in the flat list."""
    service = RecommendationService(db_session)
    flat = service.list_items(session_id=seeded["session_id"], limit=1, offset=0)

    collected = 0
    offset = 0
    while True:
        page = service.list_university_groups(
            session_id=seeded["session_id"], limit=10, offset=offset
        )
        if not page.items:
            break
        collected += sum(group.item_count for group in page.items)
        offset += len(page.items)
    assert collected == flat.total, (
        f"分组条目数之和 {collected} ≠ 平铺 {flat.total}"
        "（差值是笛卡尔积的典型症状）"
    )


def test_university_groups_total_is_the_distinct_university_count(db_session, seeded) -> None:
    """6 universities seeded, so the grouped view must report 6 groups."""
    page = RecommendationService(db_session).list_university_groups(
        session_id=seeded["session_id"], limit=50, offset=0
    )
    assert page.total == len(seeded["universities"])
    assert len({group.university_id for group in page.items}) == len(seeded["universities"])
    # Every group must carry real university data, not a blank placeholder from a bad join.
    for group in page.items:
        assert group.university_name
        assert group.province_name


def test_university_groups_respect_filters_and_expand_consistently(db_session, seeded) -> None:
    """A group's count must equal the rows returned when that group is expanded."""
    service = RecommendationService(db_session)
    groups = service.list_university_groups(
        session_id=seeded["session_id"], limit=50, offset=0, province_name="北京市"
    )
    assert groups.total == 1, "北京市只种了一所学校"

    group = groups.items[0]
    expanded = service.list_items(
        session_id=seeded["session_id"],
        limit=500,
        offset=0,
        university_id=group.university_id,
    )
    assert expanded.total == group.item_count
    assert all(item.university_id == group.university_id for item in expanded.items)


def test_university_groups_respect_exclude_scheme(db_session, mixed) -> None:
    """Excluding a scheme must drop groups that become empty and lower the counts."""
    from app.recommendation.models import Scheme, SchemeItem, SchemeVersion

    scheme = Scheme(
        session_id=mixed["session_id"], risk_mode="BALANCED", target_count=96, current_version_no=1
    )
    db_session.add(scheme)
    db_session.flush()
    version = SchemeVersion(scheme_id=scheme.id, version_no=1, operation="OPTIMIZE", item_count=3)
    db_session.add(version)
    db_session.flush()
    chosen = mixed["indexes"]["SAFE"][:3]
    for position, item_id in enumerate(chosen, start=1):
        db_session.add(
            SchemeItem(
                scheme_version_id=version.id,
                recommendation_item_id=item_id,
                position=position,
                locked=False,
                fixed_position=False,
            )
        )
    db_session.flush()

    groups = RecommendationService(db_session).list_university_groups(
        session_id=mixed["session_id"], limit=10, offset=0, exclude_scheme_id=scheme.id
    )
    # The fixture puts every item in one university, so one group with fewer items remains.
    assert groups.total == 1
    assert groups.items[0].item_count == PER_BUCKET * len(BUCKETS) - len(chosen)




