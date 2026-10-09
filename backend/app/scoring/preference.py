"""Preference Score: how much does the candidate actually want this plan?

Seven dimensions with default weights. The critical rule from the spec:

    缺失维度必须从分母剔除，不得当作 0 分。

So the score is a weighted *mean over available dimensions*, never a sum divided
by the full weight total.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.common.enums import PreferenceDimension, PreferencePolarity

# Published defaults (docs/ALGORITHM.md §9). Sums to 1.0.
DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    PreferenceDimension.MAJOR.value: 0.35,
    PreferenceDimension.UNIVERSITY.value: 0.15,
    PreferenceDimension.LOCATION.value: 0.15,
    PreferenceDimension.ACADEMIC.value: 0.10,
    PreferenceDimension.CAREER.value: 0.10,
    PreferenceDimension.TUITION.value: 0.10,
    PreferenceDimension.PERSONAL.value: 0.05,
}


@dataclass(frozen=True, slots=True)
class MajorSelector:
    """One way a user can point at a 专业: by id, by 专业类 code, or by name."""

    major_id: int | None = None
    discipline_code: str | None = None
    major_class_code: str | None = None
    name: str | None = None
    weight: float = 100.0

    def matches(self, *, major_id: int | None, discipline_code: str | None, names: Sequence[str]) -> bool:
        if self.major_id is not None and major_id is not None and self.major_id == major_id:
            return True
        if self.discipline_code and discipline_code and self.discipline_code == discipline_code:
            return True
        normalised = [n.replace(" ", "") for n in names if n]
        if self.name:
            needle = self.name.replace(" ", "")
            if any(needle in candidate for candidate in normalised):
                return True
        if self.major_class_code:
            needle = self.major_class_code.replace(" ", "")
            if any(candidate.startswith(needle) for candidate in normalised):
                return True
        return False


@dataclass(frozen=True, slots=True)
class LocationSelector:
    province_code: str | None = None
    city_code: str | None = None
    weight: float = 100.0

    def matches(self, *, province_code: str | None, city_code: str | None) -> bool:
        if self.city_code and city_code and self.city_code == city_code:
            return True
        return bool(self.province_code and province_code and self.province_code == province_code)


@dataclass(frozen=True, slots=True)
class UniversitySelector:
    university_id: int | None = None
    trait: str | None = None
    weight: float = 100.0

    def matches(
        self,
        *,
        university_id: int,
        traits: Mapping[str, bool],
    ) -> bool:
        if self.university_id is not None and self.university_id == university_id:
            return True
        if self.trait:
            return bool(traits.get(self.trait))
        return False


@dataclass(frozen=True, slots=True)
class PreferenceInput:
    """Everything the scorer is allowed to look at. Pure data — no ORM."""

    # 专业维度
    major_id: int | None = None
    discipline_code: str | None = None
    major_names: tuple[str, ...] = ()
    major_text: str = ""
    interest_keywords: tuple[str, ...] = ()
    # 学校维度
    university_id: int = 0
    university_name: str = ""
    university_traits: Mapping[str, bool] = field(default_factory=dict)
    # 地域维度
    province_code: str | None = None
    city_code: str | None = None
    # 费用维度
    tuition_fee: int = 0
    tuition_budget: int | None = None
    tuition_floor: int | None = None
    # 升学 / 职业维度（由院校与专业特征派生）
    academic_score: float | None = None
    career_score: float | None = None
    # 个性化维度
    personal_score: float | None = None


@dataclass(frozen=True, slots=True)
class PreferenceWeights:
    """Per-dimension weights plus the user's like/dislike selectors."""

    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_DIMENSION_WEIGHTS))
    major_includes: tuple[MajorSelector, ...] = ()
    major_excludes: tuple[MajorSelector, ...] = ()
    location_includes: tuple[LocationSelector, ...] = ()
    location_excludes: tuple[LocationSelector, ...] = ()
    university_includes: tuple[UniversitySelector, ...] = ()
    university_excludes: tuple[UniversitySelector, ...] = ()
    # ACADEMIC / CAREER / PERSONAL key-value preferences: key -> (weight, preferred_value)
    trait_preferences: Mapping[str, tuple[float, Any]] = field(default_factory=dict)

    def weight_for(self, dimension: PreferenceDimension | str) -> float:
        key = dimension.value if isinstance(dimension, PreferenceDimension) else dimension
        if key in self.weights:
            return max(0.0, float(self.weights[key]))
        return DEFAULT_DIMENSION_WEIGHTS.get(key, 0.0)


