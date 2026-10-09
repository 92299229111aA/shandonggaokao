"""score → rank conversion through the published 一分一段表.

V1 rule: a rank is only ever produced from data, never estimated by a model.
When the exact score row is missing we fall back to the nearest published row at
or above the score and say so in `source`/`note`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.admission.repository import CycleRepository, ScoreRankRepository
from app.candidate.schemas import ScoreRankResult
from app.common.errors import NotFoundError, RuleViolationError


class ScoreRankService:
    def __init__(self, session: Session) -> None:
        self.cycles = CycleRepository(session)
        self.segments = ScoreRankRepository(session)

    def convert(self, *, cycle_id: int, score: int) -> ScoreRankResult:
        cycle = self.cycles.get(cycle_id)
        if cycle is None:
            raise NotFoundError(f"招生周期 {cycle_id} 不存在")

        stats = self.segments.year_stats(exam_year=cycle.exam_year)
        ranked_candidates = stats.ranked_candidates if stats else None

        exact = self.segments.segment_for(exam_year=cycle.exam_year, score=score)
        if exact is not None:
            return ScoreRankResult(
                exam_year=cycle.exam_year,
                score=score,
                rank=exact.cumulative_rank,
                segment_rank=exact.cumulative_rank,
                same_score_count=exact.same_score_count,
                ranked_candidates=ranked_candidates,
                source="SEGMENT",
            )

        nearest = self.segments.nearest_segment_for(exam_year=cycle.exam_year, score=score)
        if nearest is not None:
            return ScoreRankResult(
                exam_year=cycle.exam_year,
                score=score,
                rank=nearest.cumulative_rank,
                segment_rank=nearest.cumulative_rank,
                same_score_count=nearest.same_score_count,
                ranked_candidates=ranked_candidates,
                source="INTERPOLATED",
                note=(
                    f"{score} 分无一分一段记录，使用相邻 {nearest.score} 分的累计位次"
                    f"（位次偏保守/偏大）"
                ),
            )

        raise RuleViolationError(
            f"{cycle.exam_year} 年没有可用的分数位次数据，无法由分数换算位次，请直接填写位次",
            details={"cycle_id": cycle_id, "score": score},
        )
