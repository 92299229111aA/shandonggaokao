"""CP-SAT portfolio optimizer.

    MAXIMIZE  PreferenceUtility + ConfidenceBonus
            - RiskBucketDeviation
            - UniversityConcentrationPenalty
            - MajorConcentrationPenalty

Hard constraints (never relaxed):
  * `x_i = 0` for every FAIL candidate and every user hard-excluded plan
  * `sum(x_i) <= application_limit` (the limit comes from the caller, which reads
    it from `admission_cycles`)
  * `min_safe_count`, counted only over items that can back a 保底 slot
    (UNKNOWN eligibility and the hard-excluded never count)

Soft: the risk-bucket targets. Falling short of a target costs
`deviation_weight * (target - count)^2`, so a large shortfall is punished far more
than a small one, but the solver is never forced to invent unacceptable options.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from ortools.sat.python import cp_model

from app.common.enums import RiskBucket
from app.optimizer.types import (
    ACS_SAFE_FLOOR,
    ACS_STABLE_FLOOR,
    CandidateItem,
    OptimizationRequest,
    OptimizationResult,
    OptimizerConfig,
)

logger = logging.getLogger(__name__)

# Canonical bucket order; every risk mode targets exactly these five.
RISK_BUCKET_ORDER: tuple[str, ...] = (
    RiskBucket.HIGH_REACH.value,
    RiskBucket.REACH.value,
    RiskBucket.BORDER.value,
    RiskBucket.STABLE.value,
    RiskBucket.SAFE.value,
)

BUCKET_LABELS: dict[str, str] = {
    RiskBucket.HIGH_REACH.value: "高冲",
    RiskBucket.REACH.value: "冲",
    RiskBucket.BORDER.value: "边界",
    RiskBucket.STABLE.value: "稳",
    RiskBucket.SAFE.value: "保",
}

# Integer scaling: CP-SAT works on integers, so utilities are scaled up.
UTILITY_SCALE = 10_000


class PortfolioOptimizer:
    def __init__(self, config: OptimizerConfig | None = None) -> None:
        self.config = config or OptimizerConfig()

    # ---------------- public API ----------------

    def _trim_pool(
        self, candidates: list[CandidateItem], request: OptimizationRequest
    ) -> tuple[list[CandidateItem], dict[str, Any]]:
        """Shrink the pool to a size CP-SAT can actually solve.

        The recommendation pipeline persists every eligible plan as a candidate item,
        which on the real dataset is ~15,000 rows. Handing all of them to the solver
        as boolean variables does not finish inside the time limit — OR-Tools returns
        UNKNOWN, and the user sees an empty scheme.

        Trimming keeps a multiple of each bucket's target, because the risk-distribution
        objective needs roughly `target` items per bucket and a little headroom to
        choose well. Within a bucket the best candidates by preference (then ACS) are
        kept, and locked or explicitly required items are never dropped.
        """
        headroom = request.config.pool_trim_headroom
        targets = request.targets_for()
        keep_ids = set(request.required_item_ids)

        by_bucket: dict[str, list[CandidateItem]] = defaultdict(list)
        for candidate in candidates:
            if candidate.item_id in keep_ids:
                continue
            by_bucket[candidate.risk_bucket or "UNKNOWN"].append(candidate)

        kept: list[CandidateItem] = [c for c in candidates if c.item_id in keep_ids]
        dropped = 0
        for bucket, items in by_bucket.items():
            items.sort(
                key=lambda c: (-c.normalized_preference(), -c.acs_value())
            )
            limit = max(targets.get(bucket, 0), 1) * headroom
            kept.extend(items[:limit])
            dropped += max(0, len(items) - limit)

        return kept, {
            "candidates_before_trim": len(candidates),
            "candidates_after_trim": len(kept),
            "candidates_dropped": dropped,
            "trim_headroom": headroom,
        }

    def optimize(self, request: OptimizationRequest) -> OptimizationResult:
        config = request.config
        all_eligible = [
            candidate
            for candidate in request.candidates
            if not candidate.is_fail and candidate.eligibility != "FAIL"
        ]
        excluded_count = len(request.candidates) - len(all_eligible)
        candidates, trim_report = self._trim_pool(all_eligible, request)

        if not candidates:
            return OptimizationResult(
                selected_item_ids=(),
                objective_value=0.0,
                status="INFEASIBLE",
                shortfall=request.max_count,
                relax_suggestions=(
                    "没有任何满足报考资格的志愿可选。请检查选科、体检等硬性条件，"
                    "或放宽专业/地域/学费的硬性排除条件。",
                ),
                diagnostics={"eligible_candidates": 0, "excluded_by_eligibility": excluded_count},
            )

        model = cp_model.CpModel()
        variables = [model.NewBoolVar(f"x_{candidate.item_id}") for candidate in candidates]

        # ---- objective terms ----
        objective_terms = self._preference_and_confidence_terms(model, candidates, variables)
        bucket_vars, deviation_terms, targets = self._deviation_terms(model, candidates, variables, request)
        objective_terms += deviation_terms
        university_terms, university_penalty_expr = self._concentration_terms(
            model,
            candidates,
            variables,
            key=lambda candidate: candidate.university_id,
            cap=config.university_soft_cap,
            weight=config.university_concentration_weight,
            prefix="uni",
        )
        major_terms, major_penalty_expr = self._concentration_terms(
            model,
            candidates,
            variables,
            key=lambda candidate: candidate.major_id if candidate.major_id is not None else -(candidate.plan_id),
            cap=config.major_soft_cap,
            weight=config.major_concentration_weight,
            prefix="maj",
        )
        objective_terms += university_terms + major_terms

        # ---- hard constraints ----
        total = model.NewIntVar(0, request.max_count, "total_selected")
        model.Add(total == sum(variables))
        model.Add(total <= request.max_count)

        # User-locked volunteers are forced into the solution *inside* the model, so
        # the count cap still holds. (Appending them after solving would break it.)
        required = request.required_item_ids
        if required:
            by_id = {candidate.item_id: variable for candidate, variable in zip(candidates, variables, strict=True)}
            missing = [item_id for item_id in required if item_id not in by_id]
            if missing:
                return OptimizationResult(
                    selected_item_ids=(),
                    objective_value=0.0,
                    status="INFEASIBLE",
                    shortfall=request.max_count,
                    relax_suggestions=(
                        "被锁定的志愿不在可入选范围内（可能资格不符或未进入候选池）："
                        f"{sorted(missing)[:10]}",
                    ),
                    diagnostics={"required_not_candidate": sorted(missing)},
                )
            if len(required) > request.max_count:
                return OptimizationResult(
                    selected_item_ids=(),
                    objective_value=0.0,
                    status="INFEASIBLE",
                    shortfall=request.max_count,
                    relax_suggestions=(
                        f"锁定的志愿有 {len(required)} 个，超过目标数量 {request.max_count} 个，"
                        "请先解锁部分志愿或提高目标数量",
                    ),
                    diagnostics={"required_count": len(required), "max_count": request.max_count},
                )
            for item_id in required:
                model.Add(by_id[item_id] == 1)

        self._add_safety_floor(model, candidates, variables, request)

        model.Maximize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = config.max_time_seconds
        solver.parameters.num_search_workers = config.num_workers
        solver.parameters.random_seed = config.random_seed
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # UNKNOWN means the solver gave up (time limit, or workers ran out of
            # memory on a large model) — NOT that no solution exists. Reporting it as
            # infeasibility sends the user off to loosen a constraint that was fine.
            timed_out = status == cp_model.UNKNOWN
            suggestion = (
                f"求解器在 {config.max_time_seconds:.0f} 秒内未能求解"
                f"（{len(candidates):,} 个候选志愿），本次未产出方案。"
                "可重试一次；若持续失败，请减少候选数量或调高 max_time_seconds。"
                if timed_out
                else "在当前约束下没有可行解（通常是“最低安全数量”高于实际可选数量）。"
                "请降低最低安全数量或增加候选志愿。"
            )
            return OptimizationResult(
                selected_item_ids=(),
                objective_value=0.0,
                status=solver.StatusName(status),
                shortfall=request.max_count,
                relax_suggestions=(suggestion,),
                solver_wall_time=solver.WallTime(),
                diagnostics={
                    "eligible_candidates": len(candidates),
                    "solver_status": solver.StatusName(status),
                    "timed_out": timed_out,
                    "max_time_seconds": config.max_time_seconds,
                    **trim_report,
                },
            )

        selected = [
            candidate
            for candidate, variable in zip(candidates, variables, strict=True)
            if solver.Value(variable) == 1
        ]
        risk_counts = dict(Counter(candidate.risk_bucket or "UNKNOWN" for candidate in selected))

        deviation_penalty = self._evaluate_deviation(solver, bucket_vars, targets)
        university_penalty = float(solver.Value(university_penalty_expr))
        major_penalty = float(solver.Value(major_penalty_expr))
        preference_utility = sum(
            candidate.normalized_preference() for candidate in selected
        )
        confidence_bonus = config.confidence_weight * sum(
            candidate.normalized_confidence() for candidate in selected
        )

        shortfall = max(0, request.max_count - len(selected))
        return OptimizationResult(
            selected_item_ids=tuple(candidate.item_id for candidate in selected),
            objective_value=solver.ObjectiveValue() / UTILITY_SCALE,
            status=solver.StatusName(status),
            risk_counts=risk_counts,
            target_counts=targets,
            deviation_penalty=deviation_penalty / UTILITY_SCALE,
            university_penalty=university_penalty / UTILITY_SCALE,
            major_penalty=major_penalty / UTILITY_SCALE,
            preference_utility=preference_utility,
            confidence_bonus=confidence_bonus,
            shortfall=shortfall,
            relax_suggestions=self._relax_suggestions(
                request, selected, risk_counts, targets, excluded_count
            ),
            solver_wall_time=solver.WallTime(),
            diagnostics={
                "eligible_candidates": len(candidates),
                "excluded_by_eligibility": excluded_count,
                "solver_status": solver.StatusName(status),
                **trim_report,
            },
        )

    # ---------------- objective pieces ----------------

    def _preference_and_confidence_terms(
        self, model: cp_model.CpModel, candidates: list[CandidateItem], variables: list
    ) -> list:
        config = self.config
        terms: list = []
        for candidate, variable in zip(candidates, variables, strict=True):
            utility = candidate.normalized_preference() * config.preference_weight
            utility += config.confidence_weight * candidate.normalized_confidence()
            scaled = int(round(utility * UTILITY_SCALE))
            if scaled:
                terms.append(scaled * variable)
        return terms

    def _deviation_terms(
        self,
        model: cp_model.CpModel,
        candidates: list[CandidateItem],
        variables: list,
        request: OptimizationRequest,
    ) -> tuple[dict[str, cp_model.IntVar], list, dict[str, int]]:
        """Squared shortfall against each bucket target, as a soft constraint."""
        targets = request.targets_for()
        bucket_vars: dict[str, cp_model.IntVar] = {}
        terms: list = []
        weight = int(round(self.config.deviation_weight * UTILITY_SCALE))

        for bucket in RISK_BUCKET_ORDER:
            members = [variable for candidate, variable in zip(candidates, variables, strict=True)
                       if (candidate.risk_bucket or "") == bucket]
            count_var = model.NewIntVar(0, len(members), f"count_{bucket}")
            model.Add(count_var == sum(members))
            bucket_vars[bucket] = count_var

            target = int(targets.get(bucket, 0))
            if target <= 0:
                continue
            shortfall_var = model.NewIntVar(0, target, f"short_{bucket}")
            model.Add(shortfall_var >= target - count_var)
            # squared penalty, added as exact square via multiplication by itself
            square = model.NewIntVar(0, target * target, f"short_sq_{bucket}")
            model.AddMultiplicationEquality(square, [shortfall_var, shortfall_var])
            terms.append(-weight * square)
        return bucket_vars, terms, targets

    def _concentration_terms(
        self,
        model: cp_model.CpModel,
        candidates: list[CandidateItem],
        variables: list,
        *,
        key,
        cap: int,
        weight: float,
        prefix: str,
    ) -> tuple[list, cp_model.IntVar]:
        """Penalise stacking many volunteers on one university / major.

        The penalty is `weight * max(0, count - cap)`, i.e. it only bites beyond a
        soft cap, so a small scheme is never penalised for existing.
        """
        groups: dict[int, list] = defaultdict(list)
        for candidate, variable in zip(candidates, variables, strict=True):
            groups[key(candidate)].append(variable)

        scaled_weight = int(round(weight * UTILITY_SCALE))
        terms: list = []
        excess_vars: list[cp_model.IntVar] = []
        for group_key, members in groups.items():
            if len(members) <= cap:
                continue
            count_var = model.NewIntVar(0, len(members), f"{prefix}_count_{group_key}")
            model.Add(count_var == sum(members))
            excess = model.NewIntVar(0, len(members), f"{prefix}_excess_{group_key}")
            model.Add(excess >= count_var - cap)
            excess_vars.append(excess)
            if scaled_weight:
                terms.append(-scaled_weight * excess)
        total_excess = model.NewIntVar(0, sum(len(m) for m in groups.values()), f"{prefix}_excess_total")
        model.Add(total_excess == (sum(excess_vars) if excess_vars else 0))
        return terms, total_excess

    # ---------------- hard constraints ----------------

    @staticmethod
    def _add_safety_floor(
        model: cp_model.CpModel,
        candidates: list[CandidateItem],
        variables: list,
        request: OptimizationRequest,
    ) -> None:
        """`min_safe_count` counts only volunteers that can actually back a 保底 slot."""
        config = request.config
        floors: list[tuple[float, int]] = []
        if request.min_safe_count:
            floors.append((ACS_STABLE_FLOOR, request.min_safe_count))
        if config.min_acs_stable:
            floors.append((ACS_STABLE_FLOOR, config.min_acs_stable))
        if config.min_acs_safe:
            floors.append((ACS_SAFE_FLOOR, config.min_acs_safe))

        for floor_acs, required in floors:
            eligible = [
                variable
                for candidate, variable in zip(candidates, variables, strict=True)
                if candidate.counts_as_safe and candidate.acs_value() >= floor_acs
            ]
            # An impossible floor is clamped to what exists: the candidate must not
            # be handed an infeasible problem, but the shortfall is reported.
            model.Add(sum(eligible) >= min(required, len(eligible)))

    # ---------------- reporting ----------------

    @staticmethod
    def _evaluate_deviation(
        solver: cp_model.CpSolver, bucket_vars: dict[str, cp_model.IntVar], targets: dict[str, int]
    ) -> float:
        weight = 1.0
        total = 0.0
        for bucket, variable in bucket_vars.items():
            target = int(targets.get(bucket, 0))
            shortfall = max(0, target - solver.Value(variable))
            total += weight * shortfall * shortfall
        return total

    @staticmethod
    def _relax_suggestions(
        request: OptimizationRequest,
        selected: list[CandidateItem],
        risk_counts: dict[str, int],
        targets: dict[str, int],
        excluded_count: int,
    ) -> tuple[str, ...]:
        suggestions: list[str] = []
        if excluded_count:
            suggestions.append(
                f"有 {excluded_count} 个候选因资格或硬性排除条件被优化器直接排除（不可放宽）"
            )
        if len(selected) < request.max_count:
            suggestions.append(
                f"符合条件且真正可接受的志愿只有 {len(selected)} 个，"
                f"少于目标 {request.max_count} 个；系统不会为凑满数量加入你不接受的志愿。"
            )
        for bucket, target in targets.items():
            actual = risk_counts.get(bucket, 0)
            if target and actual < target:
                label = BUCKET_LABELS.get(bucket, bucket)
                suggestions.append(
                    f"“{label}”层目标 {target} 个，实际只有 {actual} 个；"
                    "可放宽地域/专业/学费限制或调整风险模式"
                )
        return tuple(suggestions)


__all__ = ["UTILITY_SCALE", "PortfolioOptimizer"]
