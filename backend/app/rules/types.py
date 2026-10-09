"""Pure input data for the Rule Engine.

Nothing here knows about SQLAlchemy or HTTP: the recommendation pipeline maps
ORM rows onto these dataclasses, and tests construct them by hand.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.common.enums import SubjectRuleMode

T = TypeVar("T")

# Characters that legitimately separate a 专业名称 from its qualifier, e.g.
# "临床医学(5+3一体化)" or "计算机类[校企合作]".
_NAME_DELIMITERS = frozenset("(（[【·-—、,，:：")


@dataclass(frozen=True, slots=True)
class Fact(Generic[T]):
    """A candidate fact that may be missing.

    A missing fact must never be silently treated as False — that is exactly the
    difference between FAIL ("不能报") and UNKNOWN ("不确定能不能报").
    """

    value: T | None = None
    known: bool = False

    @classmethod
    def of(cls, value: T | None) -> Fact[T]:
        return cls(value=value, known=value is not None)

    @classmethod
    def missing(cls) -> Fact[T]:
        return cls(value=None, known=False)


@dataclass(frozen=True, slots=True)
class SubjectRequirement:
    """选科要求 in DNF: `groups` are OR-ed, the codes inside a group are AND-ed.

    An empty group list means "no subject restriction".
    """

    rule_set_id: int | None = None
    mode: SubjectRuleMode = SubjectRuleMode.ALL
    groups: tuple[tuple[str, ...], ...] = ()
    raw_text: str | None = None
    is_authoritative: bool = True

    def alternative_texts(self) -> list[str]:
        if not self.groups:
            return ["不限"]
        return ["+".join(group) for group in self.groups]


@dataclass(frozen=True, slots=True)
class Requirement:
    """One row of `admission.admission_requirements`, transport-agnostic."""

    requirement_type: str
    operator: str = "EQ"
    operand: Mapping[str, Any] | None = None
    severity: str = "BLOCK"
    is_deterministic: bool = True
    raw_text: str | None = None
    requirement_id: int | None = None

    def number(self, key: str) -> float | None:
        raw = (self.operand or {}).get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(value) else value

    def text(self, key: str) -> str | None:
        raw = (self.operand or {}).get(key)
        if raw is None:
            return None
        return str(raw).strip() or None

    def bool_(self, key: str) -> bool | None:
        raw = (self.operand or {}).get(key)
        return raw if isinstance(raw, bool) else None

    def list_(self, key: str) -> tuple[str, ...]:
        raw = (self.operand or {}).get(key)
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, list | tuple):
            return tuple(str(item) for item in raw)
        return ()


@dataclass(frozen=True, slots=True)
class TargetPlan:
    """A 志愿单位 (专业/专业类 + 学校) plus everything needed to judge eligibility."""

    plan_id: int
    plan_name: str
    university_id: int
    university_name: str
    province_code: str = ""
    city_code: str | None = None
    cooperative: str = "NONE"
    tuition_fee: int = 0
    plan_count: int = 0
    major_id: int | None = None
    discipline_code: str | None = None
    subject_requirement: SubjectRequirement | None = None
    requirements: tuple[Requirement, ...] = ()
    special_requirements_text: str | None = None
    series_match_quality: str = "EXACT"
    is_demo: bool = False

    def requirements_of(self, *types: str) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.requirement_type in types)

    def matches_major_name(self, names: tuple[str, ...]) -> bool:
        """Conservative name match for user hard-excluded majors.

        The excluded name must occupy the whole plan name or a delimited segment of
        it, so excluding "专业2" does not also swallow "专业20". A false positive
        only removes a plan the user said they never want, which is the safe
        direction, but prefix collisions would silently drop far too much.
        """
        haystack = self.plan_name.replace(" ", "")
        for name in names:
            needle = name.replace(" ", "")
            if not needle:
                continue
            if haystack == needle:
                return True
            if not haystack.startswith(needle):
                continue
            if haystack[len(needle) : len(needle) + 1] in _NAME_DELIMITERS:
                return True
        return False


@dataclass(frozen=True, slots=True)
class UserHardExcludes:
    """User hard blocks. The optimizer must never breach these (invariant #6)."""

    major_ids: frozenset[int] = frozenset()
    major_names: tuple[str, ...] = ()
    discipline_codes: frozenset[str] = frozenset()
    university_ids: frozenset[int] = frozenset()
    province_codes: frozenset[str] = frozenset()
    city_codes: frozenset[str] = frozenset()
    exclude_cooperative: bool = False
    exclude_private: bool = False
    max_tuition: int | None = None

    def __bool__(self) -> bool:
        return any(
            (
                self.major_ids,
                self.major_names,
                self.discipline_codes,
                self.university_ids,
                self.province_codes,
                self.city_codes,
                self.exclude_cooperative,
                self.exclude_private,
                self.max_tuition is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class CandidateContext:
    """Normalised candidate facts. Missing values are `Fact.missing()`."""

    rank: int | None = None
    score: int | None = None
    current_ranked_candidates: int | None = None
    subjects: frozenset[str] = frozenset()
    subjects_known: bool = False
    subject_scores: Mapping[str, int] = field(default_factory=dict)
    foreign_language: Fact[str] = Fact.missing()
    sex: Fact[str] = Fact.missing()
    # 体检结论等特殊条件: key -> value
    attributes: Mapping[str, Any] = field(default_factory=dict)
    hard_excludes: UserHardExcludes = UserHardExcludes()

    def attribute(self, key: str) -> Fact[Any]:
        if key not in self.attributes:
            return Fact.missing()
        value = self.attributes[key]
        return Fact(value=value, known=value is not None)

    @property
    def score_fact(self) -> Fact[int]:
        return Fact.of(self.score)

    def subject_known(self, code: str) -> Fact[bool]:
        if not self.subjects_known:
            return Fact.missing()
        return Fact(value=code in self.subjects, known=True)
