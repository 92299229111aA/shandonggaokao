"""Parse the 选科要求 column found in real Shandong plan files.

Measured against 79,279 rows of the 2022-2025 Shandong aggregate, the real values
come in three families:

1. Full names joined by 「和」 or 「或」
       物理和化学              → [[PHY, CHM]]         (all required)
       物理 或 化学             → [[PHY], [CHM]]       (either one)
2. Abbreviated single characters joined by punctuation
       物/化/生(3选1)          → [[PHY], [CHM], [BIO]]
       物、化、生(3科必选)     → [[PHY, CHM, BIO]]
3. Plain lists, sometimes with a trailing count note
       物理、化学(2科必选)     → [[PHY, CHM]]

The trailing parenthetical is a *count constraint*, not decoration:
「(N科必选)」 means all listed subjects are required, 「(N选1)」/「(N选M)」 means
choose any M of them. Getting this backwards would flip a plan between "eligible"
and "ineligible", so the note is parsed as part of the rule rather than stripped.

Anything the parser cannot map returns `None` so the caller can mark the rule set
`is_authoritative = False`, which makes the Rule Engine answer UNKNOWN. Guessing
here would silently mark an ineligible plan as eligible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical subject codes used across the system (candidate.subjects and
# subject_rule_items.subject_code share this vocabulary).
SUBJECT_CODE_BY_NAME: dict[str, str] = {
    # full names
    "物理": "PHY",
    "化学": "CHM",
    "生物": "BIO",
    "思想政治": "POL",
    "政治": "POL",
    "历史": "HIS",
    "地理": "GEO",
    # single-character abbreviations used by the abbreviated files
    "物": "PHY",
    "化": "CHM",
    "生": "BIO",
    "政": "POL",
    "史": "HIS",
    "地": "GEO",
    # not a 6-选-3 subject, but it appears in requirement text occasionally
    "技术": "TEC",
}

SUBJECT_NAME_BY_CODE: dict[str, str] = {
    "PHY": "物理",
    "CHM": "化学",
    "BIO": "生物",
    "POL": "思想政治",
    "HIS": "历史",
    "GEO": "地理",
    "TEC": "技术",
}

# Longest-first so 「思想政治」 wins over 「政治」 and 「物理」 over 「物」.
_SUBJECT_NAMES_SORTED = tuple(sorted(SUBJECT_CODE_BY_NAME, key=len, reverse=True))
_SUBJECT_PATTERN = "|".join(re.escape(name) for name in _SUBJECT_NAMES_SORTED)

# 「不限」 and its variants: no subject restriction at all.
_UNRESTRICTED_PATTERNS = (
    "不限",
    "不限选考科目",
    "不限科目",
    "不提科目要求",
    "均可",
    "无",
)

# Parenthetical count constraints. Captured, not discarded — see module docstring.
_COUNT_NOTE = re.compile(
    r"[（(]\s*(\d+)\s*(?:科|门)?\s*(?:科目)?\s*(?:考生)?\s*"
    r"(?:(必选|均须选考|均须选|须选考|均选|全选)|(?:选|选考)\s*(\d+)?)\s*"
    r"(?:方可报考)?\s*[)）]"
)

_AND_SEPARATORS = ("和", "与", "及", "、", "，", ",", "＋", "+", "且", "&", "；", ";")
_OR_SEPARATORS = ("或", "／", "/", "|", "任选其一", "任选一门", "任选")

# Sort separators longest-first so 「任选其一」 wins over 「任选」.
def _sorted_separators(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(items, key=len, reverse=True))


AND_TOKENS = _sorted_separators(_AND_SEPARATORS)
OR_TOKENS = _sorted_separators(_OR_SEPARATORS)


@dataclass(frozen=True, slots=True)
class ParsedSubjectRule:
    """DNF form: groups are OR-ed, subjects inside a group are AND-ed."""

    groups: tuple[tuple[str, ...], ...]
    mode: str  # "ALL" for a single AND group, "ANY" for OR-ed single-subject groups
    unrestricted: bool = False
    matched_subjects: tuple[str, ...] = field(default_factory=tuple)
    required_count: int | None = None
    raw_text: str | None = None

    def as_text(self) -> str:
        if self.unrestricted:
            return "不限"
        return " 或 ".join(
            "+".join(SUBJECT_NAME_BY_CODE.get(code, code) for code in group) for group in self.groups
        )


def _normalise(text: str) -> str:
    """Full-width → half-width for punctuation, collapse whitespace."""
    cleaned = text.strip()
    cleaned = cleaned.replace("（", "(").replace("）", ")").replace("，", ",")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


ABBREVIATION_NAMES = frozenset({"物", "化", "生", "政", "史", "地"})


def _extract_subjects(text: str) -> tuple[list[str], list[str]]:
    """Return (subject codes in order of appearance, text between them)."""
    matches = [
        (m.start(), m.group(0), SUBJECT_CODE_BY_NAME[m.group(0)]) for m in re.finditer(_SUBJECT_PATTERN, text)
    ]
    if not matches:
        return [], []

    subjects: list[str] = []
    separators: list[str] = []
    cursor = 0
    seen: set[str] = set()
    for position, name, code in matches:
        if position < cursor:
            continue  # overlapping match, already consumed by a longer name
        if code in seen:
            # The same subject listed twice carries no extra information; the
            # intervening text is still consumed so separators stay aligned.
            cursor = position + len(name)
            continue
        if subjects:
            separators.append(text[cursor:position])
        subjects.append(code)
        seen.add(code)
        cursor = position + len(name)
    return subjects, separators


def _abbreviation_is_authentic(text: str, subjects: list[str]) -> bool:
    """Guard against single characters of ordinary prose matching a subject.

    「详见招生章程」 contains 「详」 … but not 「物」; however 「物」 *is* a valid
    abbreviation, so any sentence containing that character would match. The real
    abbreviated files always list two or three of them separated by punctuation
    (「物/化/生」), so a lone abbreviation with no list context is refused.
    """
    if len(subjects) != 1:
        return True
    matches = [m.group(0) for m in re.finditer(_SUBJECT_PATTERN, text)]
    if not matches:
        return True
    # Only a concern when the single match came from a one-character name.
    return matches[0] not in ABBREVIATION_NAMES


def parse_subject_requirement(raw: str | None) -> ParsedSubjectRule | None:
    """Parse one 选科要求 cell.

    Returns None when the value cannot be mapped with certainty — the caller must
    record that as a non-authoritative rule rather than defaulting to "unrestricted".
    """
    if raw is None:
        return None
    # pandas hands back NaN (a float) for blank Excel cells.
    if not isinstance(raw, str):
        if isinstance(raw, float) and raw != raw:  # NaN
            return None
        raw = str(raw)
    text = _normalise(raw)
    if not text:
        return None

    # 1) explicit "no restriction" — checked first because "不限" would otherwise be
    #    run through the subject matcher and find no subjects.
    for pattern in _UNRESTRICTED_PATTERNS:
        if text == pattern or text.startswith(pattern):
            return ParsedSubjectRule(groups=(), mode="ALL", unrestricted=True, raw_text=raw)

    # 2) pull out the count constraint before matching subjects, since the
    #    parenthetical may itself contain digits only (e.g. "(3选1)").
    required_count: int | None = None
    explicit_all = False
    body = text
    note = _COUNT_NOTE.search(text)
    if note is not None:
        if note.group(2):  # 必选 / 均须选考 …
            explicit_all = True
            required_count = None
        else:
            required_count = int(note.group(3)) if note.group(3) else 1
        body = (text[: note.start()] + text[note.end() :]).strip()

    subjects, separators = _extract_subjects(body)
    if not subjects:
        return None
    if not _abbreviation_is_authentic(body, subjects):
        return None

    # 3) single subject
    if len(subjects) == 1:
        return ParsedSubjectRule(
            groups=((subjects[0],),),
            mode="ALL",
            matched_subjects=tuple(subjects),
            required_count=required_count,
            raw_text=raw,
        )

    # 4) A "N选1" / "N选M" count constraint OUTRANKS the separator heuristic.
    #    In "物/化/生(3选1)" the "/" would otherwise be read as an AND separator and
    #    the rule would demand all three subjects — the exact opposite of the text.
    if required_count is not None:
        if required_count <= 0 or required_count > len(subjects):
            return None
        if required_count == 1:
            return ParsedSubjectRule(
                groups=tuple((code,) for code in subjects),
                mode="ANY",
                matched_subjects=tuple(subjects),
                required_count=1,
                raw_text=raw,
            )
        # Choosing M of N (M > 1) cannot be expressed as plain DNF without
        # exploding into C(N, M) groups. Refuse so the plan is UNKNOWN rather
        # than silently over- or under-permissive.
        return None

    # 5) An explicit "all required" note: 「物、化、生(3科必选)」.
    if explicit_all:
        return ParsedSubjectRule(
            groups=(tuple(subjects),), mode="ALL", matched_subjects=tuple(subjects), raw_text=raw
        )

    # 6) Otherwise decide AND vs OR from the separators between subjects.
    has_or = False
    has_and = False
    for separator in separators:
        if any(token in separator for token in OR_TOKENS):
            has_or = True
        elif any(token in separator for token in AND_TOKENS) or separator == "":
            has_and = True
        else:
            # Unrecognised text between two subjects: refuse rather than guess.
            return None

    if has_or and has_and:
        # Mixed forms such as "(物理或历史)且化学" need a nested expression this
        # parser does not model. Refusing keeps the plan at UNKNOWN.
        return None

    if has_or:
        return ParsedSubjectRule(
            groups=tuple((code,) for code in subjects),
            mode="ANY",
            matched_subjects=tuple(subjects),
            raw_text=raw,
        )
    return ParsedSubjectRule(
        groups=(tuple(subjects),), mode="ALL", matched_subjects=tuple(subjects), raw_text=raw
    )


def _label(raw: object) -> str:
    """Human-readable label for a raw cell value, NaN included."""
    if raw is None:
        return "<空>"
    if isinstance(raw, float) and raw != raw:  # NaN
        return "<空>"
    text = str(raw).strip()
    return text or "<空>"


def coverage_report(values: list[str | None]) -> dict[str, object]:
    """Parse coverage over a real column, for the import report."""
    total = len(values)
    parsed: dict[str, int] = {}
    unparsed: dict[str, int] = {}
    subject_counts: dict[str, int] = {}
    for raw in values:
        result = parse_subject_requirement(raw)
        if result is None:
            key = _label(raw)
            unparsed[key] = unparsed.get(key, 0) + 1
            continue
        key = result.as_text()
        parsed[key] = parsed.get(key, 0) + 1
        for code in result.matched_subjects:
            subject_counts[code] = subject_counts.get(code, 0) + 1
    covered = total - sum(unparsed.values())
    return {
        "total": total,
        "parsed": covered,
        "unparsed": sum(unparsed.values()),
        "coverage": round(covered / total * 100, 4) if total else 0.0,
        "parsed_values": dict(sorted(parsed.items(), key=lambda kv: -kv[1])),
        "unparsed_values": dict(sorted(unparsed.items(), key=lambda kv: -kv[1])[:40]),
        "subject_counts": dict(sorted(subject_counts.items(), key=lambda kv: -kv[1])),
    }


__all__ = [
    "SUBJECT_CODE_BY_NAME",
    "SUBJECT_NAME_BY_CODE",
    "ParsedSubjectRule",
    "coverage_report",
    "parse_subject_requirement",
]
