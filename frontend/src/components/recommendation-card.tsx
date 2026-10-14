"use client";

import { AcsMeter, ConfidenceBadge, EligibilityBadge, RiskBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RecommendationItem } from "@/lib/api/types";
import { formatRank } from "@/lib/utils";

/** One candidate志愿 card. Extracted so the list page can stay about filtering and paging. */
export function RecommendationCard({
  item,
  onExplain,
}: {
  item: RecommendationItem;
  onExplain: (item: RecommendationItem) => void;
}) {
  return (
    <Card className="flex flex-col" data-testid="recommendation-card">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm leading-snug" data-testid="card-title">
              {item.plan_name}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              {item.university_name}
              {item.city_name ? ` · ${item.city_name}` : ""}
            </p>
          </div>
          <RiskBadge bucket={item.risk_bucket} />
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {item.is_985 ? <Badge variant="secondary">985</Badge> : null}
          {item.is_211 ? <Badge variant="secondary">211</Badge> : null}
          {item.is_double_first_class ? <Badge variant="secondary">双一流</Badge> : null}
          {item.cooperative !== "NONE" ? <Badge variant="outline">合作办学</Badge> : null}
          {item.eligibility !== "PASS" ? <EligibilityBadge eligibility={item.eligibility} /> : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <AcsMeter acs={item.acs} />

        <div className="flex flex-wrap items-center gap-2">
          <ConfidenceBadge label={item.confidence_label} value={item.confidence} />
          <Badge variant="outline">
            偏好匹配 {item.preference_score !== null ? item.preference_score.toFixed(0) : "—"}
          </Badge>
        </div>

        <div className="space-y-1 text-xs text-muted-foreground">
          <div className="flex justify-between">
            <span>今年计划</span>
            <span className="tabular-nums text-foreground">{item.plan_count} 人</span>
          </div>
          {item.history.length ? (
            <div className="flex justify-between">
              <span>近年最低位次</span>
              <span className="tabular-nums text-foreground">
                {item.history
                  .slice(-3)
                  .map((row) => `${row.exam_year}:${formatRank(row.min_rank)}`)
                  .join(" / ")}
              </span>
            </div>
          ) : null}
          {item.plan_change_ratio !== null ? (
            <div className="flex justify-between">
              <span>计划变化</span>
              <span
                className={`tabular-nums ${
                  item.plan_change_ratio < 0 ? "text-destructive" : "text-[hsl(var(--risk-safe))]"
                }`}
              >
                {item.plan_change_ratio > 0 ? "+" : ""}
                {(item.plan_change_ratio * 100).toFixed(0)}%
              </span>
            </div>
          ) : null}
          <div className="flex justify-between">
            <span>学费</span>
            <span className="tabular-nums text-foreground">{item.tuition_fee} 元/年</span>
          </div>
        </div>

        {item.special_requirements || item.eligibility === "UNKNOWN" ? (
          <p className="rounded border border-[hsl(var(--risk-border))] bg-amber-50 px-2 py-1 text-xs text-amber-900">
            特殊要求：{item.special_requirements ?? "存在需人工核对的要求"}
          </p>
        ) : null}

        <div className="mt-auto">
          <Button variant="outline" size="sm" className="w-full" onClick={() => onExplain(item)}>
            查看解释
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
