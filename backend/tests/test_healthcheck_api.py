"""Phase 7 acceptance (DB + HTTP): health checks against a persisted scheme.

Verifies the ORM→fact bridge, that the report is persisted and re-readable, and
above all that running a health check never changes the scheme.
"""

from __future__ import annotations

import pytest

from app.admission.models import (
    AdmissionOutcome,
    AdmissionPlan,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.candidate.models import PreferenceProfile, Profile
from app.healthcheck.assembly import build_context
from app.healthcheck.service import HealthCheckService
from app.reference.models import University
from app.schemes.service import SchemeService

pytestmark = pytest.mark.integration

API = "/api/v1"
POPULATION = 300000


@pytest.fixture()
def scheme_ready(api_client, db_session, cycle):
    """A full API-driven flow ending in a saved scheme."""
    snapshot = DatasetSnapshot(code="DEMO-2026", label="DEMO", status="PUBLISHED", is_demo=True)
    db_session.add(snapshot)
    db_session.flush()
    for year in (2023, 2024, 2025, 2026):
        db_session.add(
            ExamYearStats(
                province_code="37", exam_year=year, exam_category="SUMMER_REGULAR",
                ranked_candidates=POPULATION,
            )
        )
    rule_set = SubjectRuleSet(code="PHY+CHM", mode="ALL", raw_text="物理和化学")
    db_session.add(rule_set)
    db_session.flush()
    group = SubjectRuleGroup(rule_set_id=rule_set.id, group_no=0, mode="ALL")
    db_session.add(group)
    db_session.flush()
    db_session.add_all(
        [
            SubjectRuleItem(group_id=group.id, subject_code="PHY"),
            SubjectRuleItem(group_id=group.id, subject_code="CHM"),
        ]
    )
    university = University(
        ministry_code="DEMO700", name="演示大学", province_code="37", province_name="山东省"
    )
    db_session.add(university)
    db_session.flush()

    for index in range(10):
        series = ProgramSeries(
            series_key=f"hc-series-{index}",
            canonical_name=f"专业{index}",
            university_id=university.id,
        )
        db_session.add(series)
        db_session.flush()
        # index 0 shrinks 50 -> 4; index 9 is tiny
        history_plan = 50 if index == 0 else 30
        current_plan = 4 if index == 0 else (2 if index == 9 else 30)
        for year in (2023, 2024, 2025):
            db_session.add(
                AdmissionOutcome(
                    series_id=series.id, snapshot_id=snapshot.id, exam_year=year,
                    min_rank=25000 + index * 3000, plan_count=history_plan, is_demo=True,
                )
            )
        db_session.add(
            AdmissionPlan(
                cycle_id=cycle.id, series_id=series.id, subject_rule_set_id=rule_set.id,
                snapshot_id=snapshot.id, plan_name=f"专业{index}", plan_count=current_plan,
                tuition_fee=5000 + index * 500, is_demo=True,
                special_requirements="需通过学校面试" if index == 7 else None,
            )
        )
    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=30000, score=600, subjects=["PHY", "CHM", "BIO"]
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=30000,
        target_count=96, min_safe_count=2,
    )
    db_session.add(preference)
    db_session.flush()
    db_session.commit()

    run = api_client.post(
        f"{API}/recommendations", json={"profile_id": profile.id, "preference_id": preference.id}
    )
    assert run.status_code == 201, run.text
    session_id = run.json()["session"]["id"]

    scheme = api_client.post(f"{API}/schemes", json={"recommendation_session_id": session_id})
    assert scheme.status_code == 201, scheme.text
    return {
        "client": api_client,
        "db": db_session,
        "scheme": scheme.json(),
        "session_id": session_id,
        "snapshot": snapshot,
    }


# ---------------- assembly ----------------


