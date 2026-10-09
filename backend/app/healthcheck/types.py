"""Health Check types.

A health check is a pure function of a *fact snapshot*: it reports problems and
never mutates the scheme (spec: 诊断与决策必须分离).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.common.enums import HealthSeverity

# Transparent penalty model: the score starts at 100, each *class* of issue subtracts
# a fixed amount, and a class that fires many times adds a small capped increment.
# It is a product indicator, NOT an admission probability.
#
# Why per class rather than per occurrence: most checks are position-level, and on the
# real dataset a single scheme trips SMALL_PLAN_COUNT 40 times out of 96 volunteers
# (63% of all Shandong plans have fewer than 5 seats). Charging 5 points 40 times
# floors the score at 0 for a perfectly reasonable scheme, so the 0-100 scale stops
# telling a good scheme from a broken one.
SEVERITY_PENALTY: dict[HealthSeverity, int] = {
    HealthSeverity.ERROR: 15,
    HealthSeverity.WARNING: 5,
    HealthSeverity.INFO: 1,
}

# Extra charge per additional occurrence of the same issue code, and its ceiling.
# This keeps a widespread problem scoring worse than a single one without letting it
# dominate the whole score.
REPEAT_PENALTY = 1
REPEAT_PENALTY_CAP = 3

# Findings that describe a *favourable* fact rather than a problem. They are still
# listed for transparency but must not reduce the score — "your programme expanded"
# is good news. (Expansion genuinely helps: more seats means the cut-off rank moves
# down, which is why PlanReductionCheck reports it at all.)
NON_PENALISED_CODES: frozenset[str] = frozenset({"PLAN_EXPANDED"})

# A plan below this many seats is a small-sample risk.
#
# Recalibrated against the real Shandong dataset: the median plan takes 3 students and
# 63% of all plans take fewer than 5, so the previous value of 5 fired on two thirds of
# every scheme and carried no information. 3 sits at the real median (35% fire), which
# is what "unusually small" means for this data.
SMALL_PLAN_THRESHOLD = 3
# A plan shrinking by more than this fraction versus its history is a warning.
#
# Measured year-over-year swings of the same programme: p10 = -33%, p50 = 0%, p90 = +50%.
# 30% is therefore the knee of the distribution and flags 15.6% of plans versus the
# median of available history — a meaningful minority rather than everything.
PLAN_REDUCTION_THRESHOLD = 0.30
# A reduction claim needs a baseline. With a single history year the "median" is that
# one year, so any dip looks like a collapse. Require at least this many usable years.
PLAN_REDUCTION_MIN_HISTORY = 2
# Confidence below this (0-100) is treated as weak evidence. `SchemeFact.confidence` is
# stored on the 0-100 scale (the pipeline multiplies the 0-1 ACSEvaluation value by 100),
# which is what this compares against. Real range is 58-83, so this flags the weakest
# ~14% rather than nothing.
LOW_CONFIDENCE_THRESHOLD = 50.0
# A snapshot older than this many years relative to the target cycle is stale.
STALE_SNAPSHOT_MAX_AGE_YEARS = 1


@dataclass(frozen=True, slots=True)
class HealthIssue:
    code: str
    severity: HealthSeverity
    message: str
    position: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    suggestion: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "position": self.position,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True, slots=True)
class CheckResult:
    code: str
    title: str
    issues: tuple[HealthIssue, ...] = ()

    @property
    def worst_severity(self) -> HealthSeverity | None:
        for severity in (HealthSeverity.ERROR, HealthSeverity.WARNING, HealthSeverity.INFO):
            if any(issue.severity is severity for issue in self.issues):
                return severity
        return None

    @property
    def passed(self) -> bool:
        return not self.issues

    def counts(self) -> dict[str, int]:
        return {
            "errors": sum(1 for i in self.issues if i.severity is HealthSeverity.ERROR),
            "warnings": sum(1 for i in self.issues if i.severity is HealthSeverity.WARNING),
            "infos": sum(1 for i in self.issues if i.severity is HealthSeverity.INFO),
        }


@dataclass(frozen=True, slots=True)
class SchemeFact:
    """One volunteer as the health check sees it."""

    position: int
    plan_id: int
    program_label: str
    university_id: int
    major_id: int | None = None
    discipline_code: str | None = None
    plan_name: str = ""
    university_name: str = ""
    eligibility: str = "PASS"
    acs: float | None = None
    confidence: float | None = None
    preference_score: float | None = None
    desire_band: int | None = None
    risk_bucket: str | None = None
    locked: bool = False
    fixed_position: bool = False
    # current-cycle plan facts
    plan_count: int = 0
    tuition_fee: int = 0
    subject_requirement_text: str | None = None
    special_requirements_text: str | None = None
    has_unknown_requirement: bool = False
    # history
    plan_count_history: tuple[int, ...] = ()
    min_rank_history: tuple[int, ...] = ()
    cooperative: str = "NONE"


@dataclass(frozen=True, slots=True)
class SnapshotFact:
    id: int
    code: str
    status: str
    published_year: int | None = None
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class PreferenceFact:
    """The user's hard boundaries, re-checked against the finished scheme."""

    risk_mode: str = "BALANCED"
    max_tuition: int | None = None
    min_tuition: int | None = None
    exclude_private: bool = False
    exclude_cooperative: bool = False
    min_safe_count: int | None = None
    target_count: int = 96
    hard_exclude_major_ids: frozenset[int] = frozenset()
    hard_exclude_major_names: tuple[str, ...] = ()
    hard_exclude_discipline_codes: frozenset[str] = frozenset()
    hard_exclude_university_ids: frozenset[int] = frozenset()
    hard_exclude_province_codes: frozenset[str] = frozenset()
    hard_exclude_city_codes: frozenset[str] = frozenset()

    @property
    def has_hard_excludes(self) -> bool:
        return bool(
            self.hard_exclude_major_ids
            or self.hard_exclude_major_names
            or self.hard_exclude_discipline_codes
            or self.hard_exclude_university_ids
            or self.hard_exclude_province_codes
            or self.hard_exclude_city_codes
            or self.exclude_cooperative
            or self.max_tuition is not None
        )


