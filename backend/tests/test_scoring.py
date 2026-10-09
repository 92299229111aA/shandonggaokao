"""Phase 4 acceptance: ACS / Confidence / Preference.

Pure unit tests, no database. Covers the spec's mandatory scenarios:
  * 只有 1 年历史 -> Confidence 自动降低
  * 招生人数从 50 骤降到 5 -> ACS 降低
  * 历史位次剧烈波动 -> Confidence 下降（而不是把 ACS 当成危险分）
  * 裸位次跨年比较被禁止（必须用位次百分位）
  * Preference 缺失维度从分母剔除
"""

from __future__ import annotations

import math

import pytest

from app.common.enums import RiskBucket
from app.scoring.acs import ACSService, confidence_label
from app.scoring.preference import (
    DEFAULT_DIMENSION_WEIGHTS,
    LocationSelector,
    MajorSelector,
    PreferenceInput,
    PreferenceService,
    PreferenceWeights,
    UniversitySelector,
    desire_band,
)
from app.scoring.types import ACSParameters, HistoryPoint

CANDIDATE_RANK = 20000
POPULATION = 300000


def history(
    year: int, min_rank: int, *, plan: int = 40, population: int = POPULATION, **kwargs
) -> HistoryPoint:
    return HistoryPoint(
        exam_year=year,
        min_rank=min_rank,
        ranked_candidates=population,
        plan_count=plan,
        source_quality=kwargs.pop("source_quality", "OFFICIAL"),
        **kwargs,
    )


# ================= ACS 公式 =================


def test_parameters_match_published_formula() -> None:
    params = ACSParameters()
    assert params.half_life_years == 1.5
    assert params.decay_lambda == pytest.approx(math.log(2) / 1.5)
    assert params.d_clip == 0.30
    assert params.history_gain == 35.0
    assert params.history_scale == 0.07
    assert params.plan_gain == 7.0
    assert params.plan_scale == 0.40
    assert params.trend_gain == 6.0
    assert params.trend_scale == 0.04
    assert params.weights_sum() == pytest.approx(1.0)


def test_equal_rank_and_population_gives_neutral_history_acs() -> None:
    """考生位次百分比与历史完全一致 -> d=0 -> HistoryACS=50。"""
    # history min_rank/population == candidate rank/population
    service = ACSService()
    evaluation = service.evaluate(
        candidate_rank=CANDIDATE_RANK,
        current_ranked_candidates=POPULATION,
        history=[history(2025, CANDIDATE_RANK)],
    )
    assert evaluation.weighted_mean_d == pytest.approx(0.0)
    assert evaluation.history_acs == pytest.approx(50.0)


def test_candidate_better_than_history_is_safer() -> None:
    service = ACSService()
    # candidate rank 10000 vs history min_rank 30000 -> candidate is safer
    better = service.evaluate(
        candidate_rank=10000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000)],
    )
    worse = service.evaluate(
        candidate_rank=60000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000)],
    )
    assert better.base_acs > 50 > worse.base_acs
    assert better.acs > worse.acs


def test_d_is_clipped() -> None:
    service = ACSService()
    evaluation = service.evaluate(
        candidate_rank=1, current_ranked_candidates=POPULATION, history=[history(2025, POPULATION)]
    )
    # ln(q_y/q_c) would be ~12.6 without clipping
    assert evaluation.weighted_mean_d == pytest.approx(0.30)


def test_history_acs_uses_tanh_saturation() -> None:
    service = ACSService()
    evaluation = service.evaluate(
        candidate_rank=1000, current_ranked_candidates=POPULATION, history=[history(2025, 100000)]
    )
    assert evaluation.history_acs <= 85.0  # 50 + 35 * tanh(...) <= 85
    assert evaluation.history_acs > 80.0


def test_acs_bounds_are_respected() -> None:
    service = ACSService()
    extreme_safe = service.evaluate(
        candidate_rank=1,
        current_ranked_candidates=POPULATION,
        history=[history(2025, POPULATION), history(2024, POPULATION), history(2023, POPULATION)],
        plan_current=500,
    )
    extreme_reach = service.evaluate(
        candidate_rank=POPULATION,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 1), history(2024, 1), history(2023, 1)],
        plan_current=1,
    )
    assert 0 <= extreme_reach.acs <= 100
    assert 0 <= extreme_safe.acs <= 100
    assert extreme_safe.acs > extreme_reach.acs