def test_context_is_built_from_persisted_rows(scheme_ready) -> None:
    db = scheme_ready["db"]
    ctx = build_context(db, scheme_id=scheme_ready["scheme"]["id"])

    assert ctx.item_count == 10
    assert ctx.application_limit == 96
    assert ctx.target_exam_year == 2026
    assert ctx.snapshot is not None and ctx.snapshot.code == "DEMO-2026"
    assert ctx.risk_targets

    # history and plan facts must be carried through
    facts = {fact.plan_name: fact for fact in ctx.items}
    assert facts["专业0"].plan_count == 4
    assert facts["专业0"].plan_count_history == (50, 50, 50)
    assert facts["专业7"].special_requirements_text == "需通过学校面试"
    assert all(fact.min_rank_history for fact in ctx.items)


def test_context_reports_populated_hard_preferences(scheme_ready) -> None:
    db = scheme_ready["db"]
    ctx = build_context(db, scheme_id=scheme_ready["scheme"]["id"])
    assert ctx.preference.min_safe_count == 2
    assert ctx.preference.target_count == 96


# ---------------- end-to-end report ----------------


def test_health_check_service_produces_full_report(scheme_ready) -> None:
    db = scheme_ready["db"]
    dto = HealthCheckService(db).run(scheme_id=scheme_ready["scheme"]["id"], persist=False)

    assert dto.score <= 100
    assert dto.disclaimer
    assert len(dto.checks) == 11
    codes = {issue.code for issue in dto.issues}
    # the data set was built to trigger these
    assert "PLAN_REDUCED" in codes, "50->4 的计划缩减必须被检出"
    assert "SMALL_PLAN_COUNT" in codes, "招 2 人的专业必须被检出"
    assert "UNKNOWN_REQUIREMENT" in codes, "面试要求必须被标为待确认"
    assert "DEMO_DATA" in codes, "DEMO 数据必须被标注"


def test_health_check_flags_plan_reduction_detail(scheme_ready) -> None:
    db = scheme_ready["db"]
    dto = HealthCheckService(db).run(scheme_id=scheme_ready["scheme"]["id"], persist=False)
    reduction = next(issue for issue in dto.issues if issue.code == "PLAN_REDUCED")
    assert reduction.detail["plan_count"] == 4
    assert reduction.detail["median_history"] == 50
    assert reduction.detail["change_ratio"] < -0.9


