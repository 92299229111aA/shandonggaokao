"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { AcsMeter, ConfidenceBadge, EligibilityBadge, RiskBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { KeyValueList, Modal } from "@/components/ui/modal";
import { recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type { RecommendationItem } from "@/lib/api/types";
import { ACS_DISCLAIMER, RISK_LABELS, RISK_ORDER, formatRank } from "@/lib/utils";

export default function RecommendationsPage() {
  const router = useRouter();
  const { state, update, loaded } = useFlowState();
  const [riskFilter, setRiskFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [explain, setExplain] = useState<RecommendationItem | null>(null);

  const items = useQuery({
    queryKey: ["recommendation-items", state.sessionId, riskFilter, search],
    queryFn: () =>
      recommendationApi.items(state.sessionId!, {
        limit: 200,
        risk_bucket: riskFilter || undefined,
        search: search || undefined,
      }),
    enabled: loaded && Boolean(state.sessionId),
  });

  const createScheme = useMutation({
    mutationFn: () => schemeApi.create({ recommendation_session_id: state.sessionId! }),
    onSuccess: (scheme) => {
      update({ schemeId: scheme.id });
      router.push("/schemes");
    },
  });

  const summary = useMemo(() => {
    const distribution = items.data?.risk_distribution ?? {};
    return RISK_ORDER.map((bucket) => ({ bucket, count: distribution[bucket] ?? 0 }));
  }, [items.data?.risk_distribution]);

  if (loaded && !state.sessionId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>还没有推荐结果</CardTitle>
          <CardDescription>请先完成成绩与偏好填写，系统会生成候选志愿池。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push("/onboarding/score")}>去填写成绩与选科</Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">第 3 步 · 推荐结果</h1>
        <p className="text-sm text-muted-foreground">{ACS_DISCLAIMER}</p>
      </header>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">候选池分布</CardTitle>
          <CardDescription>
            共 {items.data?.pool_size ?? 0} 个符合条件的志愿。
            资格不符的志愿不会出现在这里；标为「待确认要求」的志愿不会被算作保底。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          {summary.map(({ bucket, count }) => (
            <button
              key={bucket}
              type="button"
              onClick={() => setRiskFilter(riskFilter === bucket ? "" : bucket)}
              className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors ${
                riskFilter === bucket ? "border-primary bg-accent" : "border-input hover:bg-accent/60"
              }`}
            >
              <RiskBadge bucket={bucket} />
              <span className="tabular-nums">{count}</span>
            </button>
          ))}
          {riskFilter ? (
            <Button variant="ghost" size="sm" onClick={() => setRiskFilter("")}>
              清除筛选
            </Button>
          ) : null}
          <div className="ml-auto w-56">
            <Input
              placeholder="搜索学校或专业"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      {items.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(items.error)}
        </p>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(items.data?.items ?? []).map((item) => (
          <Card key={item.item_id} className="flex flex-col">
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <CardTitle className="text-sm leading-snug">{item.plan_name}</CardTitle>
                  <CardDescription className="text-xs">
                    {item.university_name}
                    {item.city_name ? ` · ${item.city_name}` : ""}
                  </CardDescription>
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
                <Button variant="outline" size="sm" className="w-full" onClick={() => setExplain(item)}>
                  查看解释
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {items.data && items.data.items.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>没有符合条件的志愿</CardTitle>
            <CardDescription>
              请放宽硬性排除条件（专业/地域/学费），或回到上一步调整偏好后重新生成。
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {createScheme.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(createScheme.error)}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          disabled={createScheme.isPending || !items.data?.items.length}
          onClick={() => createScheme.mutate()}
        >
          {createScheme.isPending ? "生成方案中…" : "用这些候选生成志愿方案"}
        </Button>
        <span className="text-xs text-muted-foreground">
          组合优化会同时考虑偏好、可信度、风险层目标与集中度，候选不足时不会凑数。
        </span>
      </div>

      <Modal
        open={explain !== null}
        onClose={() => setExplain(null)}
        title={explain ? `${explain.plan_name} · ${explain.university_name}` : ""}
      >
        {explain ? (
          <div className="space-y-4 text-sm">
            <div>
              <h3 className="mb-2 font-medium">录取安全指数（ACS）</h3>
              <KeyValueList
                entries={[
                  ["ACS（安全指数）", explain.acs?.toFixed(1) ?? "—"],
                  ["Base ACS（未按可信度收缩）", explain.base_acs?.toFixed(1) ?? "—"],
                  ["数据可信度", explain.confidence?.toFixed(0) ?? "—"],
                  ["风险层", RISK_LABELS[explain.risk_bucket ?? "UNKNOWN"]],
                  ["历史覆盖年数", String((explain.acs_explanation?.history_points as number) ?? "—")],
                  ["使用年份", ((explain.acs_explanation?.years_used as number[]) ?? []).join("、") || "—"],
                ]}
              />
              <p className="mt-2 text-xs text-muted-foreground">{ACS_DISCLAIMER}</p>
            </div>

            <div>
              <h3 className="mb-2 font-medium">计算明细</h3>
              <KeyValueList
                entries={[
                  ["历史加权均值 M", String(explain.acs_explanation?.weighted_mean_d ?? "—")],
                  ["历史基础分", String(explain.acs_explanation?.history_acs ?? "—")],
                  ["招生计划调整", String(explain.acs_explanation?.plan_adjustment ?? "—")],
                  ["趋势调整", String(explain.acs_explanation?.trend_adjustment ?? "—")],
                  ["考生位次百分位", String(explain.acs_explanation?.q_candidate ?? "—")],
                ]}
              />
              {Array.isArray(explain.acs_explanation?.warnings) &&
              (explain.acs_explanation?.warnings as string[]).length ? (
                <ul className="mt-2 space-y-1 text-xs text-amber-800">
                  {(explain.acs_explanation?.warnings as string[]).map((warning) => (
                    <li key={warning}>⚠ {warning}</li>
                  ))}
                </ul>
              ) : null}
            </div>

            <div>
              <h3 className="mb-2 font-medium">偏好匹配</h3>
              <KeyValueList
                entries={[
                  ["偏好总分", explain.preference_score?.toFixed(1) ?? "—"],
                  [
                    "各维度",
                    Object.entries(
                      (explain.preference_dimensions?.dimensions as Array<Record<string, unknown>>) ?? [],
                    )
                      .map(([, value]) => {
                        const row = value as { dimension?: string; score?: number; available?: boolean };
                        return row.available ? `${row.dimension}:${row.score}` : null;
                      })
                      .filter(Boolean)
                      .join(" / ") || "无可用维度",
                  ],
                ]}
              />
              <p className="mt-2 text-xs text-muted-foreground">
                缺失的偏好维度会从分母中剔除，不会按 0 分处理。
              </p>
            </div>

            <div>
              <h3 className="mb-2 font-medium">历年录取</h3>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-1">年份</th>
                    <th className="py-1">最低位次</th>
                    <th className="py-1">最低分</th>
                    <th className="py-1">计划</th>
                  </tr>
                </thead>
                <tbody>
                  {explain.history.map((row) => (
                    <tr key={row.exam_year} className="border-b last:border-0">
                      <td className="py-1">{row.exam_year}</td>
                      <td className="py-1 tabular-nums">{formatRank(row.min_rank)}</td>
                      <td className="py-1 tabular-nums">{row.min_score ?? "—"}</td>
                      <td className="py-1 tabular-nums">{row.plan_count ?? "—"}</td>
                    </tr>
                  ))}
                  {explain.history.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="py-2 text-muted-foreground">
                        没有可用的历史录取数据，ACS 取中性值 50。
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            {explain.requirement_notes.length ? (
              <div>
                <h3 className="mb-2 font-medium">报考要求提示</h3>
                <ul className="space-y-1 text-xs">
                  {explain.requirement_notes.map((note) => (
                    <li key={note}>· {note}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
