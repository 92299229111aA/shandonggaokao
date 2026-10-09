"""Enumerations shared across ORM, DTOs and pure algorithm layers.

Stored as VARCHAR + CHECK constraint rather than native PG enums: adding a
policy value later is then a one-line CHECK change instead of an ALTER TYPE
that cannot run inside a transaction with other DDL.
"""

from __future__ import annotations

from enum import StrEnum

SCHEMAS: tuple[str, ...] = ("reference", "admission", "candidate", "recommendation")


class ExamCategory(StrEnum):
    SUMMER_REGULAR = "SUMMER_REGULAR"  # 夏季高考普通类


class BatchCode(StrEnum):
    REGULAR_1 = "REGULAR_BATCH_1"  # 常规批第1次志愿


class ProgramLevel(StrEnum):
    UNDERGRAD = "UNDERGRAD"  # 本科
    JUNIOR_COLLEGE = "JUNIOR_COLLEGE"  # 专科 (out of V1 scope, reserved)


class CooperativeType(StrEnum):
    NONE = "NONE"
    CHINESE_FOREIGN = "CHINESE_FOREIGN"  # 中外合作办学
    HONGKONG_MACAO_TAIWAN = "HONGKONG_MACAO_TAIWAN"  # 港澳台合作
    OTHER = "OTHER"


class SnapshotStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class SourceQuality(StrEnum):
    OFFICIAL = "OFFICIAL"  # 省教育招生考试院 / 阳光高考
    UNIVERSITY = "UNIVERSITY"  # 高校招生章程
    THIRD_PARTY = "THIRD_PARTY"
    DEMO = "DEMO"  # clearly-marked fixture, never usable as a real source


class SeriesMatchQuality(StrEnum):
    EXACT = "EXACT"  # same major code + same campus + same cooperative flag
    RENAMED = "RENAMED"  # same major code, display name changed
    MERGED = "MERGED"  # several majors merged into one 专业类
    SPLIT = "SPLIT"
    FUZZY = "FUZZY"  # name similarity only — weakest evidence
    NONE = "NONE"


class SubjectRuleMode(StrEnum):
    ANY = "ANY"  # no subject restriction
    ALL = "ALL"  # all listed subjects required


class RequirementType(StrEnum):
    SUBJECT = "SUBJECT"
    TOTAL_SCORE = "TOTAL_SCORE"
    SINGLE_SUBJECT = "SINGLE_SUBJECT"
    LANGUAGE = "LANGUAGE"
    SEX = "SEX"
    MEDICAL = "MEDICAL"
    QUALIFICATION = "QUALIFICATION"
    OTHER = "OTHER"
    # Not an admission_requirements value: used for user-level hard blocks
    PREFERENCE = "PREFERENCE"


class RequirementSeverity(StrEnum):
    BLOCK = "BLOCK"  # FAIL when unmet
    WARN = "WARN"  # warning only
    INFO = "INFO"


class EligibilityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class RiskBucket(StrEnum):
    HIGH_REACH = "HIGH_REACH"  # 高冲 0-29
    REACH = "REACH"  # 冲   30-44
    BORDER = "BORDER"  # 边界 45-59
    STABLE = "STABLE"  # 稳   60-79
    SAFE = "SAFE"  # 保   80-100


class RiskMode(StrEnum):
    STEADY = "STEADY"  # 稳健
    BALANCED = "BALANCED"  # 均衡
    AGGRESSIVE = "AGGRESSIVE"  # 激进


class PreferenceDimension(StrEnum):
    MAJOR = "MAJOR"
    UNIVERSITY = "UNIVERSITY"
    LOCATION = "LOCATION"
    ACADEMIC = "ACADEMIC"  # 升学/学校特征
    CAREER = "CAREER"
    TUITION = "TUITION"
    PERSONAL = "PERSONAL"


class PreferencePolarity(StrEnum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"  # user hard exclude — never breached by the optimizer


class SessionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class HealthSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class SexRestriction(StrEnum):
    ANY = "ANY"
    MALE_ONLY = "MALE_ONLY"
    FEMALE_ONLY = "FEMALE_ONLY"


# Risk bucket boundaries are part of the published algorithm spec
# (docs/ALGORITHM.md) — they are algorithm constants, not policy numbers.
RISK_BUCKET_BOUNDS: tuple[tuple[RiskBucket, float, float], ...] = (
    (RiskBucket.HIGH_REACH, 0.0, 29.999),
    (RiskBucket.REACH, 30.0, 44.999),
    (RiskBucket.BORDER, 45.0, 59.999),
    (RiskBucket.STABLE, 60.0, 79.999),
    (RiskBucket.SAFE, 80.0, 100.0),
)


def risk_bucket_for(acs: float) -> RiskBucket:
    """Map a 0-100 ACS value onto its risk bucket."""
    if acs < 30:
        return RiskBucket.HIGH_REACH
    if acs < 45:
        return RiskBucket.REACH
    if acs < 60:
        return RiskBucket.BORDER
    if acs < 80:
        return RiskBucket.STABLE
    return RiskBucket.SAFE
