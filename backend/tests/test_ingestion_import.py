"""Official-data import interface tests.

This is the seam the user's own files will come through, so the tests focus on the
behaviour that matters when real data arrives: dry-run safety, refusal to guess,
idempotent re-import, and useful error messages.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import func, select

from app.admission.models import (
    AdmissionOutcome,
    AdmissionPlan,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SourceDocument,
)
from app.common.errors import RuleViolationError
from app.ingestion.importer import (
    OFFICIAL_QUALITY,
    TEMPLATES,
    ImportKind,
    IngestionService,
)
from app.reference.models import University

pytestmark = pytest.mark.integration


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


@pytest.fixture()
def snapshot(db_session):
    row = DatasetSnapshot(code="OFFICIAL-2026", label="官方数据", status="PUBLISHED", is_demo=False)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def university(db_session):
    row = University(
        ministry_code="10001", name="测试大学", province_code="11", province_name="北京市",
        city_code="110108", city_name="北京市",
    )
    db_session.add(row)
    db_session.flush()
    return row


# ================= 一分一段表 =================


def test_score_rank_dry_run_writes_nothing(db_session) -> None:
    content = csv_bytes("exam_year,score,same_score_count,cumulative_rank\n2025,600,2700,20000\n")
    report = IngestionService(db_session).import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="sd.csv",
        snapshot_id=None, cycle_id=None, dry_run=True,
    )
    assert report.ok, report.as_dict()
    assert report.dry_run is True
    assert report.rows_accepted == 1
    assert report.created == 0
    assert (
        db_session.execute(select(func.count()).select_from(ScoreRankSegment)).scalar_one() == 0
    )


def test_score_rank_import_creates_rows_and_year_stats(db_session) -> None:
    content = csv_bytes(
        "exam_year,score,same_score_count,cumulative_rank\n"
        "2025,700,3,3\n"
        "2025,699,5,8\n"
        "2024,700,4,4\n"
    )
    report = IngestionService(db_session).import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="sd.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
    )
    assert report.ok, report.as_dict()
    assert report.created == 3
    assert report.source_document_id is not None

    # 考生总人数 comes from the last row of each year
    stats_2025 = db_session.execute(
        select(ExamYearStats).where(ExamYearStats.exam_year == 2025)
    ).scalar_one()
    assert stats_2025.ranked_candidates == 8
    stats_2024 = db_session.execute(
        select(ExamYearStats).where(ExamYearStats.exam_year == 2024)
    ).scalar_one()
    assert stats_2024.ranked_candidates == 4


def test_score_rank_marks_provenance_as_official(db_session) -> None:
    content = csv_bytes("exam_year,score,same_score_count,cumulative_rank\n2025,600,2700,20000\n")
    IngestionService(db_session).import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="sd.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
        publisher="山东省教育招生考试院",
    )
    document = db_session.execute(select(SourceDocument)).scalar_one()
    assert document.quality == OFFICIAL_QUALITY
    assert document.is_demo is False, "官方数据导入不得被标记为 DEMO"
    assert document.publisher == "山东省教育招生考试院"
    assert len(document.checksum_sha256) == 64


def test_score_rank_reimport_is_idempotent(db_session) -> None:
    content = csv_bytes("exam_year,score,same_score_count,cumulative_rank\n2025,600,2700,20000\n")
    service = IngestionService(db_session)
    first = service.import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="a.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
    )
    second = service.import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="a.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
    )
    assert first.created == 1 and first.updated == 0
    assert second.created == 0 and second.updated == 1
    assert db_session.execute(select(func.count()).select_from(ScoreRankSegment)).scalar_one() == 1


def test_score_rank_rejects_bad_rows_with_reasons(db_session) -> None:
    content = csv_bytes(
        "exam_year,score,same_score_count,cumulative_rank\n"
        "2025,900,3,3\n"          # score out of range
        "2025,600,abc,20000\n"    # not a number
        "2025,600,-1,20000\n"     # negative count
        "2025,600,2700,20000\n"   # good
        "2025,600,2700,21000\n"   # duplicate key
    )
    report = IngestionService(db_session).import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="bad.csv",
        snapshot_id=None, cycle_id=None, dry_run=True,
    )
    assert report.ok is False
    assert report.rows_total == 5
    assert report.rows_accepted == 1
    assert report.rows_rejected == 4
    codes = {issue.code for issue in report.errors}
    assert {"SCORE_OUT_OF_RANGE", "NOT_A_NUMBER", "INVALID_COUNTS", "DUPLICATE_KEY"} <= codes
    # every error carries the 1-based source row so a human can fix the file
    assert all(issue.row and issue.row >= 2 for issue in report.errors)


def test_score_rank_accepts_gbk_and_tab_separated(db_session) -> None:
    content = csv_bytes("exam_year\tscore\tsame_score_count\tcumulative_rank\n2025\t600\t2700\t20000\n", "gbk")
    report = IngestionService(db_session).import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="gbk.csv",
        snapshot_id=None, cycle_id=None, dry_run=True,
    )
    assert report.ok, report.as_dict()
    assert report.rows_accepted == 1


def test_score_rank_requires_the_right_columns(db_session) -> None:
    content = csv_bytes("year,point\n2025,600\n")
    with pytest.raises(RuleViolationError) as excinfo:
        IngestionService(db_session).import_file(
            kind=ImportKind.SCORE_RANK, content=content, filename="wrong.csv",
            snapshot_id=None, cycle_id=None, dry_run=True,
        )
    assert "缺少必需的列" in str(excinfo.value)
    assert excinfo.value.details["found"] == ["year", "point"]


# ================= 招生计划 =================


def test_plans_import_creates_series_and_plan(db_session, snapshot, cycle, university) -> None:
    content = csv_bytes(
        "院校名称,专业名称,计划数,学费,选科要求\n"
        "测试大学,计算机类,45,5800,物理和化学\n"
    )
    report = IngestionService(db_session).import_file(
        kind=ImportKind.PLANS, content=content, filename="plans.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    assert report.ok, report.as_dict()
    assert report.created == 1

    plan = db_session.execute(select(AdmissionPlan)).scalar_one()
    assert plan.plan_name == "计算机类"
    assert plan.plan_count == 45
    assert plan.tuition_fee == 5800
    assert plan.is_demo is False, "官方导入的计划不得被标记为 DEMO"
    assert plan.source_document_id is not None
    assert db_session.execute(select(func.count()).select_from(ProgramSeries)).scalar_one() == 1


def test_plans_import_refuses_unknown_university(db_session, snapshot, cycle) -> None:
    """不猜测创建院校 —— 院校基础数据必须单独导入。"""
    content = csv_bytes("院校名称,专业名称,计划数\n不存在的大学,计算机类,45\n")
    report = IngestionService(db_session).import_file(
        kind=ImportKind.PLANS, content=content, filename="plans.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=True,
    )
    assert report.ok is False
    assert report.errors[0].code == "UNKNOWN_UNIVERSITY"
    assert report.created == 0


def test_plans_import_reports_missing_tuition_as_warning(db_session, snapshot, cycle, university) -> None:
    content = csv_bytes("院校名称,专业名称,计划数,学费\n测试大学,数学类,40,\n")
    report = IngestionService(db_session).import_file(
        kind=ImportKind.PLANS, content=content, filename="plans.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=True,
    )
    assert report.ok is True
    assert any(issue.code == "MISSING_TUITION" for issue in report.warnings)


def test_plans_import_is_idempotent(db_session, snapshot, cycle, university) -> None:
    content = csv_bytes("院校名称,专业名称,计划数,学费\n测试大学,计算机类,45,5800\n")
    service = IngestionService(db_session)
    first = service.import_file(
        kind=ImportKind.PLANS, content=content, filename="p.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    second = service.import_file(
        kind=ImportKind.PLANS, content=content, filename="p.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    assert first.created == 1
    assert second.updated == 1 and second.created == 0
    assert db_session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one() == 1


def test_plans_import_without_snapshot_is_rejected(db_session, cycle, university) -> None:
    content = csv_bytes("院校名称,专业名称,计划数\n测试大学,计算机类,45\n")
    with pytest.raises(RuleViolationError, match="数据快照"):
        IngestionService(db_session).import_file(
            kind=ImportKind.PLANS, content=content, filename="p.csv",
            snapshot_id=None, cycle_id=cycle.id, dry_run=True,
        )


# ================= 投档结果 =================


def test_outcomes_import_links_to_existing_series(db_session, snapshot, cycle, university) -> None:
    plans_csv = csv_bytes("院校名称,专业名称,计划数,学费\n测试大学,计算机类,45,5800\n")
    service = IngestionService(db_session)
    service.import_file(
        kind=ImportKind.PLANS, content=plans_csv, filename="p.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )

    outcomes_csv = csv_bytes(
        "院校名称,专业名称,年份,最低位次,最低分\n"
        "测试大学,计算机类,2025,12000,640\n"
        "测试大学,计算机类,2024,13000,635\n"
    )
    report = service.import_file(
        kind=ImportKind.OUTCOMES, content=outcomes_csv, filename="o.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    assert report.ok, report.as_dict()
    assert report.created == 2

    outcomes = list(db_session.execute(select(AdmissionOutcome)).scalars())
    assert len(outcomes) == 2
    assert {row.exam_year for row in outcomes} == {2024, 2025}
    assert all(row.is_demo is False for row in outcomes)
    assert all(row.min_rank in {12000, 13000} for row in outcomes)


def test_outcomes_import_refuses_rows_without_a_series(db_session, snapshot, cycle) -> None:
    content = csv_bytes("院校名称,专业名称,年份,最低位次\n测试大学,计算机类,2025,12000\n")
    report = IngestionService(db_session).import_file(
        kind=ImportKind.OUTCOMES, content=content, filename="o.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=True,
    )
    assert report.ok is False
    assert report.errors[0].code == "UNKNOWN_SERIES"


def test_outcomes_reimport_updates_in_place(db_session, snapshot, cycle, university) -> None:
    plans_csv = csv_bytes("院校名称,专业名称,计划数,学费\n测试大学,计算机类,45,5800\n")
    service = IngestionService(db_session)
    service.import_file(
        kind=ImportKind.PLANS, content=plans_csv, filename="p.csv",
        snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    service.import_file(
        kind=ImportKind.OUTCOMES,
        content=csv_bytes("院校名称,专业名称,年份,最低位次\n测试大学,计算机类,2025,12000\n"),
        filename="o.csv", snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    corrected = service.import_file(
        kind=ImportKind.OUTCOMES,
        content=csv_bytes("院校名称,专业名称,年份,最低位次\n测试大学,计算机类,2025,11500\n"),
        filename="o.csv", snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    assert corrected.updated == 1 and corrected.created == 0
    outcome = db_session.execute(select(AdmissionOutcome)).scalar_one()
    assert outcome.min_rank == 11500, "修正后的文件应覆盖旧值"


# ================= 生命周期：导入后能被流水线使用 =================


def test_imported_data_feeds_the_pipeline(db_session, snapshot, cycle, university) -> None:
    """导入一分一段 + 计划 + 投档结果后，必须能直接生成候选池。"""
    from app.candidate.models import PreferenceProfile, Profile
    from app.recommendation.pipeline import RecommendationPipeline

    service = IngestionService(db_session)
    # 考生人数必须覆盖 ACS 要用的每一年，否则那一年的历史会被剔除
    # （这正是"缺少当年考生人数就不做裸位次比较"的保护）。
    service.import_file(
        kind=ImportKind.SCORE_RANK,
        content=csv_bytes(
            "exam_year,score,same_score_count,cumulative_rank\n"
            "2023,600,2600,19000\n2023,599,2600,21600\n"
            "2024,600,2650,19500\n2024,599,2650,22150\n"
            "2025,600,2700,20000\n2025,599,2700,22700\n"
            "2026,600,2700,20000\n2026,599,2700,22700\n"
        ),
        filename="sr.csv", snapshot_id=None, cycle_id=None, dry_run=False,
    )
    service.import_file(
        kind=ImportKind.PLANS,
        content=csv_bytes("院校名称,专业名称,计划数,学费\n测试大学,计算机类,45,5800\n"),
        filename="p.csv", snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )
    service.import_file(
        kind=ImportKind.OUTCOMES,
        content=csv_bytes(
            "院校名称,专业名称,年份,最低位次\n"
            "测试大学,计算机类,2025,15000\n"
            "测试大学,计算机类,2024,16000\n"
            "测试大学,计算机类,2023,17000\n"
        ),
        filename="o.csv", snapshot_id=snapshot.id, cycle_id=cycle.id, dry_run=False,
    )

    profile = Profile(
        cycle_id=cycle.id, exam_year=cycle.exam_year, rank=20000, score=600,
        subjects=["PHY", "CHM", "BIO"],
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, candidate_rank=20000, target_count=96
    )
    db_session.add(preference)
    db_session.flush()

    result = RecommendationPipeline(db_session).run(
        profile_id=profile.id, preference_id=preference.id
    )
    assert result.stats.plans_scanned == 1
    assert result.stats.pooled == 1, "导入的真实数据必须能进入候选池"
    bundle = next(b for b in result.bundles if b.poolable)
    assert bundle.acs is not None
    assert bundle.acs.history_points == 3, "三年历史都应参与 ACS"
    assert bundle.acs.confidence > 0


def test_same_file_content_is_recognised_as_one_source(db_session) -> None:
    """同一份文件重复上传不应产生两条来源记录。"""
    content = csv_bytes("exam_year,score,same_score_count,cumulative_rank\n2025,600,2700,20000\n")
    service = IngestionService(db_session)
    service.import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="first-name.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
    )
    service.import_file(
        kind=ImportKind.SCORE_RANK, content=content, filename="renamed.csv",
        snapshot_id=None, cycle_id=None, dry_run=False,
    )
    assert db_session.execute(select(func.count()).select_from(SourceDocument)).scalar_one() == 1


def test_templates_are_valid_csv() -> None:
    import csv as csv_module

    for name, text in TEMPLATES.items():
        reader = csv_module.DictReader(io.StringIO(text))
        assert reader.fieldnames, f"{name} 模板缺少表头"
        rows = list(reader)
        assert rows, f"{name} 模板应含一行示例数据"