def test_health_check_persists_and_is_readable(scheme_ready) -> None:
    client = scheme_ready["client"]
    scheme_id = scheme_ready["scheme"]["id"]

    response = client.post(f"{API}/schemes/{scheme_id}/health-check", json={})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] is not None

    fetched = client.get(f"{API}/health-checks/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["score"] == body["score"]
    assert len(fetched.json()["issues"]) == len(body["issues"])

    listed = client.get(f"{API}/schemes/{scheme_id}/health-checks")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [body["id"]]


def test_health_check_specific_version(scheme_ready) -> None:
    client = scheme_ready["client"]
    scheme = scheme_ready["scheme"]
    scheme_id = scheme["id"]

    # create version 2 by removing one entry
    client.delete(f"{API}/schemes/{scheme_id}/items/10", params={"version": scheme["lock_version"]})

    v1 = client.post(f"{API}/schemes/{scheme_id}/health-check", json={"version": 1})
    v2 = client.post(f"{API}/schemes/{scheme_id}/health-check", json={"version": 2})
    assert v1.status_code == 201 and v2.status_code == 201
    assert v1.json()["summary"]["item_count"] == 10
    assert v2.json()["summary"]["item_count"] == 9
    assert v1.json()["version_no"] == 1
    assert v2.json()["version_no"] == 2


def test_health_check_unknown_version_is_404(scheme_ready) -> None:
    response = scheme_ready["client"].post(
        f"{API}/schemes/{scheme_ready['scheme']['id']}/health-check", json={"version": 99}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_health_check_does_not_modify_the_scheme(scheme_ready) -> None:
    """规格：Health Check 只报告问题，不得擅自修改方案。"""
    client = scheme_ready["client"]
    scheme_id = scheme_ready["scheme"]["id"]

    before = client.get(f"{API}/schemes/{scheme_id}").json()
    client.post(f"{API}/schemes/{scheme_id}/health-check", json={})
    after = client.get(f"{API}/schemes/{scheme_id}").json()

    assert before["current_version_no"] == after["current_version_no"]
    assert before["lock_version"] == after["lock_version"]
    assert before["item_count"] == after["item_count"]
    assert [i["recommendation_item_id"] for i in before["items"]] == [
        i["recommendation_item_id"] for i in after["items"]
    ]
    assert [i["position"] for i in before["items"]] == [i["position"] for i in after["items"]]

    versions = client.get(f"{API}/schemes/{scheme_id}/versions").json()
    assert [row["version_no"] for row in versions] == [1], "体检不得产生新方案版本"


def test_health_check_detects_hard_preference_violation_in_scheme(scheme_ready) -> None:
    """规格必测：hard_exclude 泄漏时必须报 ERROR。"""
    db = scheme_ready["db"]
    from app.candidate.models import MajorPreference

    scheme = scheme_ready["scheme"]
    # Exclude a major that is already inside the scheme, then re-check.
    victim = scheme["items"][0]
    preference = (
        db.query(PreferenceProfile)
        .filter(PreferenceProfile.id == scheme["session_id"] * 0 + _preference_id(db, scheme))
        .one()
    )
    db.add(
        MajorPreference(
            preference_profile_id=preference.id,
            major_name=victim["plan_name"],
            polarity="EXCLUDE",
            is_hard_exclude=True,
        )
    )
    db.flush()

    dto = HealthCheckService(db).run(scheme_id=scheme["id"], persist=False)
    violations = [issue for issue in dto.issues if issue.code == "HARD_PREFERENCE_VIOLATED"]
    assert violations, "硬性排除泄漏必须被检出"
    assert violations[0].severity == "ERROR"
    assert dto.passed is False


def test_health_check_on_stale_snapshot(scheme_ready) -> None:
    db = scheme_ready["db"]
    newer = DatasetSnapshot(
        code="DEMO-2027", label="更新的数据", status="PUBLISHED", is_demo=True
    )
    db.add(newer)
    db.flush()

    dto = HealthCheckService(db).run(scheme_id=scheme_ready["scheme"]["id"], persist=False)
    stale = [issue for issue in dto.issues if issue.code == "STALE_DATASET"]
    assert stale, "出现更新的快照后必须提示 STALE_DATASET"
    assert stale[0].detail["latest_snapshot_code"] == "DEMO-2027"


def test_health_check_flags_duplicate_added_manually(scheme_ready) -> None:
    """手动加入重复志愿必须被检出（DuplicateCheck 覆盖编辑后的方案）。"""
    db = scheme_ready["db"]
    scheme = scheme_ready["scheme"]
    service = SchemeService(db)
    current = service.get(scheme["id"])
    victim = current.items[0]

    # Force a duplicate row directly: this is exactly the case the check exists for.
    from app.recommendation.models import SchemeItem, SchemeVersion

    version = (
        db.query(SchemeVersion)
        .filter(
            SchemeVersion.scheme_id == scheme["id"],
            SchemeVersion.version_no == current.current_version_no,
        )
        .one()
    )
    db.add(
        SchemeItem(
            scheme_version_id=version.id,
            recommendation_item_id=victim.recommendation_item_id,
            position=current.item_count + 1,
            eligibility=victim.eligibility,
            acs=victim.acs,
            confidence=victim.confidence,
            preference_score=victim.preference_score,
            desire_band=victim.desire_band,
            risk_bucket=victim.risk_bucket,
        )
    )
    version.item_count = current.item_count + 1
    db.flush()

    dto = HealthCheckService(db).run(scheme_id=scheme["id"], persist=False)
    duplicates = [issue for issue in dto.issues if issue.code == "DUPLICATE_PLAN"]
    assert duplicates, "重复志愿必须被检出"
    assert duplicates[0].severity == "ERROR"


def _preference_id(db, scheme: dict) -> int:
    from app.recommendation.models import Session as RunSession

    run = db.get(RunSession, scheme["session_id"])
    return run.preference_profile_id