def test_bare_rank_comparison_is_rejected() -> None:
    """跨年必须做位次百分位标准化：缺少当年考生人数时必须报错，不能拿裸位次比。"""
    service = ACSService()
    with pytest.raises(ValueError, match="current_ranked_candidates"):
        service.evaluate(
            candidate_rank=CANDIDATE_RANK, current_ranked_candidates=None, history=[history(2025, 30000)]
        )
    with pytest.raises(ValueError, match="candidate_rank"):
        service.evaluate(
            candidate_rank=None, current_ranked_candidates=POPULATION, history=[history(2025, 30000)]
        )


def test_population_change_is_normalised() -> None:
    """同一历史位次在不同考生规模下含义不同：位次百分位会被正确标准化。"""
    service = ACSService()
    # candidate is at 10% of population
    small_year = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=300000,  # 10%
        history=[history(2025, 30000, population=600000)],  # history at 5% -> harder
    )
    big_year = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=300000,  # 10%
        history=[history(2025, 60000, population=600000)],  # history at 10% -> equal
    )
    assert small_year.weighted_mean_d < big_year.weighted_mean_d


# ================= 时间衰减 =================


def test_recent_year_dominates_older_years() -> None:
    service = ACSService()
    # old year very hard, recent year easy -> result driven by the recent one
    evaluation = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 90000), history(2021, 5000)],
    )
    neutral = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000)],
    )
    assert evaluation.weighted_mean_d > neutral.weighted_mean_d


def test_decay_weight_halves_after_half_life() -> None:
    params = ACSParameters()
    assert math.exp(-params.decay_lambda * 0) == pytest.approx(1.0)
    assert math.exp(-params.decay_lambda * 1.5) == pytest.approx(0.5, abs=1e-9)


# ================= 计划调整 =================


def test_plan_expansion_increases_acs_and_reduction_decreases_it() -> None:
    service = ACSService()
    common = {"candidate_rank": 30000, "current_ranked_candidates": POPULATION}
    history_points = [history(2025, 35000, plan=50), history(2024, 35000, plan=50)]

    expanded = service.evaluate(history=history_points, plan_current=120, **common)
    stable = service.evaluate(history=history_points, plan_current=50, **common)
    shrunk = service.evaluate(history=history_points, plan_current=5, **common)

    assert expanded.plan_adjustment > 0
    assert stable.plan_adjustment == pytest.approx(0.0)
    assert shrunk.plan_adjustment < 0
    assert expanded.acs > stable.acs > shrunk.acs


def test_plan_collapse_from_50_to_5_lowers_acs() -> None:
    """规格必测场景：招生人数从 50 骤降到 5，ACS 应降低。"""
    service = ACSService()
    points = [history(2025, 30000, plan=50), history(2024, 30000, plan=50), history(2023, 30000, plan=50)]
    before = service.evaluate(
        candidate_rank=30000, current_ranked_candidates=POPULATION, history=points, plan_current=50
    )
    after = service.evaluate(
        candidate_rank=30000, current_ranked_candidates=POPULATION, history=points, plan_current=5
    )
    assert after.acs < before.acs
    assert after.plan_adjustment < -3.0  # a 10x reduction is a large penalty


def test_plan_adjustment_without_history_is_zero() -> None:
    evaluation = ACSService().evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, plan=0)],
        plan_current=50,
    )
    assert evaluation.plan_adjustment == 0.0


# ================= 趋势 =================


def test_trend_detects_worsening_competition() -> None:
    service = ACSService()
    # q_y decreases each year (min_rank gets smaller) -> competition intensifying
    worsening = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2023, 45000), history(2024, 35000), history(2025, 25000)],
    )
    improving = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2023, 25000), history(2024, 35000), history(2025, 45000)],
    )
    assert worsening.trend_adjustment < 0
    assert improving.trend_adjustment > 0