@dataclass(frozen=True, slots=True)
class HealthCheckContext:
    """Everything the checks may look at. No ORM, no session."""

    items: tuple[SchemeFact, ...] = ()
    application_limit: int = 96
    risk_targets: dict[str, int] = field(default_factory=dict)
    preference: PreferenceFact = field(default_factory=PreferenceFact)
    snapshot: SnapshotFact | None = None
    latest_published_snapshot: SnapshotFact | None = None
    target_exam_year: int | None = None
    # eligibility re-evaluated against the *current* rules, if the caller did so
    recheck_enabled: bool = False

    @property
    def item_count(self) -> int:
        return len(self.items)


class HealthCheck(Protocol):
    code: str
    title: str

    def run(self, ctx: HealthCheckContext) -> CheckResult: ...


def result(code: str, title: str, issues: Sequence[HealthIssue]) -> CheckResult:
    return CheckResult(code=code, title=title, issues=tuple(issues))


def score_for(issues: Sequence[HealthIssue]) -> tuple[int, dict[str, int]]:
    """Health score: 100 minus the penalty of each issue *class*, floored at 0.

    Grouping by code is what makes the number usable on real data. `counts` still
    reports every occurrence, so nothing is hidden from the candidate — only the
    scoring is aggregated.
    """
    grouped: dict[str, list[HealthIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.code, []).append(issue)

    penalty = 0
    for code, group in grouped.items():
        if code in NON_PENALISED_CODES:
            continue
        severity = max(group, key=lambda i: SEVERITY_PENALTY[i.severity]).severity
        extra = min(REPEAT_PENALTY_CAP, (len(group) - 1) * REPEAT_PENALTY)
        penalty += SEVERITY_PENALTY[severity] + extra

    counts = {
        "errors": sum(1 for i in issues if i.severity is HealthSeverity.ERROR),
        "warnings": sum(1 for i in issues if i.severity is HealthSeverity.WARNING),
        "infos": sum(1 for i in issues if i.severity is HealthSeverity.INFO),
    }
    return max(0, 100 - penalty), counts


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "NON_PENALISED_CODES",
    "PLAN_REDUCTION_THRESHOLD",
    "REPEAT_PENALTY",
    "REPEAT_PENALTY_CAP",
    "SEVERITY_PENALTY",
    "SMALL_PLAN_THRESHOLD",
    "STALE_SNAPSHOT_MAX_AGE_YEARS",
    "CheckResult",
    "HealthCheck",
    "HealthCheckContext",
    "HealthIssue",
    "PreferenceFact",
    "SchemeFact",
    "SnapshotFact",
    "result",
    "score_for",
]
