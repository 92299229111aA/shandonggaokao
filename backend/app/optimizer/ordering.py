"""Two-step finishing: CP-SAT picks the set, then we order it by real desire.

    1. 组合优化（CP-SAT）  → 选哪些志愿
    2. 最终排序            → 这些志愿按什么顺序填

Sort key (docs/ALGORITHM.md §13):

    1. 用户手动固定位置 (fixed_position, 越小越前)
    2. desire_band DESC        desire_band = floor(PreferenceScore / 3)
    3. ACS ASC                 （同一意愿层内，更难的先尝试）
    4. Preference Score DESC
    5. Confidence DESC
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.optimizer.types import OrderedItem, SchemeOrderItem
from app.scoring.preference import desire_band

# Fixed positions must win over everything, so they are ranked by position first
# and never compared on score.
_FIXED_GROUP = 0
_NORMAL_GROUP = 1


def _sort_key(item: SchemeOrderItem) -> tuple[int, int, int, float, float, float, int]:
    preference = item.preference_score if item.preference_score is not None else 0.0
    acs = item.acs if item.acs is not None else 100.0
    confidence = item.confidence if item.confidence is not None else 0.0
    if item.fixed_position and item.position is not None:
        # A pinned item keeps its absolute position; the tuple's later fields are
        # irrelevant because we assign positions explicitly afterwards.
        return (_FIXED_GROUP, item.position, 0, 0.0, 0.0, 0.0, item.item_id)
    return (
        _NORMAL_GROUP,
        0,
        -desire_band(preference),
        acs,
        -preference,
        -confidence,
        item.item_id,
    )


def order_items(items: Iterable[SchemeOrderItem]) -> list[OrderedItem]:
    """Assign 1-based positions to the selected volunteers."""
    materialised = list(items)
    if not materialised:
        return []

    pinned = sorted(
        (item for item in materialised if item.fixed_position and item.position is not None),
        key=lambda item: (item.position or 0, item.item_id),
    )
    # Two items cannot share a fixed position; later duplicates fall back to normal
    # ordering rather than silently overwriting one another.
    taken: set[int] = set()
    pinned_unique: list[SchemeOrderItem] = []
    for item in pinned:
        position = item.position or 0
        if position in taken or position < 1:
            continue
        taken.add(position)
        pinned_unique.append(item)

    pinned_ids = {item.item_id for item in pinned_unique}
    remaining = sorted(
        (item for item in materialised if item.item_id not in pinned_ids), key=_sort_key
    )

    total_slots = max(len(materialised), max(taken, default=0))
    ordered: list[OrderedItem] = []
    pinned_by_position = {
        int(item.position): item for item in pinned_unique if item.position is not None
    }

    cursor = 0
    for position in range(1, total_slots + 1):
        candidate: SchemeOrderItem | None = pinned_by_position.get(position)
        if candidate is None:
            if cursor >= len(remaining):
                continue
            candidate = remaining[cursor]
            cursor += 1
        ordered.append(_to_ordered(candidate, position))

    # Any leftovers (shouldn't happen, but never drop a volunteer silently)
    while cursor < len(remaining):
        ordered.append(_to_ordered(remaining[cursor], len(ordered) + 1))
        cursor += 1

    return ordered


def _to_ordered(item: SchemeOrderItem, position: int) -> OrderedItem:
    preference = item.preference_score if item.preference_score is not None else 0.0
    return OrderedItem(
        item_id=item.item_id,
        plan_id=item.plan_id,
        position=position,
        fixed_position=bool(item.fixed_position and item.position == position),
        locked=item.locked,
        desire_band=desire_band(preference),
        acs=item.acs if item.acs is not None else 100.0,
        preference_score=preference,
        confidence=item.confidence if item.confidence is not None else 0.0,
        eligibility=item.eligibility,
    )


def reorder_after_removal(items: Sequence[SchemeOrderItem]) -> list[OrderedItem]:
    """Removing an entry compacts positions but keeps pinned items where they fit."""
    remaining = [item for item in items if not item.fixed_position]
    pinned = [item for item in items if item.fixed_position and item.position]
    merged: list[SchemeOrderItem] = [*remaining, *pinned]
    return order_items(merged)


__all__ = ["order_items", "reorder_after_removal"]