def test_trend_requires_two_distinct_years() -> None:
    service = ACSService()
    single = service.evaluate(
        candidate_rank=30000, current_ranked_candidates=POPULATION, history=[history(2025, 30000)]
    )
    assert single.trend_adjustment == 0.0

    duplicate_years = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000), history(2025, 40000)],
    )
    assert duplicate_years.trend_adjustment == 0.0


# ================= Confidence =================


def test_single_year_history_lowers_confidence() -> None:
    """规格必测场景：历史数据只有 1 年时 Confidence 自动降低。"""
    service = ACSService()
    one_year = service.evaluate(
        candidate_rank=30000, current_ranked_candidates=POPULATION, history=[history(2025, 30000)]
    )
    three_years = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000), history(2024, 30000), history(2023, 30000)],
    )
    assert one_year.confidence < three_years.confidence
    assert one_year.confidence_parts.history_coverage == pytest.approx(1 / 3)
    assert three_years.confidence_parts.history_coverage == pytest.approx(1.0)
    assert "只有 1 年历史数据" in " ".join(one_year.warnings)


def test_volatile_history_lowers_confidence_not_acs_only() -> None:
    """规格必测场景：历史位次剧烈波动时 Confidence 下降。"""
    service = ACSService()
    stable = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000), history(2024, 30500), history(2023, 29800)],
    )
    volatile = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 8000), history(2024, 90000), history(2023, 15000)],
    )
    assert volatile.confidence < stable.confidence
    assert volatile.confidence_parts.volatility < stable.confidence_parts.volatility


def test_confidence_shrinks_acs_toward_50() -> None:
    """ACS = 50 + (BaseACS-50)*C：数据越不可信，越向 50 收缩。"""
    service = ACSService()
    # Very strong evidence
    strong = service.evaluate(
        candidate_rank=1000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 100000), history(2024, 100000), history(2023, 100000)],
        plan_current=40,
    )
    # Same underlying edge but only one fuzzy, third-party, tiny-plan year
    weak = service.evaluate(
        candidate_rank=1000,
        current_ranked_candidates=POPULATION,
        history=[
            history(2025, 100000, plan=2, source_quality="THIRD_PARTY", series_match_quality="FUZZY"),
        ],
        plan_current=2,
    )
    assert weak.confidence < strong.confidence
    assert abs(weak.acs - 50) < abs(strong.acs - 50)


def test_fuzzy_series_match_lowers_confidence_and_warns() -> None:
    service = ACSService()
    exact = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, series_match_quality="EXACT")],
    )
    fuzzy = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, series_match_quality="FUZZY")],
    )
    assert fuzzy.confidence_parts.series_match < exact.confidence_parts.series_match
    assert any("序列匹配" in w for w in fuzzy.warnings)


def test_demo_source_quality_lowers_confidence() -> None:
    service = ACSService()
    official = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, source_quality="OFFICIAL")],
    )
    demo = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, source_quality="DEMO")],
    )
    assert demo.confidence < official.confidence


def test_small_plan_count_lowers_plan_quality() -> None:
    service = ACSService()
    big = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, plan=60)],
    )
    small = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000, plan=2)],
    )
    assert small.confidence_parts.plan_quality < big.confidence_parts.plan_quality


def test_stale_dataset_lowers_freshness() -> None:
    service = ACSService()
    fresh = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000)],
        data_year=2025,
    )
    stale = service.evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000)],
        data_year=2027,
    )
    assert stale.confidence_parts.freshness < fresh.confidence_parts.freshness


def test_no_history_gives_neutral_acs_with_warning() -> None:
    evaluation = ACSService().evaluate(
        candidate_rank=30000, current_ranked_candidates=POPULATION, history=[]
    )
    assert evaluation.acs == 50.0
    assert evaluation.confidence == 0.0
    assert evaluation.history_points == 0
    assert any("没有可用" in w or "没有任何" in w for w in evaluation.warnings)


def test_unusable_history_points_are_dropped_and_reported() -> None:
    evaluation = ACSService().evaluate(
        candidate_rank=30000,
        current_ranked_candidates=POPULATION,
        history=[
            history(2025, 30000),
            HistoryPoint(exam_year=2024, min_rank=None, ranked_candidates=POPULATION),
            HistoryPoint(exam_year=2023, min_rank=30000, ranked_candidates=None),
        ],
    )
    assert evaluation.history_points == 1
    assert any("已从 ACS 计算中剔除" in w for w in evaluation.warnings)


