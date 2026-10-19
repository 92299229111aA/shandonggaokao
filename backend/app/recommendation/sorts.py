"""Candidate-list sort keys.

Kept next to the schemas rather than in the service so the API layer can declare the same
values in its query parameter, and a typo becomes a 422 instead of a silent fallback to
the default order.
"""

from __future__ import annotations

from enum import StrEnum


class ItemSort(StrEnum):
    """How the candidate pool is presented.

    This only changes *browsing* order. The optimizer and the final scheme ordering are a
    separate two-step concern and are not affected by it.
    """

    PREFERENCE = "PREFERENCE"
    ACS_DESC = "ACS_DESC"
    ACS_ASC = "ACS_ASC"
    TUITION_ASC = "TUITION_ASC"
    PLAN_COUNT_DESC = "PLAN_COUNT_DESC"
    # Round-robin across risk buckets, so the first page of a 19k-item pool contains a
    # usable mixture instead of one bucket. Needed because `preference_score` collapses to
    # a three-value 院校层次 mapping when no explicit preferences are set: ordering by it
    # put the 500 hardest 985 programmes first and hid every other bucket.
    RISK_MIX = "RISK_MIX"
