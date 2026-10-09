"""Import every ORM model so metadata is complete for Alembic and tests.

Kept out of `app/db/__init__.py` on purpose: models import `app.db.base`, so
importing them from the package root would create a circular import.
"""

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    AdmissionRequirement,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SourceDocument,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.candidate.models import (
    Attribute,
    LocationPreference,
    MajorPreference,
    OtherPreference,
    PreferenceProfile,
    Profile,
    Subject,
    SubjectScore,
    UniversityPreference,
)
from app.recommendation.models import (
    HealthCheck,
    HealthCheckIssue,
    ModelVersion,
    RecommendationItem,
    Scheme,
    SchemeItem,
    SchemeVersion,
    Session,
)
from app.reference.models import Campus, Major, University

ALL_MODELS = (
    # reference
    University,
    Campus,
    Major,
    # admission
    SourceDocument,
    DatasetSnapshot,
    AdmissionCycle,
    ExamYearStats,
    ScoreRankSegment,
    ProgramSeries,
    SubjectRuleSet,
    SubjectRuleGroup,
    SubjectRuleItem,
    AdmissionPlan,
    AdmissionOutcome,
    AdmissionRequirement,
    # candidate
    Profile,
    Subject,
    SubjectScore,
    Attribute,
    PreferenceProfile,
    MajorPreference,
    LocationPreference,
    UniversityPreference,
    OtherPreference,
    # recommendation
    ModelVersion,
    Session,
    RecommendationItem,
    Scheme,
    SchemeVersion,
    SchemeItem,
    HealthCheck,
    HealthCheckIssue,
)

__all__ = ["ALL_MODELS"]