# ================= 风险桶 =================


@pytest.mark.parametrize(
    ("acs", "bucket"),
    [
        (10.0, RiskBucket.HIGH_REACH),
        (35.0, RiskBucket.REACH),
        (50.0, RiskBucket.BORDER),
        (70.0, RiskBucket.STABLE),
        (90.0, RiskBucket.SAFE),
    ],
)
def test_risk_bucket_mapping(acs: float, bucket: RiskBucket) -> None:
    assert ACSService.risk_bucket(acs) == bucket


def test_confidence_labels() -> None:
    assert confidence_label(0.9) == "HIGH"
    assert confidence_label(0.6) == "MEDIUM"
    assert confidence_label(0.2) == "LOW"


# ================= 可解释性 / 确定性 =================


def test_evaluation_is_deterministic() -> None:
    service = ACSService()
    kwargs = {
        "candidate_rank": 25000,
        "current_ranked_candidates": POPULATION,
        "history": [history(2025, 30000), history(2024, 33000), history(2023, 31000)],
        "plan_current": 45,
    }
    first = service.evaluate(**kwargs)
    second = service.evaluate(**kwargs)
    assert first.acs == second.acs
    assert first.as_dict() == second.as_dict()


def test_explanation_contains_disclaimer_and_parts() -> None:
    evaluation = ACSService().evaluate(
        candidate_rank=25000,
        current_ranked_candidates=POPULATION,
        history=[history(2025, 30000), history(2024, 33000)],
        plan_current=45,
    )
    payload = evaluation.as_dict()
    assert "不是录取概率" in payload["disclaimer"]
    assert set(payload["confidence_parts"]) == {
        "history_coverage",
        "freshness",
        "series_match",
        "source_quality",
        "volatility",
        "plan_quality",
    }
    assert payload["years_used"] == [2024, 2025]


# ================= Preference =================


def test_default_weights_match_spec() -> None:
    assert DEFAULT_DIMENSION_WEIGHTS == {
        "MAJOR": 0.35,
        "UNIVERSITY": 0.15,
        "LOCATION": 0.15,
        "ACADEMIC": 0.10,
        "CAREER": 0.10,
        "TUITION": 0.10,
        "PERSONAL": 0.05,
    }
    assert sum(DEFAULT_DIMENSION_WEIGHTS.values()) == pytest.approx(1.0)


def test_missing_dimensions_are_dropped_from_denominator() -> None:
    """规格硬性要求：缺失维度从分母剔除，不能按 0 分处理。"""
    service = PreferenceService()
    candidate = PreferenceInput(
        major_id=1, major_names=("计算机类",), major_text="计算机类", university_id=10
    )
    weights = PreferenceWeights(
        major_includes=(MajorSelector(major_id=1, weight=90.0),),
    )
    evaluation = service.evaluate(candidate=candidate, weights=weights)

    # Only MAJOR is available -> score is exactly that dimension's score
    assert evaluation.available_dimensions == ("MAJOR",)
    assert evaluation.score == pytest.approx(90.0)
    assert evaluation.score != pytest.approx(90.0 * 0.35)


def test_missing_dimension_does_not_zero_the_score() -> None:
    """如果按 0 分处理，缺少信息会把所有志愿都压到很低——真实行为应是中性。"""
    service = PreferenceService()
    no_info = service.evaluate(candidate=PreferenceInput(), weights=PreferenceWeights())
    assert no_info.score == pytest.approx(50.0)
    assert no_info.available_dimensions == ()


def test_weighted_mean_across_two_dimensions() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(
        major_id=1, major_text="计算机类", major_names=("计算机类",), province_code="31"
    )
    weights = PreferenceWeights(
        major_includes=(MajorSelector(major_id=1, weight=100.0),),
        location_includes=(LocationSelector(province_code="31", weight=50.0),),
    )
    evaluation = service.evaluate(candidate=candidate, weights=weights)
    assert set(evaluation.available_dimensions) == {"MAJOR", "LOCATION"}
    expected = (0.35 * 100.0 + 0.15 * (50.0 * 0.85)) / (0.35 + 0.15)
    assert evaluation.score == pytest.approx(expected, rel=1e-6)


