"""Three-state eligibility result types.

The engine never guesses. A rule that cannot be evaluated from trusted data
returns UNKNOWN — it must never be coerced to PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.common.enums import EligibilityStatus, RequirementSeverity, RequirementType


class RuleCode(StrEnum):
    """Stable diagnostic codes; also used as HealthCheck issue codes."""

    SUBJECT_NOT_SATISFIED = "SUBJECT_NOT_SATISFIED"
    SUBJECT_REQUIREMENT_UNPARSED = "SUBJECT_REQUIREMENT_UNPARSED"
    SUBJECTS_UNKNOWN = "SUBJECTS_UNKNOWN"
    TOTAL_SCORE_BELOW = "TOTAL_SCORE_BELOW"
    SCORE_UNKNOWN = "SCORE_UNKNOWN"
    SINGLE_SUBJECT_BELOW = "SINGLE_SUBJECT_BELOW"
    SINGLE_SUBJECT_SCORE_UNKNOWN = "SINGLE_SUBJECT_SCORE_UNKNOWN"
    FOREIGN_LANGUAGE_MISMATCH = "FOREIGN_LANGUAGE_MISMATCH"
    FOREIGN_LANGUAGE_UNKNOWN = "FOREIGN_LANGUAGE_UNKNOWN"
    SEX_MISMATCH = "SEX_MISMATCH"
    SEX_UNKNOWN = "SEX_UNKNOWN"
    MEDICAL_UNSATISFIED = "MEDICAL_UNSATISFIED"
    MEDICAL_UNKNOWN = "MEDICAL_UNKNOWN"
    QUALIFICATION_MISSING = "QUALIFICATION_MISSING"
    QUALIFICATION_UNKNOWN = "QUALIFICATION_UNKNOWN"
    REQUIREMENT_UNRESOLVED = "REQUIREMENT_UNRESOLVED"
    REQUIREMENT_MALFORMED = "REQUIREMENT_MALFORMED"
    SPECIAL_REQUIREMENT_UNVERIFIED = "SPECIAL_REQUIREMENT_UNVERIFIED"
    USER_HARD_EXCLUDE = "USER_HARD_EXCLUDE"


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """One concrete reason behind a FAIL / UNKNOWN / WARN outcome."""

    code: RuleCode
    message: str
    requirement_type: RequirementType
    severity: RequirementSeverity
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """A single rule's verdict.

    Invariants:
      * FAIL   -> at least one BLOCK-severity violation
      * UNKNOWN-> no FAIL, but at least one unresolved (BLOCK or WARN) item
      * PASS   -> only WARN/INFO items, if any
    """

    status: EligibilityStatus
    rule_code: str
    failures: tuple[RuleViolation, ...] = ()
    unknowns: tuple[RuleViolation, ...] = ()
    warnings: tuple[RuleViolation, ...] = ()

    @property
    def explanation(self) -> str:
        parts = [v.message for v in (*self.failures, *self.unknowns, *self.warnings)]
        return "；".join(parts)

    @classmethod
    def pass_(cls, rule_code: str, *, warnings: tuple[RuleViolation, ...] = ()) -> RuleOutcome:
        return cls(EligibilityStatus.PASS, rule_code, warnings=warnings)

    @classmethod
    def fail(cls, rule_code: str, violation: RuleViolation, *extra: RuleViolation) -> RuleOutcome:
        return cls(EligibilityStatus.FAIL, rule_code, failures=(violation, *extra))

    @classmethod
    def unknown(cls, rule_code: str, violation: RuleViolation, *extra: RuleViolation) -> RuleOutcome:
        return cls(EligibilityStatus.UNKNOWN, rule_code, unknowns=(violation, *extra))


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """Final verdict for one 志愿单位 (专业+学校)."""

    status: EligibilityStatus
    rule_code: str = "COMBINED"
    failures: tuple[RuleViolation, ...] = ()
    unknowns: tuple[RuleViolation, ...] = ()
    warnings: tuple[RuleViolation, ...] = ()
    hard_excluded: bool = False
    excluded_by: str | None = None

    @property
    def can_be_pooled(self) -> bool:
        """FAIL never enters the candidate pool (architecture invariant #5)."""
        return self.status is not EligibilityStatus.FAIL

    @property
    def counts_as_safe(self) -> bool:
        """Only PASS can back a 保底 slot; UNKNOWN may not."""
        return self.status is EligibilityStatus.PASS

    def as_reasons(self) -> list[dict[str, Any]]:
        """JSON-serialisable explanation, persisted to recommendation_items."""
        return [
            {
                "code": str(v.code),
                "message": v.message,
                "requirement_type": str(v.requirement_type),
                "severity": str(v.severity),
                "detail": v.detail,
            }
            for v in (*self.failures, *self.unknowns, *self.warnings)
        ]

    @classmethod
    def combine(cls, outcomes: list[RuleOutcome]) -> EligibilityResult:
        """Three-state AND, per the published truth table.

        Any FAIL        -> FAIL
        else any UNKNOWN-> UNKNOWN
        else            -> PASS
        """
        failures: list[RuleViolation] = []
        unknowns: list[RuleViolation] = []
        warnings: list[RuleViolation] = []
        for outcome in outcomes:
            failures.extend(outcome.failures)
            unknowns.extend(outcome.unknowns)
            warnings.extend(outcome.warnings)

        if failures:
            status = EligibilityStatus.FAIL
        elif unknowns:
            status = EligibilityStatus.UNKNOWN
        else:
            status = EligibilityStatus.PASS

        return cls(
            status=status,
            failures=tuple(failures),
            unknowns=tuple(unknowns),
            warnings=tuple(warnings),
        )
