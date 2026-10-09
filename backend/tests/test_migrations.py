"""Phase 1 acceptance: migrations, schema parity with the ORM, and key constraints.

The parity test is the guardrail that keeps hand-written migrations and the
SQLAlchemy models from drifting apart as later phases add columns.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.common.enums import SCHEMAS
from app.db.base import metadata
from app.db.registry import ALL_MODELS

pytestmark = pytest.mark.integration


def test_all_four_schemas_exist(migrated_engine) -> None:
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(:names)"),
            {"names": list(SCHEMAS)},
        ).scalars()
        assert set(rows) == set(SCHEMAS)


def test_every_model_table_is_created(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    existing = {
        (schema, table)
        for schema in SCHEMAS
        for table in inspector.get_table_names(schema=schema)
    }
    expected = {(t.__table__.schema, t.__table__.name) for t in ALL_MODELS}
    assert expected - existing == set(), "models missing from the migrated database"
    assert existing - expected == set(), "migrated tables with no ORM model"


def test_migrations_match_orm_metadata(migrated_engine) -> None:
    """Autogenerate diff against the migrated DB must be empty.

    Uses alembic's own comparison so this stays authoritative as models evolve.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "include_schemas": True,
                # The migration-history table is alembic's, not part of the model
                "include_name": lambda name, type_, parent_names: not (
                    type_ == "table" and name == "alembic_version"
                ),
            },
        )
        diff = compare_metadata(context, metadata)

    assert diff == [], f"schema drift between migrations and ORM models: {diff}"


def test_application_limit_is_stored_per_cycle(db_session) -> None:
    """The 96 limit must be data, never a constant."""
    from app.admission.models import AdmissionCycle

    cycle = AdmissionCycle(
        province_code="37",
        exam_year=2026,
        exam_category="SUMMER_REGULAR",
        batch_code="REGULAR_BATCH_1",
        round_no=1,
        program_level="UNDERGRAD",
        application_limit=96,
        policy_version="sd-2026-v1",
    )
    db_session.add(cycle)
    db_session.flush()
    assert cycle.id is not None

    # A second cycle in a different year may carry a different limit
    other = AdmissionCycle(
        province_code="37",
        exam_year=2027,
        exam_category="SUMMER_REGULAR",
        batch_code="REGULAR_BATCH_1",
        round_no=1,
        program_level="UNDERGRAD",
        application_limit=100,
        policy_version="sd-2027-v1",
    )
    db_session.add(other)
    db_session.flush()
    assert other.application_limit == 100


def test_duplicate_cycle_scope_rejected(db_session) -> None:
    from app.admission.models import AdmissionCycle

    common = {
        "province_code": "37",
        "exam_year": 2026,
        "exam_category": "SUMMER_REGULAR",
        "batch_code": "REGULAR_BATCH_1",
        "round_no": 1,
        "program_level": "UNDERGRAD",
        "application_limit": 96,
    }
    db_session.add(AdmissionCycle(**common))
    db_session.flush()
    db_session.add(AdmissionCycle(**common))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_out_of_scope_cycle_rejected_by_check_constraint(db_session) -> None:
    """V1 must not silently accept 艺术类 / 提前批 cycles."""
    from app.admission.models import AdmissionCycle

    db_session.add(
        AdmissionCycle(
            province_code="37",
            exam_year=2026,
            exam_category="ART",
            batch_code="REGULAR_BATCH_1",
            round_no=1,
            program_level="UNDERGRAD",
            application_limit=96,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_profile_requires_score_or_rank(db_session) -> None:
    from app.admission.models import AdmissionCycle
    from app.candidate.models import Profile

    cycle = AdmissionCycle(
        exam_year=2026,
        exam_category="SUMMER_REGULAR",
        batch_code="REGULAR_BATCH_1",
        application_limit=96,
    )
    db_session.add(cycle)
    db_session.flush()

    db_session.add(Profile(cycle_id=cycle.id, exam_year=2026, subjects=["PHY", "CHM", "BIO"]))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_fail_item_can_never_be_pooled(db_session) -> None:
    """The DB itself forbids pooling a FAIL candidate."""
    from app.admission.models import AdmissionCycle, AdmissionPlan, DatasetSnapshot, ProgramSeries
    from app.candidate.models import PreferenceProfile, Profile
    from app.recommendation.models import ModelVersion, RecommendationItem, Session
    from app.reference.models import University

    cycle = AdmissionCycle(
        exam_year=2026, exam_category="SUMMER_REGULAR", batch_code="REGULAR_BATCH_1", application_limit=96
    )
    snapshot = DatasetSnapshot(code="demo-v1", label="DEMO", status="PUBLISHED", is_demo=True)
    uni = University(
        ministry_code="DEMO001", name="演示大学", province_code="37", province_name="山东省"
    )
    db_session.add_all([cycle, snapshot, uni])
    db_session.flush()
    series = ProgramSeries(series_key="demo-1", canonical_name="演示专业", university_id=uni.id)
    db_session.add(series)
    db_session.flush()
    plan = AdmissionPlan(
        cycle_id=cycle.id,
        series_id=series.id,
        snapshot_id=snapshot.id,
        plan_name="演示专业",
        plan_count=10,
        tuition_fee=5000,
    )
    profile = Profile(cycle_id=cycle.id, exam_year=2026, rank=1000, subjects=["PHY", "CHM", "BIO"])
    model = ModelVersion(code="acs-v1", label="ACS v1", parameters={})
    db_session.add_all([plan, profile, model])
    db_session.flush()
    pref = PreferenceProfile(profile_id=profile.id, version=1)
    db_session.add(pref)
    db_session.flush()
    session = Session(
        profile_id=profile.id,
        preference_profile_id=pref.id,
        cycle_id=cycle.id,
        snapshot_id=snapshot.id,
        model_version_id=model.id,
        profile_version=profile.version,
        preference_version=pref.version,
        policy_version="sd-2026-v1",
        model_version="acs-v1",
    )
    db_session.add(session)
    db_session.flush()

    db_session.add(
        RecommendationItem(session_id=session.id, plan_id=plan.id, eligibility="FAIL", in_candidate_pool=True)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