@dataclass(frozen=True, slots=True)
class DimensionScore:
    dimension: str
    score: float
    weight: float
    available: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": round(self.score, 3),
            "weight": round(self.weight, 4),
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PreferenceEvaluation:
    score: float
    dimensions: tuple[DimensionScore, ...]
    available_dimensions: tuple[str, ...]
    excluded: bool = False
    exclude_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "available_dimensions": list(self.available_dimensions),
            "excluded": self.excluded,
            "exclude_reason": self.exclude_reason,
            "dimensions": [d.as_dict() for d in self.dimensions],
        }

    def dimension(self, name: str) -> DimensionScore | None:
        for item in self.dimensions:
            if item.dimension == name:
                return item
        return None


class PreferenceService:
    """Computes the user-preference score. Never looks at eligibility or ACS."""

    def evaluate(self, *, candidate: PreferenceInput, weights: PreferenceWeights) -> PreferenceEvaluation:
        exclusions = self._hard_exclusions(candidate, weights)
        if exclusions:
            return PreferenceEvaluation(
                score=0.0,
                dimensions=(),
                available_dimensions=(),
                excluded=True,
                exclude_reason="；".join(exclusions),
            )

        dimensions: list[DimensionScore] = [
            self._major_dimension(candidate, weights),
            self._university_dimension(candidate, weights),
            self._location_dimension(candidate, weights),
            self._academic_dimension(candidate, weights),
            self._career_dimension(candidate, weights),
            self._tuition_dimension(candidate, weights),
            self._personal_dimension(candidate, weights),
        ]

        available = [d for d in dimensions if d.available and d.weight > 0]
        if not available:
            # No preference information at all: neutral, not zero.
            return PreferenceEvaluation(
                score=50.0,
                dimensions=tuple(dimensions),
                available_dimensions=(),
            )

        denominator = sum(d.weight for d in available)
        numerator = sum(d.weight * d.score for d in available)
        score = numerator / denominator if denominator > 0 else 50.0
        return PreferenceEvaluation(
            score=max(0.0, min(100.0, score)),
            dimensions=tuple(dimensions),
            available_dimensions=tuple(d.dimension for d in available),
        )

    # ---------------- hard exclusions ----------------

    @staticmethod
    def _hard_exclusions(candidate: PreferenceInput, weights: PreferenceWeights) -> list[str]:
        reasons: list[str] = []
        for major_selector in weights.major_excludes:
            if major_selector.matches(
                major_id=candidate.major_id,
                discipline_code=candidate.discipline_code,
                names=(*candidate.major_names, candidate.major_text),
            ):
                reasons.append("专业命中用户硬性排除")
                break
        for location_selector in weights.location_excludes:
            if location_selector.matches(
                province_code=candidate.province_code, city_code=candidate.city_code
            ):
                reasons.append("地域命中用户硬性排除")
                break
        for university_selector in weights.university_excludes:
            if university_selector.matches(
                university_id=candidate.university_id, traits=candidate.university_traits
            ):
                reasons.append("学校命中用户硬性排除")
                break
        return reasons

    # ---------------- dimensions ----------------

    @staticmethod
    def _major_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.MAJOR)
        reasons: list[str] = []
        score = 50.0
        available = False

        if weights.major_includes:
            available = True
            best = 0.0
            for selector in weights.major_includes:
                if selector.matches(
                    major_id=candidate.major_id,
                    discipline_code=candidate.discipline_code,
                    names=(*candidate.major_names, candidate.major_text),
                ):
                    best = max(best, max(0.0, min(100.0, selector.weight)))
                    reasons.append("命中指定专业偏好")
            score = best if best > 0 else 20.0
            if best == 0:
                reasons.append("不在任何指定专业偏好中")

        keyword_hits = [k for k in candidate.interest_keywords if k and k in candidate.major_text]
        if keyword_hits:
            available = True
            score = max(score, min(100.0, 60.0 + 10.0 * len(keyword_hits)))
            reasons.append(f"专业名称命中兴趣关键词：{'、'.join(keyword_hits)}")

        return DimensionScore(
            dimension=PreferenceDimension.MAJOR.value,
            score=score,
            weight=weight,
            available=available,
            reason="；".join(reasons),
        )

    @staticmethod
    def _university_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.UNIVERSITY)
        reasons: list[str] = []
        score = 50.0
        available = False

        if weights.university_includes:
            available = True
            best = 0.0
            for selector in weights.university_includes:
                if selector.matches(
                    university_id=candidate.university_id, traits=candidate.university_traits
                ):
                    best = max(best, max(0.0, min(100.0, selector.weight)))
                    reasons.append("命中指定学校偏好")
            score = best if best > 0 else 20.0
            if best == 0:
                reasons.append("不在任何指定学校偏好中")

        return DimensionScore(
            dimension=PreferenceDimension.UNIVERSITY.value,
            score=score,
            weight=weight,
            available=available,
            reason="；".join(reasons),
        )

    @staticmethod
    def _location_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.LOCATION)
        reasons: list[str] = []
        score = 50.0
        available = False

        if weights.location_includes:
            available = True
            best = 0.0
            for selector in weights.location_includes:
                if selector.matches(
                    province_code=candidate.province_code, city_code=candidate.city_code
                ):
                    # A city-level match is stronger evidence than a province-level one
                    base = max(0.0, min(100.0, selector.weight))
                    if selector.city_code and selector.city_code == candidate.city_code:
                        best = max(best, base)
                        reasons.append("命中目标城市")
                    else:
                        best = max(best, base * 0.85)
                        reasons.append("命中目标省份")
            score = best if best > 0 else 20.0
            if best == 0:
                reasons.append("不在任何偏好地域内")

        return DimensionScore(
            dimension=PreferenceDimension.LOCATION.value,
            score=score,
            weight=weight,
            available=available,
            reason="；".join(reasons),
        )

    @staticmethod
    def _academic_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.ACADEMIC)
        reasons: list[str] = []
        available = candidate.academic_score is not None
        score = 50.0

        if candidate.academic_score is not None:
            score = max(0.0, min(100.0, candidate.academic_score))
            reasons.append("依据院校层次/升学特征计算")
        else:
            traits = candidate.university_traits
            flags = [traits.get(key) for key in ("IS_985", "IS_211", "IS_DOUBLE_FIRST_CLASS", "HAS_POSTGRAD")]
            known = [flag for flag in flags if flag is not None]
            if known:
                available = True
                score = 100.0 * (sum(1 for flag in known if flag) / len(known))
                reasons.append("依据院校层次标签计算")

        return DimensionScore(
            dimension=PreferenceDimension.ACADEMIC.value,
            score=score,
            weight=weight,
            available=available,
            reason="；".join(reasons),
        )

    @staticmethod
    def _career_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.CAREER)
        available = candidate.career_score is not None
        score = 50.0 if candidate.career_score is None else max(0.0, min(100.0, candidate.career_score))
        return DimensionScore(
            dimension=PreferenceDimension.CAREER.value,
            score=score,
            weight=weight,
            available=available,
            reason="依据职业方向偏好计算" if available else "缺少职业方向数据",
        )

    @staticmethod
    def _tuition_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        """Score by how comfortably the tuition fits the stated budget."""
        weight = weights.weight_for(PreferenceDimension.TUITION)
        has_budget = candidate.tuition_budget is not None or candidate.tuition_floor is not None
        if not has_budget:
            return DimensionScore(
                dimension=PreferenceDimension.TUITION.value,
                score=50.0,
                weight=weight,
                available=False,
                reason="未提供学费预算",
            )

        score = 100.0
        reasons: list[str] = []
        if candidate.tuition_budget is not None:
            budget = candidate.tuition_budget
            if candidate.tuition_fee > budget:
                # Over budget: decays but stays positive so ordering remains smooth
                excess = (candidate.tuition_fee - budget) / max(1.0, budget)
                score = max(0.0, 100.0 * math.exp(-3.0 * excess))
                reasons.append("超出学费预算")
            else:
                # Comfortably under budget: the more headroom the better, saturating
                headroom = (budget - candidate.tuition_fee) / max(1.0, budget)
                score = min(100.0, 70.0 + 30.0 * min(1.0, headroom * 4))
                reasons.append("在学费预算内")
        if candidate.tuition_floor is not None and candidate.tuition_fee < candidate.tuition_floor:
            score = min(score, 60.0)
            reasons.append("低于期望学费下限")

        return DimensionScore(
            dimension=PreferenceDimension.TUITION.value,
            score=max(0.0, min(100.0, score)),
            weight=weight,
            available=True,
            reason="；".join(reasons),
        )

    @staticmethod
    def _personal_dimension(candidate: PreferenceInput, weights: PreferenceWeights) -> DimensionScore:
        weight = weights.weight_for(PreferenceDimension.PERSONAL)
        available = candidate.personal_score is not None
        score = 50.0 if candidate.personal_score is None else max(0.0, min(100.0, candidate.personal_score))
        return DimensionScore(
            dimension=PreferenceDimension.PERSONAL.value,
            score=score,
            weight=weight,
            available=available,
            reason="依据个性化偏好计算" if available else "缺少个性化偏好数据",
        )


def desire_band(preference_score: float) -> int:
    """Sorting step 2: floor(PreferenceScore / 3) — see docs/ALGORITHM.md §13."""
    return int(math.floor(max(0.0, min(100.0, preference_score)) / 3))


def polarity_of(value: str | PreferencePolarity) -> PreferencePolarity:
    return value if isinstance(value, PreferencePolarity) else PreferencePolarity(value)


__all__ = [
    "DEFAULT_DIMENSION_WEIGHTS",
    "DimensionScore",
    "LocationSelector",
    "MajorSelector",
    "PreferenceEvaluation",
    "PreferenceInput",
    "PreferenceService",
    "PreferenceWeights",
    "UniversitySelector",
    "desire_band",
]
