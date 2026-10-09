"""Risk-bucket boundaries are part of the published algorithm contract."""

from __future__ import annotations

import pytest

from app.common.enums import RiskBucket, risk_bucket_for


@pytest.mark.parametrize(
    ("acs", "expected"),
    [
        (0.0, RiskBucket.HIGH_REACH),
        (15.0, RiskBucket.HIGH_REACH),
        (29.999, RiskBucket.HIGH_REACH),
        (30.0, RiskBucket.REACH),
        (44.999, RiskBucket.REACH),
        (45.0, RiskBucket.BORDER),
        (59.999, RiskBucket.BORDER),
        (60.0, RiskBucket.STABLE),
        (79.999, RiskBucket.STABLE),
        (80.0, RiskBucket.SAFE),
        (100.0, RiskBucket.SAFE),
    ],
)
def test_risk_bucket_boundaries(acs: float, expected: RiskBucket) -> None:
    assert risk_bucket_for(acs) == expected


def test_risk_bucket_is_total_over_range() -> None:
    for i in range(0, 1001):
        assert risk_bucket_for(i / 10) in set(RiskBucket)
