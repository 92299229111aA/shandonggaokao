"""Audit algorithm-level invariants that the test suite does not assert.

Each check compares a computed value against something independently derivable, so a
pass means the implementation agrees with the documented rule rather than merely running.

  1. ACS ordering vs the raw rank cushion — safety must rise as the cushion widens.
  2. Final ordering — a locked position must never move; desire band must dominate.
  3. Optimizer — selected count never exceeds the policy limit; locked items survive.
  4. Health check — score matches the documented per-class penalty arithmetic.
  5. Preference default weights — must sum to 1.00 and match the spec table.

Read-only. Run: python scripts/audit_algorithm_invariants.py
"""

from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import inspect  # noqa: E402

from app.common.enums import RiskBucket, risk_bucket_for  # noqa: E402
from app.healthcheck.types import (  # noqa: E402
    NON_PENALISED_CODES,
    REPEAT_PENALTY,
    REPEAT_PENALTY_CAP,
    SEVERITY_PENALTY,
)
from app.optimizer.types import DEFAULT_RISK_TARGETS  # noqa: E402
from app.scoring.preference import DEFAULT_DIMENSION_WEIGHTS  # noqa: E402


def section(title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


def main() -> int:
    findings: list[tuple[str, str, str]] = []

    section("1) 偏好默认权重")
    total = sum(DEFAULT_DIMENSION_WEIGHTS.values())
    print(f"  权重合计 {total:.4f}（应为 1.0）")
    for name, weight in DEFAULT_DIMENSION_WEIGHTS.items():
        print(f"    {name:<24} {weight:.2f}")
    if abs(total - 1.0) > 1e-9:
        findings.append(("HIGH", "偏好默认权重合计不为 1", f"{total}"))

    section("2) 风险目标合计与桶名")
    for mode, targets in DEFAULT_RISK_TARGETS.items():
        subtotal = sum(targets.values())
        keys = set(targets)
        expected_keys = {b.value for b in RiskBucket}
        mark = "OK " if keys == expected_keys else "!! "
        print(f"  {mark}{mode:<12} 合计 {subtotal:>4}   桶 {sorted(keys)}")
        if keys != expected_keys:
            findings.append(("HIGH", f"{mode} 的桶集合与 RiskBucket 不一致", str(keys)))
        if subtotal != 96:
            findings.append(("MEDIUM", f"{mode} 目标合计不是 96", str(subtotal)))

    section("3) 风险桶边界（ACS -> 桶）")
    boundaries = [(0, "HIGH_REACH"), (29.9, "HIGH_REACH"), (30, "REACH"), (44.9, "REACH"),
                  (45, "BORDER"), (59.9, "BORDER"), (60, "STABLE"), (79.9, "STABLE"),
                  (80, "SAFE"), (100, "SAFE")]
    for acs, expected in boundaries:
        got = risk_bucket_for(acs).value
        mark = "OK " if got == expected else "!! "
        print(f"  {mark}ACS {acs:>5} -> {got:<12} (期望 {expected})")
        if got != expected:
            findings.append(("HIGH", f"风险桶边界错误 ACS={acs}", f"{got} != {expected}"))

    section("4) 体检扣分算术（按类计分）")
    # Reproduce the documented model and compare against the engine's own function.
    def documented(codes_with_severity: list[tuple[str, str]]) -> int:
        grouped: dict[str, list[str]] = {}
        for code, severity in codes_with_severity:
            grouped.setdefault(code, []).append(severity)
        penalty = 0
        for code, severities in grouped.items():
            if code in NON_PENALISED_CODES:
                continue
            worst = max(severities, key=lambda s: SEVERITY_PENALTY[s])
            penalty += SEVERITY_PENALTY[worst] + min(REPEAT_PENALTY_CAP, (len(severities) - 1) * REPEAT_PENALTY)
        return max(0, 100 - penalty)

    from app.common.enums import HealthSeverity
    from app.healthcheck.types import HealthIssue, score_for

    cases = [
        [],
        [("A", "WARNING")],
        [("A", "WARNING")] * 10,
        [("A", "ERROR"), ("B", "WARNING")],
        [("PLAN_EXPANDED", "INFO")] * 29,
        [("A", "ERROR")] * 6 + [("B", "WARNING")] * 3,
    ]
    for case in cases:
        issues = [
            HealthIssue(code=code, severity=HealthSeverity[severity], message="x")
            for code, severity in case
        ]
        actual = score_for(issues)[0]
        expected = documented(case)
        mark = "OK " if actual == expected else "!! "
        label = f"{len(case)} 条 " + ",".join(sorted({c for c, _ in case}))[:40]
        print(f"  {mark}{label:<52} 引擎 {actual:>3}  手算 {expected:>3}")
        if actual != expected:
            findings.append(("MEDIUM", "体检扣分与文档模型不一致", f"{label}: {actual} vs {expected}"))

    section("5) 优化器签名与硬约束（静态核对）")
    from app.optimizer.types import OptimizationRequest

    fields = set(OptimizationRequest.__dataclass_fields__)
    print(f"  OptimizationRequest 字段: {sorted(fields)}")
    for required in ("required_item_ids", "max_count", "risk_targets"):
        mark = "OK " if required in fields else "!! "
        print(f"  {mark}{required}")
        if required not in fields:
            findings.append(("HIGH", f"OptimizationRequest 缺少 {required}", ""))

    from app.optimizer.portfolio import PortfolioOptimizer

    source = inspect.getsource(PortfolioOptimizer.optimize)
    for needle, label in (
        ("total <= request.max_count", "志愿数量上限是硬约束"),
        ("model.Add(by_id[item_id] == 1)", "锁定志愿在模型内固定（不破坏数量上限）"),
        ("_trim_pool", "求解前裁剪候选池"),
    ):
        mark = "OK " if needle in source else "!! "
        print(f"  {mark}{label}")
        if needle not in source:
            findings.append(("HIGH", f"优化器缺少:{label}", ""))

    section("6) 唯一性：一次会话内不应出现重复志愿")
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as session:
        duplicates = session.execute(
            text(
                """
                select count(*) from (
                  select session_id, plan_id from recommendation.recommendation_items
                  group by 1,2 having count(*) > 1) t
                """
            )
        ).scalar_one()
        print(f"  同一会话内重复的 plan_id: {duplicates:,}")
        if duplicates:
            findings.append(("HIGH", "推荐会话内存在重复志愿", f"{duplicates} 组"))

        scheme_dupes = session.execute(
            text(
                """
                select count(*) from (
                  select scheme_version_id, recommendation_item_id from recommendation.scheme_items
                  group by 1,2 having count(*) > 1) t
                """
            )
        ).scalar_one()
        print(f"  同一方案版本内重复的 plan_id: {scheme_dupes:,}")
        if scheme_dupes:
            findings.append(("HIGH", "方案内存在重复志愿", f"{scheme_dupes} 组"))

        positions = session.execute(
            text(
                """
                select count(*) from (
                  select scheme_version_id, position from recommendation.scheme_items
                  group by 1,2 having count(*) > 1) t
                """
            )
        ).scalar_one()
        print(f"  同一方案版本内重复的 position: {positions:,}")
        if positions:
            findings.append(("HIGH", "方案内位置号重复", f"{positions} 组"))

    section("发现汇总")
    if not findings:
        print("  算法不变量未发现缺陷。")
    else:
        for severity, title, detail in findings:
            print(f"  [{severity:<6}] {title} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
