"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { RecommendationCard } from "@/components/recommendation-card";
import { RiskBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Input, Label, Select } from "@/components/ui/input";
import { KeyValueList, Modal } from "@/components/ui/modal";
import { catalogApi, recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type { RecommendationItem } from "@/lib/api/types";
import { ACS_DISCLAIMER, RISK_LABELS, RISK_ORDER, formatRank } from "@/lib/utils";

/** 50 per page: 志愿填报 is a comparison task, so position beats endless scrolling. */
const PAGE_SIZE = 50;

const SORT_OPTIONS = [
  { value: "PREFERENCE", label: "偏好匹配度（默认）" },
  { value: "ACS_DESC", label: "安全指数：从高到低" },
  { value: "ACS_ASC", label: "安全指数：从低到高" },
  { value: "TUITION_ASC", label: "学费：从低到高" },
  { value: "PLAN_COUNT_DESC", label: "招生人数：从多到少" },
];

interface Filters {
  riskBucket: string;
  provinceName: string;
  is985: boolean;
  is211: boolean;
  isDoubleFirstClass: boolean;
}

const EMPTY_FILTERS: Filters = {
  riskBucket: "",
  provinceName: "",
  is985: false,
  is211: false,
  isDoubleFirstClass: false,
};

function activeCount(filters: Filters): number {
  return (
    (filters.riskBucket ? 1 : 0) +
    (filters.provinceName ? 1 : 0) +
    (filters.is985 ? 1 : 0) +
    (filters.is211 ? 1 : 0) +
    (filters.isDoubleFirstClass ? 1 : 0)
  );
}

export default function RecommendationsPage() {
  const router = useRouter();
  const { state, update, loaded } = useFlowState();

  const [page, setPage] = useState(0);
  const [sort, setSort] = useState("PREFERENCE");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [explain, setExplain] = useState<RecommendationItem | null>(null);

  // Typing should not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(timer);
  }, [search]);

  // Any change to the query resets to the first page, otherwise the user lands on an
  // empty page after narrowing the result set.
  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, sort, filters]);

  const filterCount = activeCount(filters);

  const items = useQuery({
    queryKey: ["recommendation-items", state.sessionId, page, sort, debouncedSearch, filters],
    queryFn: () =>
      recommendationApi.items(state.sessionId!, {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort,
        search: debouncedSearch || undefined,
        risk_bucket: filters.riskBucket || undefined,
        province_name: filters.provinceName || undefined,
        is_985: filters.is985 || undefined,
        is_211: filters.is211 || undefined,
        is_double_first_class: filters.isDoubleFirstClass || undefined,
      }),
    enabled: loaded && Boolean(state.sessionId),
  });

  // Province list comes from the loaded snapshot rather than a hardcoded array.
  const options = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
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

  const total = items.data?.total ?? 0;
  const shown = items.data?.items.length ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = page * PAGE_SIZE + shown;

  return (
    <div className="space-y-5">
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
              onClick={() =>
                setFilters((previous) => ({
                  ...previous,
                  riskBucket: previous.riskBucket === bucket ? "" : bucket,
                }))
              }
              className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors ${
                filters.riskBucket === bucket
                  ? "border-primary bg-accent"
                  : "border-input hover:bg-accent/60"
              }`}
            >
              <RiskBadge bucket={bucket} />
              <span className="tabular-nums">{count}</span>
            </button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-5">
          <div className="min-w-[12rem] flex-1">
            <Input
              placeholder="搜索学校或专业（在整个候选池中搜索）"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          <div className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground">排序</Label>
            <Select className="w-52" value={sort} onChange={(event) => setSort(event.target.value)}>
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>

          <Button variant="outline" onClick={() => setFiltersOpen((open) => !open)}>
            筛选{filterCount ? ` (${filterCount})` : ""}
            <span className="ml-1 text-xs">{filtersOpen ? "▲" : "▼"}</span>
          </Button>

          {filterCount ? (
            <Button variant="ghost" size="sm" onClick={() => setFilters(EMPTY_FILTERS)}>
              清除筛选
            </Button>
          ) : null}
        </CardContent>

        {filtersOpen ? (
          <CardContent className="border-t pt-4">
            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-1.5">
                <Label className="text-xs">院校所在省</Label>
                <Select
                  value={filters.provinceName}
                  onChange={(event) =>
                    setFilters((previous) => ({ ...previous, provinceName: event.target.value }))
                  }
                >
                  <option value="">全部省份</option>
                  {(options.data?.provinces ?? []).map((province) => (
                    <option key={province.code} value={province.name}>
                      {province.name}（{province.university_count}）
                    </option>
                  ))}
                </Select>
              </div>

              <div className="space-y-2 md:col-span-2">
                <Label className="text-xs">院校层次</Label>
                <div className="flex flex-wrap gap-4 pt-1">
                  {(
                    [
                      ["is985", "只要 985"],
                      ["is211", "只要 211"],
                      ["isDoubleFirstClass", "只要双一流"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={filters[key]}
                        onChange={(event) =>
                          setFilters((previous) => ({ ...previous, [key]: event.target.checked }))
                        }
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </CardContent>
        ) : null}
      </Card>

      {state.inputWarnings.length ? (
        <Card className="border-amber-300 bg-amber-50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base text-amber-900">请先核对你的输入</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {state.inputWarnings.map((warning) => (
              <p key={warning} className="text-sm text-amber-900">
                ⚠ {warning}
              </p>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {items.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(items.error)}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {total === 0
            ? "没有符合条件的志愿"
            : `共 ${total.toLocaleString()} 条符合条件，当前显示第 ${rangeStart}–${rangeEnd} 条`}
        </p>
        {lastPage > 0 ? (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((current) => Math.max(0, current - 1))}
            >
              上一页
            </Button>
            <span className="text-sm tabular-nums text-muted-foreground">
              {page + 1} / {lastPage + 1}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= lastPage}
              onClick={() => setPage((current) => Math.min(lastPage, current + 1))}
            >
              下一页
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(items.data?.items ?? []).map((item) => (
          <RecommendationCard key={item.item_id} item={item} onExplain={setExplain} />
        ))}
      </div>

      {total > 0 && lastPage > 0 ? (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            上一页
          </Button>
          <span className="text-sm tabular-nums text-muted-foreground">
            第 {page + 1} / {lastPage + 1} 页
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= lastPage}
            onClick={() => setPage((current) => Math.min(lastPage, current + 1))}
          >
            下一页
          </Button>
        </div>
      ) : null}

      {items.data && shown === 0 && total === 0 && filterCount + (debouncedSearch ? 1 : 0) > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>当前筛选下没有志愿</CardTitle>
            <CardDescription>请放宽筛选条件，或清除筛选后重新浏览。</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setSearch("");
              }}
            >
              清除全部筛选
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {createScheme.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(createScheme.error)}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          disabled={createScheme.isPending || !state.sessionId}
          onClick={() => createScheme.mutate()}
        >
          {createScheme.isPending ? "生成方案中…" : "用这些候选生成志愿方案"}
        </Button>
        <span className="text-xs text-muted-foreground">
          组合优化会同时考虑偏好、可信度、风险层目标与集中度，候选不足时不会凑数。
          排序只影响浏览顺序，不改变方案结果。
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
              <h3 className="mb-2 font-medium">招生与历史</h3>
              <KeyValueList
                entries={[
                  ["院校专业序号", explain.major_sequence ?? "—"],
                  ["今年计划", `${explain.plan_count} 人`],
                  ["学费", `${explain.tuition_fee} 元/年`],
                  ["学制", `${explain.duration_years} 年`],
                  ["校区", explain.campus_name ?? "—"],
                  [
                    "近年最低位次",
                    explain.history.length
                      ? explain.history
                          .map((row) => `${row.exam_year}:${formatRank(row.min_rank)}`)
                          .join("、")
                      : "—",
                  ],
                ]}
              />
            </div>

            <div>
              <h3 className="mb-2 font-medium">资格与偏好</h3>
              <KeyValueList
                entries={[
                  ["资格结论", explain.eligibility],
                  ["选科要求", explain.subject_requirement_text ?? "—"],
                  ["特殊要求", explain.special_requirements ?? "—"],
                  ["合作办学", explain.cooperative === "NONE" ? "否" : explain.cooperative],
                ]}
              />
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
