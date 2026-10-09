"""Dev probe: what ACS is actually reachable under the published parameters?

d_y is clipped to +/-0.30 and ACS is then shrunk toward 50 by confidence, so the
attainable range is narrower than the 0-100 scale suggests. This prints the ceiling
so the risk-bucket boundaries can be sanity-checked against reality.
"""

import math

CLIP = 0.30
GAIN = 35.0
SCALE = 0.07
HISTORY_ACS_AT_CLIP = 50 + GAIN * math.tanh(CLIP / SCALE)

print(f"d clipped at +/-{CLIP}")
print(f"HistoryACS at the clip       : {HISTORY_ACS_AT_CLIP:.2f}")
print()

print(f"{'confidence':>10} {'ACS(no adj)':>12} {'ACS(+7 plan)':>13} {'ACS(+13 both)':>14}")
print("-" * 54)
for confidence in (0.50, 0.60, 0.70, 0.79, 0.90, 1.00):
    base = HISTORY_ACS_AT_CLIP
    plain = 50 + (base - 50) * confidence
    with_plan = 50 + (min(100.0, base + 7.0) - 50) * confidence
    with_both = 50 + (min(100.0, base + 13.0) - 50) * confidence
    print(f"{confidence:>10.2f} {plain:>12.1f} {with_plan:>13.1f} {with_both:>14.1f}")

print()
print("Bucket boundaries: HIGH_REACH<30, REACH<45, BORDER<60, STABLE<80, SAFE>=80")
print()
print("Implication: with a realistic confidence of ~0.79, even the easiest possible")
print("program (d at the +0.30 clip, plus a plan expansion) lands around 75-78, i.e.")
print("STABLE. SAFE (>=80) requires both the clip AND confidence near 1.0, which")
print("needs a perfect dataset. The default risk targets that ask for 'ACS>=80'")
print("counts (e.g. 稳健 20 个) are therefore not reachable as written.")