def test_hard_exclude_major_returns_excluded() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(major_id=7, major_text="临床医学", major_names=("临床医学",))
    weights = PreferenceWeights(major_excludes=(MajorSelector(major_id=7),))
    evaluation = service.evaluate(candidate=candidate, weights=weights)
    assert evaluation.excluded is True
    assert evaluation.score == 0.0
    assert "专业" in (evaluation.exclude_reason or "")


def test_hard_exclude_location_and_university() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(province_code="31", city_code="310100", university_id=5)
    by_city = service.evaluate(
        candidate=candidate,
        weights=PreferenceWeights(location_excludes=(LocationSelector(city_code="310100"),)),
    )
    assert by_city.excluded is True

    by_university = service.evaluate(
        candidate=candidate,
        weights=PreferenceWeights(university_excludes=(UniversitySelector(university_id=5),)),
    )
    assert by_university.excluded is True


def test_university_trait_selector_matches_flags() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(
        university_id=1, university_traits={"IS_985": True, "IS_211": True}
    )
    evaluation = service.evaluate(
        candidate=candidate,
        weights=PreferenceWeights(university_includes=(UniversitySelector(trait="IS_985", weight=88.0),)),
    )
    assert evaluation.dimension("UNIVERSITY").score == pytest.approx(88.0)


def test_tuition_dimension_prefers_within_budget() -> None:
    service = PreferenceService()
    weights = PreferenceWeights()
    within = service.evaluate(
        candidate=PreferenceInput(tuition_fee=5000, tuition_budget=10000), weights=weights
    )
    over = service.evaluate(
        candidate=PreferenceInput(tuition_fee=30000, tuition_budget=10000), weights=weights
    )
    assert within.dimension("TUITION").score > over.dimension("TUITION").score
    assert over.dimension("TUITION").score >= 0.0


def test_custom_dimension_weights_are_respected() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(major_id=1, major_text="计算机类", province_code="31")
    weights = PreferenceWeights(
        weights={"MAJOR": 0.9, "LOCATION": 0.1},
        major_includes=(MajorSelector(major_id=1, weight=100.0),),
        location_includes=(LocationSelector(province_code="31", weight=30.0),),
    )
    evaluation = service.evaluate(candidate=candidate, weights=weights)
    expected = (0.9 * 100.0 + 0.1 * (30.0 * 0.85)) / 1.0
    assert evaluation.score == pytest.approx(expected, rel=1e-6)


def test_zero_weight_dimension_is_ignored() -> None:
    service = PreferenceService()
    candidate = PreferenceInput(major_id=1, major_text="计算机类")
    weights = PreferenceWeights(
        weights={"MAJOR": 0.0},
        major_includes=(MajorSelector(major_id=1, weight=100.0),),
    )
    evaluation = service.evaluate(candidate=candidate, weights=weights)
    assert evaluation.available_dimensions == ()
    assert evaluation.score == pytest.approx(50.0)


def test_preference_score_bounds() -> None:
    service = PreferenceService()
    evaluation = service.evaluate(
        candidate=PreferenceInput(tuition_fee=0, tuition_budget=1, personal_score=100.0),
        weights=PreferenceWeights(),
    )
    assert 0.0 <= evaluation.score <= 100.0


def test_desire_band() -> None:
    assert desire_band(0.0) == 0
    assert desire_band(2.999) == 0
    assert desire_band(3.0) == 1
    assert desire_band(99.0) == 33
    assert desire_band(100.0) == 33
    # higher preference never yields a lower band
    assert desire_band(90.0) > desire_band(60.0) > desire_band(30.0)


def test_preference_explanation_is_serialisable() -> None:
    import json

    evaluation = PreferenceService().evaluate(
        candidate=PreferenceInput(major_id=1, major_text="计算机类", province_code="31"),
        weights=PreferenceWeights(major_includes=(MajorSelector(major_id=1, weight=80.0),)),
    )
    assert json.loads(json.dumps(evaluation.as_dict(), ensure_ascii=False))["score"] == pytest.approx(80.0)
