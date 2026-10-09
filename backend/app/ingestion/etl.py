"""Ingestion framework contract for official data (Phase 9 scaffold).

V1 ships DEMO fixtures only. This module defines the *shape* official parsers must
produce, so `seed_demo` and a future real ETL converge on the same contract and the
recommendation pipeline never learns where its data came from.

Pipeline (spec §10):

    Official Source -> RAW Archive -> Parser -> Normalizer -> Matcher
      -> Validator -> Dataset Snapshot: DRAFT -> Validation Report -> PUBLISHED

Rules this module exists to enforce:

1. Every parsed fact carries a `source_document_id`; an unparseable rule must be
   marked `is_deterministic=False` rather than guessed.
2. Nothing enters the engine until a snapshot is PUBLISHED.
3. `checksum_sha256` keeps the same official file from being ingested twice under
   two identities.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

DEMO_QUALITY = "DEMO"


def file_checksum(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a raw archive file. Used as `source_documents.checksum_sha256`."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RawArchive:
    """A downloaded official file plus its provenance."""

    path: Path
    title: str
    publisher: str | None
    url: str | None
    quality: str
    published_on: str | None = None
    is_demo: bool = False

    @property
    def checksum(self) -> str:
        return file_checksum(self.path)


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    row: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationReport:
    """Outcome of validating a parsed batch before it may be PUBLISHED."""

    issues: list[ValidationIssue] = field(default_factory=list)
    rows_parsed: int = 0
    rows_rejected: int = 0
    unknowns: int = 0

    @property
    def ok(self) -> bool:
        return not any(issue.code.startswith("ERROR_") for issue in self.issues)

    def error(self, code: str, message: str, *, row: int | None = None, **detail: Any) -> None:
        self.issues.append(
            ValidationIssue(code=f"ERROR_{code}", message=message, row=row, detail=detail)
        )
        self.rows_rejected += 1

    def warn(self, code: str, message: str, *, row: int | None = None, **detail: Any) -> None:
        self.issues.append(
            ValidationIssue(code=f"WARN_{code}", message=message, row=row, detail=detail)
        )

    def unknown(self, code: str, message: str, *, row: int | None = None, **detail: Any) -> None:
        """Record something that could not be resolved deterministically.

        Such a row must reach the Rule Engine as UNKNOWN, never as PASS.
        """
        self.issues.append(
            ValidationIssue(code=f"UNKNOWN_{code}", message=message, row=row, detail=detail)
        )
        self.unknowns += 1


@runtime_checkable
class Parser(Protocol):
    """File -> structured rows. Must not invent values that are not in the file."""

    name: str

    def parse(self, archive: RawArchive) -> list[dict[str, Any]]: ...


@runtime_checkable
class Normalizer(Protocol):
    """Structured rows -> database-ready payloads (codes resolved, units fixed)."""

    def normalize(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


@runtime_checkable
class Validator(Protocol):
    """Database-ready payloads -> a report the operator can act on."""

    def validate(self, rows: list[dict[str, Any]]) -> ValidationReport: ...


@dataclass(frozen=True, slots=True)
class IngestionPlan:
    """Wiring for one official file type."""

    name: str
    parser: Parser
    normalizer: Normalizer
    validator: Validator
    target_table: str


__all__ = [
    "DEMO_QUALITY",
    "IngestionPlan",
    "Normalizer",
    "Parser",
    "RawArchive",
    "ValidationIssue",
    "ValidationReport",
    "Validator",
    "file_checksum",
]
