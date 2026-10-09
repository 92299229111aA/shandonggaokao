"""List every Health Check issue code with its severity, to audit the penalty model.

A scoring model should penalise problems and not reward-side facts. This prints what
each check can emit so the severity assignments can be reviewed against the data.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
SOURCE = BACKEND / "app" / "healthcheck" / "checks.py"

TEXT = SOURCE.read_text(encoding="utf-8")

# code="X", severity=HealthSeverity.Y   (possibly with fields between)
PATTERN = re.compile(
    r'code="(\w+)"[\s\S]{0,180}?severity=(?:HealthSeverity\.(\w+)|(\w+))',
)


def main() -> int:
    seen: dict[str, str] = {}
    for match in PATTERN.finditer(TEXT):
        code = match.group(1)
        severity = match.group(2) or f"<{match.group(3)}>"
        seen.setdefault(code, severity)

    print(f"{'code':<26}{'severity':<14}说明")
    print("-" * 78)
    notes = {
        "PLAN_EXPANDED": "招生计划扩张 —— 对考生有利，不应扣分",
        "LIMIT_UNUSED": "志愿未填满上限 —— 可改进，但不是缺陷",
        "PLAN_COUNT_ZERO": "计划为 0，无法填报",
        "SMALL_PLAN_COUNT": "招生人数过少",
        "PLAN_REDUCED": "计划缩减",
        "LOW_CONFIDENCE": "数据可信度低",
    }
    for code, severity in sorted(seen.items()):
        print(f"{code:<26}{severity:<14}{notes.get(code, '')}")
    print()
    print(f"共 {len(seen)} 个 issue code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
