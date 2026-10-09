"""Phase 2 acceptance: Profiles / Attributes / Preferences / Catalog / Meta APIs."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.admission.models import (
    AdmissionPlan,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.reference.models import Major, University

pytestmark = pytest.mark.integration

API = "/api/v1"


# ---------------- fixtures ----------------


@pytest.fixture()
def snapshot(db_session):
    row = DatasetSnapshot(code="DEMO-V1", label="DEMO 数据集", status="PUBLISHED", is_demo=True)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def catalog_seed(db_session, cycle, snapshot):
    """One 院校 + 专业 + series + plan with a 物理和化学 requirement."""
    university = University(
        ministry_code="DEMO1042",
        name="演示海洋大学",
        short_name="演示海大",
        province_code="37",
        province_name="山东省",
        city_code="370200",
        city_name="青岛市",
        is_985=True,
        is_211=True,
        is_double_first_class=True,
    )
    major = Major(code="0809", name="计算机类", level="MAJOR_CLASS", discipline_code="08")
    db_session.add_all([university, major])
    db_session.flush()

    rule_set = SubjectRuleSet(code="PHY+CHM", mode="ALL", raw_text="物理和化学(2门科目考生均须选考)")
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

    series = ProgramSeries(
        series_key="demo-ocean-cs",
        canonical_name="计算机类",
        university_id=university.id,
        major_id=major.id,
        cooperative="NONE",
        first_year=2023,
        last_year=2025,
    )
    db_session.add(series)
    db_session.flush()

    plan = AdmissionPlan(
        cycle_id=cycle.id,
        series_id=series.id,
        subject_rule_set_id=rule_set.id,
        snapshot_id=snapshot.id,
        plan_name="计算机类",
        plan_count=40,
        tuition_fee=5800,
        duration_years=4,
        is_demo=True,
    )
    db_session.add(plan)
    db_session.flush()
    return {"university": university, "major": major, "series": series, "plan": plan}


# ---------------- meta ----------------


def test_health_endpoint(api_client) -> None:
    response = api_client.get(f"{API}/meta/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"


def test_admission_cycle_exposes_application_limit(api_client, cycle) -> None:
    """The volunteer limit must be readable policy data, not a constant."""
    response = api_client.get(f"{API}/meta/admission-cycles")
    assert response.status_code == 200
    cycles = response.json()
    assert [c["application_limit"] for c in cycles] == [96]
    assert cycles[0]["exam_year"] == 2026


def test_dataset_meta_flags_demo_data(api_client, cycle, snapshot, catalog_seed) -> None:
    response = api_client.get(f"{API}/meta/dataset", params={"cycle_id": cycle.id})
    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert body["cycle"]["application_limit"] == 96
    assert body["plan_count"] == 1
    assert body["university_count"] == 1
    assert any("DEMO" in w for w in body["warnings"])


# ---------------- profiles ----------------


def test_create_profile_minimal(api_client, cycle) -> None:
    response = api_client.post(
        f"{API}/profiles",
        json={"cycle_id": cycle.id, "rank": 12000, "subjects": ["PHY", "CHM", "BIO"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rank"] == 12000
    assert body["version"] == 1
    assert body["exam_year"] == 2026
    assert body["subjects"] == ["PHY", "CHM", "BIO"]


def test_create_profile_requires_score_or_rank(api_client, cycle) -> None:
    response = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "subjects": ["PHY", "CHM", "BIO"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_profile_rejects_unknown_subject(api_client, cycle) -> None:
    response = api_client.post(
        f"{API}/profiles",
        json={"cycle_id": cycle.id, "rank": 100, "subjects": ["PHY", "CHM", "ASTRO"]},
    )
    assert response.status_code == 422


def test_create_profile_unknown_cycle_returns_404(api_client) -> None:
    response = api_client.post(
        f"{API}/profiles", json={"cycle_id": 999999, "rank": 100, "subjects": ["PHY", "CHM", "BIO"]}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_profile_update_is_versioned_and_optimistic(api_client, cycle) -> None:
    created = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 5000, "subjects": ["PHY", "CHM", "GEO"]}
    ).json()
    profile_id = created["id"]

    ok = api_client.patch(f"{API}/profiles/{profile_id}", json={"rank": 4800, "expected_version": 1})
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.json()["rank"] == 4800

    stale = api_client.patch(f"{API}/profiles/{profile_id}", json={"rank": 4700, "expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCHEME_VERSION_CONFLICT"


def test_profile_cannot_drop_both_score_and_rank(api_client, cycle) -> None:
    created = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 5000, "subjects": ["PHY", "CHM", "GEO"]}
    ).json()
    # only rank exists; clearing it would leave the profile meaningless
    response = api_client.patch(f"{API}/profiles/{created['id']}", json={"score": None})
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert response.json()["rank"] == 5000


def test_attributes_roundtrip(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 900, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()

    put = api_client.put(
        f"{API}/profiles/{profile['id']}/attributes",
        json={"attr_key": "COLOR_VISION", "value_type": "BOOL", "value_bool": False, "source": "VERIFIED"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["value_bool"] is False

    # upsert on the same key updates instead of duplicating
    api_client.put(
        f"{API}/profiles/{profile['id']}/attributes",
        json={"attr_key": "COLOR_VISION", "value_type": "BOOL", "value_bool": True},
    )
    listed = api_client.get(f"{API}/profiles/{profile['id']}/attributes").json()
    assert len(listed) == 1
    assert listed[0]["value_bool"] is True

    deleted = api_client.delete(f"{API}/profiles/{profile['id']}/attributes/COLOR_VISION")
    assert deleted.status_code == 204
    assert api_client.get(f"{API}/profiles/{profile['id']}/attributes").json() == []


def test_attribute_value_type_must_match(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 900, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()
    response = api_client.put(
        f"{API}/profiles/{profile['id']}/attributes",
        json={"attr_key": "HEIGHT", "value_type": "INT"},
    )
    assert response.status_code == 422


# ---------------- preferences ----------------


def test_preference_versions_are_append_only(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 30000, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()

    first = api_client.post(
        f"{API}/profiles/{profile['id']}/preferences",
        json={"risk_mode": "STEADY", "max_tuition": 8000},
    )
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1
    assert first.json()["target_count"] == 96

    second = api_client.post(
        f"{API}/profiles/{profile['id']}/preferences",
        json={"risk_mode": "AGGRESSIVE", "max_tuition": 12000, "target_count": 60},
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2
    assert second.json()["target_count"] == 60

    versions = api_client.get(f"{API}/profiles/{profile['id']}/preferences").json()
    assert [v["version"] for v in versions] == [2, 1]
    # the old version is still readable (history is never overwritten)
    assert versions[1]["risk_mode"] == "STEADY"
    assert versions[0]["is_active"] is True
    assert versions[1]["is_active"] is False

    latest = api_client.get(f"{API}/profiles/{profile['id']}/preferences/latest").json()
    assert latest["version"] == 2


def test_preference_cannot_exceed_policy_limit(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 30000, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()
    response = api_client.post(
        f"{API}/profiles/{profile['id']}/preferences",
        json={"target_count": 120},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RULE_VIOLATION"
    assert response.json()["error"]["details"]["application_limit"] == 96


def test_preference_dimension_children(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 30000, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()
    response = api_client.post(
        f"{API}/profiles/{profile['id']}/preferences",
        json={
            "risk_mode": "BALANCED",
            "exclude_cooperative": True,
            "major_preferences": [
                {"major_name": "临床医学", "polarity": "EXCLUDE", "is_hard_exclude": True, "weight": 100}
            ],
            "location_preferences": [{"province_code": "31", "province_name": "上海市", "weight": 80}],
            "university_preferences": [{"trait": "IS_985", "weight": 60}],
            "other_preferences": [{"dimension": "CAREER", "key": "POSTGRAD_RATE", "weight": 50}],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["exclude_cooperative"] is True
    assert body["major_preferences"][0]["is_hard_exclude"] is True
    assert body["location_preferences"][0]["province_code"] == "31"
    assert body["university_preferences"][0]["trait"] == "IS_985"
    assert body["other_preferences"][0]["dimension"] == "CAREER"


def test_preference_rejects_unknown_dimension_weight(api_client, cycle) -> None:
    profile = api_client.post(
        f"{API}/profiles", json={"cycle_id": cycle.id, "rank": 30000, "subjects": ["PHY", "CHM", "BIO"]}
    ).json()
    response = api_client.post(
        f"{API}/profiles/{profile['id']}/preferences",
        json={"dimension_weights": {"LUCK": 10}},
    )
    assert response.status_code == 422


def test_v1_scope_is_enforced_on_profile_creation(api_client, db_session) -> None:
    """Only SUMMER_REGULAR / REGULAR_BATCH_1 cycles may be used."""
    from app.admission.models import AdmissionCycle

    art_cycle = AdmissionCycle(
        province_code="37",
        exam_year=2026,
        exam_category="ART",
        batch_code="REGULAR_BATCH_1",
        application_limit=60,
    )
    # the CHECK constraint already blocks persisting such a cycle at all
    db_session.add(art_cycle)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# ---------------- catalog ----------------


def test_universities_and_majors_listing(api_client, cycle, snapshot, catalog_seed) -> None:
    universities = api_client.get(f"{API}/catalog/universities", params={"search": "海洋"}).json()
    assert universities["total"] == 1
    assert universities["items"][0]["is_985"] is True

    majors = api_client.get(f"{API}/catalog/majors", params={"discipline_code": "08"}).json()
    assert majors["total"] == 1
    assert majors["items"][0]["name"] == "计算机类"


def test_plans_listing_includes_subject_rule_dnf(api_client, cycle, snapshot, catalog_seed) -> None:
    response = api_client.get(f"{API}/catalog/plans", params={"cycle_id": cycle.id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["program_label"] == "计算机类 · 演示海洋大学"
    assert item["tuition_fee"] == 5800
    assert item["is_demo"] is True
    assert item["subject_rule"]["mode"] == "ALL"
    assert item["subject_rule"]["groups"] == [["PHY", "CHM"]]


def test_plan_detail_includes_requirements(api_client, cycle, snapshot, catalog_seed) -> None:

    plan_id = catalog_seed["plan"].id
    detail = api_client.get(f"{API}/catalog/plans/{plan_id}").json()
    assert detail["plan_id"] == plan_id
    assert detail["requirements"] == []


def test_plan_search_and_tuition_filter(api_client, cycle, snapshot, catalog_seed) -> None:
    assert api_client.get(f"{API}/catalog/plans", params={"cycle_id": cycle.id, "search": "海洋"}).json()[
        "total"
    ] == 1
    assert api_client.get(
        f"{API}/catalog/plans", params={"cycle_id": cycle.id, "max_tuition": 5000}
    ).json()["total"] == 0
    assert api_client.get(
        f"{API}/catalog/plans", params={"cycle_id": cycle.id, "province_code": "31"}
    ).json()["total"] == 0


def test_missing_plan_returns_unified_error(api_client) -> None:
    response = api_client.get(f"{API}/catalog/plans/987654")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "details"}


# ---------------- score → rank ----------------


def test_score_to_rank_uses_published_segments(api_client, db_session, cycle) -> None:
    db_session.add_all(
        [
            ScoreRankSegment(
                exam_year=2026, exam_category="SUMMER_REGULAR", score=600, same_score_count=800,
                cumulative_rank=20000,
            ),
            ScoreRankSegment(
                exam_year=2026, exam_category="SUMMER_REGULAR", score=599, same_score_count=810,
                cumulative_rank=20810,
            ),
            ExamYearStats(
                province_code="37", exam_year=2026, exam_category="SUMMER_REGULAR",
                ranked_candidates=300000,
            ),
        ]
    )
    db_session.flush()

    response = api_client.post(f"{API}/catalog/score-to-rank", json={"cycle_id": cycle.id, "score": 600})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rank"] == 20000
    assert body["source"] == "SEGMENT"
    assert body["ranked_candidates"] == 300000


def test_score_to_rank_falls_back_to_nearest_and_says_so(api_client, db_session, cycle) -> None:
    db_session.add(
        ScoreRankSegment(
            exam_year=2026, exam_category="SUMMER_REGULAR", score=605, same_score_count=700,
            cumulative_rank=18000,
        )
    )
    db_session.flush()

    response = api_client.post(f"{API}/catalog/score-to-rank", json={"cycle_id": cycle.id, "score": 600})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "INTERPOLATED"
    assert body["rank"] == 18000
    assert "无一分一段记录" in body["note"]


def test_score_to_rank_without_data_is_a_clear_error(api_client, cycle) -> None:
    response = api_client.post(f"{API}/catalog/score-to-rank", json={"cycle_id": cycle.id, "score": 600})
    assert response.status_code == 400
    assert "位次" in response.json()["error"]["message"]


# ---------------- OpenAPI ----------------


def test_openapi_schema_is_available_and_unified(api_client) -> None:
    schema = api_client.get("/openapi.json").json()
    assert schema["info"]["title"]
    assert "录取安全指数" in schema["info"]["description"]
    # documented error model present on write endpoints
    assert "/api/v1/profiles" in schema["paths"]
    assert "/api/v1/catalog/score-to-rank" in schema["paths"]


def test_docs_page_served(api_client) -> None:
    assert api_client.get("/docs").status_code == 200
