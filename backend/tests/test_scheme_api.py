"""Phase 6 acceptance (HTTP): the scheme API contract.

Focuses on what only the HTTP layer can get wrong: status codes, the unified
error envelope, and optimistic-locking producing a real 409.
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
from app.reference.models import University

pytestmark = pytest.mark.integration

API = "/api/v1"
POPULATION = 300000


@pytest.fixture()
def ready_session(api_client, db_session, cycle):
    """Drive the real API end to end up to a generated scheme."""
    snapshot = DatasetSnapshot(code="DEMO-HTTP", label="DEMO", status="PUBLISHED", is_demo=True)
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
        ministry_code="DEMO500", name="演示大学", province_code="37", province_name="山东省"
    )
    db_session.add(university)
    db_session.flush()

    for index in range(8):
        series = ProgramSeries(
            series_key=f"http-series-{index}",
            canonical_name=f"专业{index}",
            university_id=university.id,
        )
        db_session.add(series)
        db_session.flush()
        for year in (2023, 2024, 2025):
            db_session.add(
                AdmissionOutcome(
                    series_id=series.id, snapshot_id=snapshot.id, exam_year=year,
                    min_rank=25000 + index * 3000, plan_count=30, is_demo=True,
                )
            )
        db_session.add(
            AdmissionPlan(
                cycle_id=cycle.id, series_id=series.id, subject_rule_set_id=rule_set.id,
                snapshot_id=snapshot.id, plan_name=f"专业{index}", plan_count=30,
                tuition_fee=5000, is_demo=True,
            )
        )
    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=30000, score=600, subjects=["PHY", "CHM", "BIO"]
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=30000, target_count=96
    )
    db_session.add(preference)
    db_session.flush()
    db_session.commit()

    run = api_client.post(
        f"{API}/recommendations",
        json={"profile_id": profile.id, "preference_id": preference.id},
    )
    assert run.status_code == 201, run.text
    session_id = run.json()["session"]["id"]
    assert run.json()["pool_size"] == 8

    scheme = api_client.post(f"{API}/schemes", json={"recommendation_session_id": session_id})
    assert scheme.status_code == 201, scheme.text
    return {"scheme": scheme.json(), "session_id": session_id, "client": api_client}


def test_recommendation_endpoint_locks_versions(ready_session) -> None:
    session_id = ready_session["session_id"]
    response = ready_session["client"].get(f"{API}/recommendations/{session_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["profile_version"] >= 1
    assert body["preference_version"] == 1
    assert body["policy_version"]
    assert body["model_version"]
    assert body["snapshot_id"]


def test_recommendation_items_endpoint_exposes_card_fields(ready_session) -> None:
    session_id = ready_session["session_id"]
    response = ready_session["client"].get(f"{API}/recommendations/{session_id}/items")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 8
    item = body["items"][0]
    for field in ("program_label", "acs", "confidence", "preference_score", "risk_bucket", "history"):
        assert field in item
    assert "probability" not in response.text.lower()


def test_scheme_endpoint_returns_ordered_items(ready_session) -> None:
    scheme = ready_session["scheme"]
    assert scheme["version_no"] == 1
    assert scheme["lock_version"] == 1
    assert scheme["item_count"] == 8
    assert [item["position"] for item in scheme["items"]] == list(range(1, 9))


def test_order_update_bumps_version_and_returns_409_on_stale(ready_session) -> None:
    client = ready_session["client"]
    scheme = ready_session["scheme"]
    scheme_id = scheme["id"]

    entry = [{"recommendation_item_id": scheme["items"][-1]["recommendation_item_id"], "position": 1}]
    ok = client.patch(
        f"{API}/schemes/{scheme_id}/order",
        json={"version": scheme["lock_version"], "items": entry},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["current_version_no"] == 2
    assert ok.json()["lock_version"] == 2

    conflict = client.patch(
        f"{API}/schemes/{scheme_id}/order",
        json={"version": scheme["lock_version"], "items": entry},
    )
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["error"]["code"] == "SCHEME_VERSION_CONFLICT"
    assert body["error"]["details"]["actual"] == 2
    assert body["error"]["details"]["expected"] == 1


def test_history_version_remains_readable_over_http(ready_session) -> None:
    client = ready_session["client"]
    scheme = ready_session["scheme"]
    scheme_id = scheme["id"]
    original_first = scheme["items"][0]["recommendation_item_id"]

    client.patch(
        f"{API}/schemes/{scheme_id}/order",
        json={
            "version": scheme["lock_version"],
            "items": [{"recommendation_item_id": scheme["items"][-1]["recommendation_item_id"], "position": 1}],
        },
    )

    versions = client.get(f"{API}/schemes/{scheme_id}/versions")
    assert [row["version_no"] for row in versions.json()] == [2, 1]

    old = client.get(f"{API}/schemes/{scheme_id}", params={"version_no": 1})
    assert old.status_code == 200
    assert old.json()["is_historical"] is True
    assert old.json()["items"][0]["recommendation_item_id"] == original_first


def test_lock_and_remove_over_http(ready_session) -> None:
    client = ready_session["client"]
    scheme = ready_session["scheme"]
    scheme_id = scheme["id"]

    locked = client.put(
        f"{API}/schemes/{scheme_id}/items/1/lock",
        json={"version": scheme["lock_version"], "locked": True},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["items"][0]["locked"] is True

    removed = client.delete(
        f"{API}/schemes/{scheme_id}/items/2", params={"version": locked.json()["lock_version"]}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["item_count"] == 7


def test_optimize_over_http_keeps_locked(ready_session) -> None:
    client = ready_session["client"]
    scheme = ready_session["scheme"]
    scheme_id = scheme["id"]

    locked = client.put(
        f"{API}/schemes/{scheme_id}/items/8/lock",
        json={"version": scheme["lock_version"], "locked": True},
    ).json()
    locked_item_id = locked["items"][-1]["recommendation_item_id"]

    optimized = client.post(
        f"{API}/schemes/{scheme_id}/optimize",
        json={"version": locked["lock_version"], "target_count": 3, "risk_mode": "STEADY"},
    )
    assert optimized.status_code == 200, optimized.text
    body = optimized.json()
    assert body["item_count"] == 3
    assert locked_item_id in [item["recommendation_item_id"] for item in body["items"]]
    assert body["optimizer_stats"]["selected_count"] == 3


def test_scheme_rejects_target_above_policy_limit(ready_session) -> None:
    client = ready_session["client"]
    response = client.post(
        f"{API}/schemes",
        json={"recommendation_session_id": ready_session["session_id"], "target_count": 120},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "RULE_VIOLATION"
    assert body["error"]["details"]["application_limit"] == 96


def test_export_over_http_has_no_probability_claim(ready_session) -> None:
    client = ready_session["client"]
    response = client.get(f"{API}/schemes/{ready_session['scheme']['id']}/export")
    assert response.status_code == 200
    body = response.json()
    assert body["application_limit"] == 96
    assert len(body["rows"]) == 8
    # the disclaimer must explicitly refuse the probability framing
    assert "录取安全指数" in body["disclaimer"]
    assert "而非录取概率" in body["disclaimer"]


def test_unknown_scheme_returns_unified_404(ready_session) -> None:
    response = ready_session["client"].get(f"{API}/schemes/987654")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "details"}
