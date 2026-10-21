"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { restrictToParentElement, restrictToVerticalAxis } from "@dnd-kit/modifiers";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { EligibilityBadge, RiskBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Input, Select } from "@/components/ui/input";
import { catalogApi, recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage, isVersionConflict } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type {
  RecommendationItem,
  RiskMode,
  Scheme,
  SchemeItem,
  UniversityGroup,
} from "@/lib/api/types";
import { ACS_DISCLAIMER, RISK_LABELS, RISK_ORDER } from "@/lib/utils";

/** Candidates per page in the pool. 50 matches the recommendations page. */
const POOL_PAGE_SIZE = 50;

/**
 * Bucket tabs with counts.
 *
 * The counts come from `bucket_counts`, which the server computes under the current
 * filters but *excluding* the bucket filter itself — otherwise selecting one bucket would
 * zero the others. `available` is what makes them trustworthy: items already in the scheme
 * are excluded server-side, so the number is what can actually be added.
 */
function BucketTabs({
  value,
  counts,
  onChange,
}: {
  value: string;
  counts: Record<string, number>;
  onChange: (bucket: string) => void;
}) {
  // Derive the "全部" figure from the same counts rather than from `total`: `total` already
  // has the bucket filter applied, so it would show the selected bucket's size here.
  const all = RISK_ORDER.reduce((sum, bucket) => sum + (counts[bucket] ?? 0), 0);
  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={() => onChange("")}
        className={`rounded-md border px-2 py-1 text-xs transition-colors ${
          value === "" ? "border-primary bg-accent" : "border-input hover:bg-accent/60"
        }`}
      >
        全部 <span className="tabular-nums opacity-70">{all}</span>
      </button>
      {RISK_ORDER.map((bucket) => {
        const count = counts[bucket] ?? 0;
        return (
          <button
            key={bucket}
            type="button"
            onClick={() => onChange(value === bucket ? "" : bucket)}
            className={`rounded-md border px-2 py-1 text-xs transition-colors ${
              value === bucket ? "border-primary bg-accent" : "border-input hover:bg-accent/60"
            } ${count === 0 ? "opacity-40" : ""}`}
          >
            {RISK_LABELS[bucket]} <span className="tabular-nums opacity-70">{count}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function SchemeEditorPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { state, loaded } = useFlowState();

  const [conflict, setConflict] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedPoolIds, setSelectedPoolIds] = useState<number[]>([]);
  const [riskMode, setRiskMode] = useState<RiskMode>("BALANCED");
  const [targetCount, setTargetCount] = useState<number | null>(null);
  const [minSafeCount, setMinSafeCount] = useState<string>("");
  const [localOrder, setLocalOrder] = useState<SchemeItem[] | null>(null);

  // ---- candidate pool filters (server-side) ----
  const [poolBucket, setPoolBucket] = useState("");
  const [poolSearch, setPoolSearch] = useState("");
  const [debouncedPoolSearch, setDebouncedPoolSearch] = useState("");
  const [poolProvince, setPoolProvince] = useState("");
  const [pool985, setPool985] = useState(false);
  const [pool211, setPool211] = useState(false);
  const [poolDfc, setPoolDfc] = useState(false);
  const [poolPage, setPoolPage] = useState(0);
  // "list" = one row per school×major; "school" = one row per university, expandable.
  const [poolView, setPoolView] = useState<"list" | "school">("list");
  const [expandedUniversity, setExpandedUniversity] = useState<number | null>(null);

  // ---- scheme panel filters (client-side: the scheme is at most a few dozen rows) ----
  const [schemeBucket, setSchemeBucket] = useState("");
  const [schemeSearch, setSchemeSearch] = useState("");
  const [schemeProvince, setSchemeProvince] = useState("");
  const [schemeUniversity, setSchemeUniversity] = useState("");
  const [schemeOnlyUnlocked, setSchemeOnlyUnlocked] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedPoolSearch(poolSearch), 350);
    return () => clearTimeout(timer);
  }, [poolSearch]);

  // Narrowing the pool must return to page 1, or the user lands on an empty page.
  useEffect(() => {
    setPoolPage(0);
  }, [poolBucket, debouncedPoolSearch, poolProvince, pool985, pool211, poolDfc, poolView]);

  // A collapsed row must not stay "expanded" once the filters change under it.
  useEffect(() => {
    setExpandedUniversity(null);
  }, [poolBucket, debouncedPoolSearch, poolProvince, pool985, pool211, poolDfc]);

  const scheme = useQuery({
    queryKey: ["scheme", state.schemeId],
    queryFn: () => schemeApi.get(state.schemeId!),
    enabled: loaded && Boolean(state.schemeId),
  });

  const pool = useQuery({
    // `schemeVersionNo` is part of the key: adding or removing a candidate changes what
    // the pool may offer, so the counts and pages must be refetched.
    queryKey: [
      "pool",
      state.sessionId,
      state.schemeId,
      scheme.data?.current_version_no,
      poolBucket,
      debouncedPoolSearch,
      poolProvince,
      pool985,
      pool211,
      poolDfc,
      poolPage,
    ],
    queryFn: () =>
      recommendationApi.items(state.sessionId!, {
        limit: POOL_PAGE_SIZE,
        offset: poolPage * POOL_PAGE_SIZE,
        // Round-robin across buckets: a plain sort always surfaces one bucket only.
        sort: "RISK_MIX",
        exclude_scheme_id: state.schemeId ?? undefined,
        risk_bucket: poolBucket || undefined,
        search: debouncedPoolSearch || undefined,
        province_name: poolProvince || undefined,
        is_985: pool985 || undefined,
        is_211: pool211 || undefined,
        is_double_first_class: poolDfc || undefined,
      }),
    enabled: loaded && Boolean(state.sessionId) && Boolean(state.schemeId),
  });

  const provinces = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
    enabled: loaded && Boolean(state.schemeId),
  });

  // Grouped view. Disabled while the flat view is active so switching back and forth does
  // not refetch a page nobody is looking at.
  const groups = useQuery({
    queryKey: [
      "pool-groups",
      state.sessionId,
      state.schemeId,
      scheme.data?.current_version_no,
      poolBucket,
      debouncedPoolSearch,
      poolProvince,
      pool985,
      pool211,
      poolDfc,
      poolPage,
    ],
    queryFn: () =>
      recommendationApi.universityGroups(state.sessionId!, {
        limit: POOL_PAGE_SIZE,
        offset: poolPage * POOL_PAGE_SIZE,
        exclude_scheme_id: state.schemeId ?? undefined,
        risk_bucket: poolBucket || undefined,
        search: debouncedPoolSearch || undefined,
        province_name: poolProvince || undefined,
        is_985: pool985 || undefined,
        is_211: pool211 || undefined,
        is_double_first_class: poolDfc || undefined,
      }),
    enabled:
      loaded && Boolean(state.sessionId) && Boolean(state.schemeId) && poolView === "school",
  });

  // Majors of the expanded school. Same filters as the pool, narrowed to one university.
  const expandedItems = useQuery({
    queryKey: [
      "pool-expanded",
      state.sessionId,
      state.schemeId,
      scheme.data?.current_version_no,
      expandedUniversity,
      poolBucket,
      debouncedPoolSearch,
      poolProvince,
      pool985,
      pool211,
      poolDfc,
    ],
    queryFn: () =>
      recommendationApi.items(state.sessionId!, {
        limit: 200,
        university_id: expandedUniversity!,
        sort: "RISK_MIX",
        exclude_scheme_id: state.schemeId ?? undefined,
        risk_bucket: poolBucket || undefined,
        search: debouncedPoolSearch || undefined,
        province_name: poolProvince || undefined,
        is_985: pool985 || undefined,
        is_211: pool211 || undefined,
        is_double_first_class: poolDfc || undefined,
      }),
    enabled: loaded && Boolean(state.sessionId) && expandedUniversity !== null,
  });

  useEffect(() => {
    if (scheme.data) {
      setLocalOrder(scheme.data.items);
      setRiskMode(scheme.data.risk_mode);
      setTargetCount(scheme.data.target_count);
    }
  }, [scheme.data]);

  const items = useMemo(
    () => localOrder ?? scheme.data?.items ?? [],
    [localOrder, scheme.data?.items],
  );

  // Declared before the reorder callbacks because they must refuse to move rows while a
  // filter is hiding part of the list. Drag indices come from the full list, so a drop
  // would silently move items the user cannot see.
  const schemeFilterCount =
    (schemeBucket ? 1 : 0) +
    (schemeProvince ? 1 : 0) +
    (schemeUniversity ? 1 : 0) +
    (schemeSearch.trim() ? 1 : 0) +
    (schemeOnlyUnlocked ? 1 : 0);
  const schemeFiltered = schemeFilterCount > 0;

  const handleError = useCallback((error: unknown) => {
    if (isVersionConflict(error)) {
      setConflict(errorMessage(error));
      return;
    }
    setNotice(errorMessage(error));
  }, []);

  const syncSuccess = useCallback(
    (updated: Scheme) => {
      setConflict(null);
      setNotice(updated.change_summary ?? null);
      setLocalOrder(updated.items);
      queryClient.setQueryData(["scheme", updated.id], updated);
      queryClient.invalidateQueries({ queryKey: ["scheme-versions", updated.id] });
    },
    [queryClient],
  );

  const reorder = useMutation({
    mutationFn: (payload: { order: SchemeItem[]; summary: string }) =>
      schemeApi.updateOrder(state.schemeId!, {
        version: scheme.data!.lock_version,
        change_summary: payload.summary,
        items: payload.order.map((item, index) => ({
          recommendation_item_id: item.recommendation_item_id,
          position: index + 1,
          locked: item.locked,
        })),
      }),
    onSuccess: syncSuccess,
    onError: (error) => {
      setLocalOrder(scheme.data?.items ?? null);
      handleError(error);
    },
  });

  const addItems = useMutation({
    mutationFn: (itemIds: number[]) =>
      schemeApi.addItems(state.schemeId!, {
        version: scheme.data!.lock_version,
        recommendation_item_ids: itemIds,
      }),
    onSuccess: (updated) => {
      syncSuccess(updated);
      setSelectedPoolIds([]);
    },
    onError: handleError,
  });

  const removeItem = useMutation({
    mutationFn: (position: number) =>
      schemeApi.removeItem(state.schemeId!, position, scheme.data!.lock_version),
    onSuccess: syncSuccess,
    onError: handleError,
  });

  const toggleLock = useMutation({
    mutationFn: (payload: { position: number; locked: boolean }) =>
      schemeApi.setLock(state.schemeId!, payload.position, scheme.data!.lock_version, payload.locked),
    onSuccess: syncSuccess,
    onError: handleError,
  });

  const optimize = useMutation({
    mutationFn: () =>
      schemeApi.optimize(state.schemeId!, {
        version: scheme.data!.lock_version,
        risk_mode: riskMode,
        target_count: targetCount ?? undefined,
        min_safe_count: minSafeCount === "" ? null : Number(minSafeCount),
      }),
    onSuccess: (updated) => {
      syncSuccess(updated);
      setNotice(`已重新优化：${updated.item_count} 个志愿，锁定志愿已保留。`);
    },
    onError: handleError,
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id || !scheme.data) return;
      // Guard as well as disabling the handle: drag indices are computed against the full
      // list while only the filtered rows are visible, so a drop would move rows the user
      // cannot see.
      if (schemeFiltered) return;
      const oldIndex = items.findIndex((item) => item.recommendation_item_id === active.id);
      const newIndex = items.findIndex((item) => item.recommendation_item_id === over.id);
      if (oldIndex < 0 || newIndex < 0) return;

      const next = arrayMove(items, oldIndex, newIndex);
      setLocalOrder(next);
      reorder.mutate({ order: next, summary: `拖拽调整：第 ${oldIndex + 1} 位移到第 ${newIndex + 1} 位` });
    },
    [items, reorder, scheme.data, schemeFiltered],
  );

  const move = useCallback(
    (position: number, direction: -1 | 1) => {
      if (schemeFiltered) return;
      const index = items.findIndex((item) => item.position === position);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= items.length) return;
      const next = arrayMove(items, index, target);
      setLocalOrder(next);
      reorder.mutate({
        order: next,
        summary: `上/下移：第 ${position} 位调整到第 ${target + 1} 位`,
      });
    },
    [items, reorder, schemeFiltered],
  );

  // The server already excludes items that are in the scheme, so no client-side filter is
  // needed here — and that matters once the pool is paged, because filtering the current
  // page would leave `total` and the page numbers describing a different set.
  const poolItems = pool.data?.items ?? [];
  const groupItems = groups.data?.items ?? [];
  // Paging counts whichever list is on screen: items in list view, universities in school
  // view. Using the item total for both would show far too many pages in school view.
  const poolTotal = poolView === "school" ? (groups.data?.total ?? 0) : (pool.data?.total ?? 0);
  const poolShown = poolView === "school" ? groupItems.length : poolItems.length;
  const poolLastPage = Math.max(0, Math.ceil(poolTotal / POOL_PAGE_SIZE) - 1);
  const poolRangeStart = poolTotal === 0 ? 0 : poolPage * POOL_PAGE_SIZE + 1;
  const poolRangeEnd = poolPage * POOL_PAGE_SIZE + poolShown;
  const poolLoading = poolView === "school" ? groups.isPending : pool.isPending;
  const poolUnit = poolView === "school" ? "所院校" : "条";

  const visibleSchemeItems = useMemo(() => {
    const needle = schemeSearch.trim();
    return items.filter((item) => {
      if (schemeBucket && item.risk_bucket !== schemeBucket) return false;
      if (schemeProvince && item.province_name !== schemeProvince) return false;
      if (schemeUniversity && item.university_name !== schemeUniversity) return false;
      if (schemeOnlyUnlocked && item.locked) return false;
      if (!needle) return true;
      return item.plan_name.includes(needle) || item.university_name.includes(needle);
    });
  }, [
    items,
    schemeBucket,
    schemeProvince,
    schemeUniversity,
    schemeSearch,
    schemeOnlyUnlocked,
  ]);

  const schemeBucketCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      const bucket = item.risk_bucket ?? "";
      if (bucket) counts[bucket] = (counts[bucket] ?? 0) + 1;
    }
    return counts;
  }, [items]);

  // Provinces and schools present in the scheme, with counts. The scheme holds at most a
  // few dozen rows, so a plain select is clearer than another search box.
  const schemeProvinceOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      if (!item.province_name) continue;
      counts.set(item.province_name, (counts.get(item.province_name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  const schemeUniversityOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      if (!item.university_name) continue;
      // Respect the province filter so the school list cannot offer an impossible choice.
      if (schemeProvince && item.province_name !== schemeProvince) continue;
      counts.set(item.university_name, (counts.get(item.university_name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items, schemeProvince]);

  const atCapacity = items.length >= (scheme.data?.target_count ?? 96);

  if (loaded && !state.schemeId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>还没有志愿方案</CardTitle>
          <CardDescription>请先生成推荐结果，再用候选志愿生成方案。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push("/recommendations")}>去看推荐结果</Button>
        </CardContent>
      </Card>
    );
  }

  if (scheme.isError) {
    return (
      <Card className="border-destructive">
        <CardHeader>
          <CardTitle className="text-destructive">无法读取方案</CardTitle>
          <CardDescription>{errorMessage(scheme.error)}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">第 4 步 · 志愿编辑器</h1>
          <p className="text-sm text-muted-foreground">
            {scheme.data
              ? `当前 v${scheme.data.current_version_no} · 共 ${scheme.data.item_count} 个志愿 · 上限 ${scheme.data.target_count} 个`
              : "加载中…"}
            {"　"}拖拽或使用上下移调整顺序，每次修改都会生成新版本。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <a href={`/schemes/versions`}>历史版本</a>
          </Button>
          <Button size="sm" onClick={() => router.push("/health-check")}>
            进入志愿体检
          </Button>
        </div>
      </header>

      {conflict ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-destructive bg-red-50 px-3 py-2 text-sm text-destructive">
          <span>{conflict}</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setConflict(null);
              queryClient.invalidateQueries({ queryKey: ["scheme", state.schemeId] });
            }}
          >
            刷新方案
          </Button>
        </div>
      ) : null}

      {notice && !conflict ? (
        <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{notice}</p>
      ) : null}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">重新优化</CardTitle>
          <CardDescription>
            锁定（🔒）的志愿在任何情况下都会被保留。资格待确认的志愿不会被计入保底数量。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">风险模式</label>
            <Select
              className="w-36"
              value={riskMode}
              onChange={(event) => setRiskMode(event.target.value as RiskMode)}
            >
              <option value="STEADY">稳健</option>
              <option value="BALANCED">均衡</option>
              <option value="AGGRESSIVE">激进</option>
            </Select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">目标数量</label>
            <Input
              className="w-28"
              type="number"
              min={1}
              value={targetCount ?? ""}
              onChange={(event) => setTargetCount(Number(event.target.value))}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">最低安全数量</label>
            <Input
              className="w-28"
              type="number"
              min={0}
              value={minSafeCount}
              onChange={(event) => setMinSafeCount(event.target.value)}
              placeholder="可留空"
            />
          </div>
          <Button disabled={optimize.isPending} onClick={() => optimize.mutate()}>
            {optimize.isPending ? "优化中…" : "重新优化"}
          </Button>
          {scheme.data?.optimizer_stats ? (
            <Badge variant="outline">
              上次优化：选中 {String(scheme.data.optimizer_stats.selected_count ?? "—")} 个
            </Badge>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.35fr]">
        <Card className="flex max-h-[75vh] flex-col">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">候选池（{poolItems.length}）</CardTitle>
            <CardDescription className="text-xs">
              资格不符的志愿不会出现在这里。已在方案内的志愿也不再显示。
            </CardDescription>

            <div className="space-y-2 pt-2">
              <BucketTabs
                value={poolBucket}
                counts={pool.data?.bucket_counts ?? {}}
                onChange={setPoolBucket}
              />

              <div className="flex gap-2">
                <Input
                  placeholder="搜索学校或专业（整个候选池）"
                  value={poolSearch}
                  onChange={(event) => setPoolSearch(event.target.value)}
                />
                <Button
                  size="sm"
                  disabled={!selectedPoolIds.length || addItems.isPending || atCapacity}
                  title={atCapacity ? "方案已满，请先删除或调高目标数量" : undefined}
                  onClick={() => addItems.mutate(selectedPoolIds)}
                >
                  加入({selectedPoolIds.length})
                </Button>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Select
                  className="w-36"
                  value={poolProvince}
                  onChange={(event) => setPoolProvince(event.target.value)}
                >
                  <option value="">全部省份</option>
                  {(provinces.data?.provinces ?? []).map((province) => (
                    <option key={province.code} value={province.name}>
                      {province.name}（{province.university_count}）
                    </option>
                  ))}
                </Select>
                {(
                  [
                    [pool985, setPool985, "985"],
                    [pool211, setPool211, "211"],
                    [poolDfc, setPoolDfc, "双一流"],
                  ] as const
                ).map(([checked, setter, label]) => (
                  <label key={label} className="flex items-center gap-1.5 text-xs">
                    <Checkbox
                      checked={checked}
                      onChange={(event) => setter(event.target.checked)}
                    />
                    {label}
                  </label>
                ))}
              </div>

              <p className="text-xs tabular-nums text-muted-foreground">
                {poolLoading
                  ? "正在读取候选池…"
                  : poolTotal === 0
                    ? "当前筛选下没有可加入的志愿"
                    : `可加入 ${poolTotal.toLocaleString()} ${poolUnit}，当前显示第 ${poolRangeStart}–${poolRangeEnd} 条`}
              </p>

              <div className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">视图</span>
                {(
                  [
                    ["list", "列表"],
                    ["school", "按学校"],
                  ] as const
                ).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setPoolView(mode)}
                    className={`rounded-md border px-2 py-1 text-xs transition-colors ${
                      poolView === mode
                        ? "border-primary bg-accent"
                        : "border-input hover:bg-accent/60"
                    }`}
                  >
                    {label}
                  </button>
                ))}
                {poolView === "school" ? (
                  <span className="text-xs text-muted-foreground">
                    （一校一行，点开看该校专业）
                  </span>
                ) : null}
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex-1 space-y-2 overflow-auto">
            {poolView === "list"
              ? poolItems.map((item) => (
                  <PoolRow
                    key={item.item_id}
                    item={item}
                    checked={selectedPoolIds.includes(item.item_id)}
                    disabled={atCapacity}
                    onToggle={() =>
                      setSelectedPoolIds((previous) =>
                        previous.includes(item.item_id)
                          ? previous.filter((id) => id !== item.item_id)
                          : [...previous, item.item_id],
                      )
                    }
                  />
                ))
              : (groups.data?.items ?? []).map((group) => (
                  <SchoolGroupRow
                    key={group.university_id}
                    group={group}
                    expanded={expandedUniversity === group.university_id}
                    onToggle={() =>
                      setExpandedUniversity((current) =>
                        current === group.university_id ? null : group.university_id,
                      )
                    }
                    majors={expandedItems.data?.items ?? []}
                    majorsLoading={
                      expandedUniversity === group.university_id && expandedItems.isPending
                    }
                    selectedIds={selectedPoolIds}
                    disabled={atCapacity}
                    onToggleMajor={(itemId) =>
                      setSelectedPoolIds((previous) =>
                        previous.includes(itemId)
                          ? previous.filter((id) => id !== itemId)
                          : [...previous, itemId],
                      )
                    }
                  />
                ))}
            {poolView === "school" && (groups.data?.items.length ?? 0) === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                {groups.isPending ? "正在读取院校…" : "当前筛选下没有可加入的院校。"}
              </p>
            ) : null}
            {poolItems.length === 0 && poolView === "list" ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                {pool.isPending ? "正在读取候选池…" : "候选池中没有可加入的志愿。请放宽筛选条件。"}
              </p>
            ) : null}
          </CardContent>
          <CardContent className="flex items-center justify-between border-t py-2">
            <span className="text-xs text-muted-foreground">
              {atCapacity ? "方案已满 96 条，加入前请先删除或调高目标数量" : ""}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={poolPage === 0}
                onClick={() => setPoolPage((current) => Math.max(0, current - 1))}
              >
                上一页
              </Button>
              <span className="text-xs tabular-nums text-muted-foreground">
                {poolPage + 1} / {poolLastPage + 1}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={poolPage >= poolLastPage}
                onClick={() => setPoolPage((current) => Math.min(poolLastPage, current + 1))}
              >
                下一页
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="flex max-h-[75vh] flex-col">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">
              我的方案（{items.length}/{scheme.data?.target_count ?? "—"}）
            </CardTitle>
            <CardDescription className="text-xs">
              顺序即填报顺序：越想去越靠前。同一次愿层内更难的志愿建议放前面。
            </CardDescription>
            <div className="space-y-2 pt-2">
              <Input
                placeholder="搜索方案内志愿"
                value={schemeSearch}
                onChange={(event) => setSchemeSearch(event.target.value)}
              />
              <BucketTabs
                value={schemeBucket}
                counts={schemeBucketCounts}
                onChange={setSchemeBucket}
              />
              <div className="flex flex-wrap gap-2">
                <Select
                  className="w-32"
                  value={schemeProvince}
                  onChange={(event) => {
                    setSchemeProvince(event.target.value);
                    // A school from another province would be impossible to match, so clear
                    // it rather than silently showing an empty list.
                    setSchemeUniversity("");
                  }}
                >
                  <option value="">全部省份</option>
                  {schemeProvinceOptions.map(([name, count]) => (
                    <option key={name} value={name}>
                      {name}（{count}）
                    </option>
                  ))}
                </Select>
                <Select
                  className="min-w-36 flex-1"
                  value={schemeUniversity}
                  onChange={(event) => setSchemeUniversity(event.target.value)}
                >
                  <option value="">全部学校</option>
                  {schemeUniversityOptions.map(([name, count]) => (
                    <option key={name} value={name}>
                      {name}（{count}）
                    </option>
                  ))}
                </Select>
              </div>
              <label className="flex items-center gap-1.5 text-xs">
                <Checkbox
                  checked={schemeOnlyUnlocked}
                  onChange={(event) => setSchemeOnlyUnlocked(event.target.checked)}
                />
                只看未锁定
              </label>
              {schemeFiltered ? (
                <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900">
                  筛选生效时不能调整顺序（拖拽与上下移已禁用），
                  请清除筛选后再排序，否则隐藏的志愿会一起位移。
                  <button
                    type="button"
                    className="ml-1 underline"
                    onClick={() => {
                      setSchemeBucket("");
                      setSchemeProvince("");
                      setSchemeUniversity("");
                      setSchemeSearch("");
                      setSchemeOnlyUnlocked(false);
                    }}
                  >
                    清除筛选
                  </button>
                </p>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-auto">
            {items.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                方案为空。请从左侧候选池加入志愿，或点击“重新优化”。
              </p>
            ) : visibleSchemeItems.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                没有符合筛选条件的志愿。
              </p>
            ) : (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                modifiers={[restrictToVerticalAxis, restrictToParentElement]}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={items.map((item) => item.recommendation_item_id)}
                  strategy={verticalListSortingStrategy}
                >
                  <ol className="space-y-2">
                    {visibleSchemeItems.map((item) => (
                      <SchemeRow
                        key={item.recommendation_item_id}
                        item={item}
                        index={items.findIndex(
                          (row) => row.recommendation_item_id === item.recommendation_item_id,
                        )}
                        total={items.length}
                        disabled={reorder.isPending || toggleLock.isPending || removeItem.isPending}
                        reorderDisabled={schemeFiltered}
                        onMoveUp={() => move(item.position, -1)}
                        onMoveDown={() => move(item.position, 1)}
                        onToggleLock={() => toggleLock.mutate({ position: item.position, locked: !item.locked })}
                        onRemove={() => removeItem.mutate(item.position)}
                      />
                    ))}
                  </ol>
                </SortableContext>
              </DndContext>
            )}
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-muted-foreground">{ACS_DISCLAIMER}</p>
    </div>
  );
}

function PoolRow({
  item,
  checked,
  disabled = false,
  onToggle,
}: {
  item: RecommendationItem;
  checked: boolean;
  /** True when the scheme is full: selecting would only fail on submit. */
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-md border p-3 transition-colors ${
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-accent/50"
      }`}
    >
      <Checkbox
        checked={checked}
        onChange={onToggle}
        disabled={disabled}
        className="mt-1"
        data-testid="pool-row-checkbox"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{item.plan_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {item.university_name}
              {item.city_name ? ` · ${item.city_name}` : ""}
            </p>
          </div>
          <RiskBadge bucket={item.risk_bucket} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">ACS {item.acs?.toFixed(1) ?? "—"}</span>
          <span>偏好 {item.preference_score?.toFixed(0) ?? "—"}</span>
          <span className="tabular-nums">计划 {item.plan_count}</span>
          <span className="tabular-nums">{item.tuition_fee} 元</span>
          {item.eligibility !== "PASS" ? <EligibilityBadge eligibility={item.eligibility} /> : null}
        </div>
      </div>
    </label>
  );
}

function SchemeRow({
  item,
  index,
  total,
  disabled,
  reorderDisabled = false,
  onMoveUp,
  onMoveDown,
  onToggleLock,
  onRemove,
}: {
  item: SchemeItem;
  index: number;
  total: number;
  disabled: boolean;
  /** True while a filter hides rows: reordering would move unseen items. */
  reorderDisabled?: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onToggleLock: () => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.recommendation_item_id,
    disabled: reorderDisabled,
  });

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`rounded-md border bg-card p-3 ${isDragging ? "opacity-60 ring-2 ring-primary" : ""}`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          {...attributes}
          {...listeners}
          disabled={reorderDisabled}
          className={`mt-1 select-none rounded px-1 text-muted-foreground ${
            reorderDisabled ? "cursor-not-allowed opacity-30" : "cursor-grab touch-none hover:bg-accent"
          }`}
          aria-label={
            reorderDisabled
              ? "筛选生效时不能拖动排序"
              : `拖动第 ${item.position} 个志愿`
          }
          title={reorderDisabled ? "筛选生效时不能拖动排序" : undefined}
        >
          ⠿
        </button>

        <span className="mt-0.5 w-7 shrink-0 text-center text-sm font-semibold tabular-nums text-muted-foreground">
          {item.position}
        </span>

        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {item.plan_name}
                {item.locked ? <span title="已锁定，重新优化时保留"> 🔒</span> : null}
              </p>
              <p className="truncate text-xs text-muted-foreground">{item.university_name}</p>
            </div>
            <RiskBadge bucket={item.risk_bucket} />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="tabular-nums">ACS {item.acs?.toFixed(1) ?? "—"}</span>
            <span>偏好 {item.preference_score?.toFixed(0) ?? "—"}</span>
            <span className="tabular-nums">计划 {item.plan_count}</span>
            <span className="tabular-nums">{item.tuition_fee} 元</span>
            {item.eligibility !== "PASS" ? <EligibilityBadge eligibility={item.eligibility} /> : null}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex gap-1">
            <Button
              size="icon"
              variant="ghost"
              disabled={disabled || reorderDisabled || index === 0}
              onClick={onMoveUp}
              aria-label="上移"
            >
              ↑
            </Button>
            <Button
              size="icon"
              variant="ghost"
              disabled={disabled || reorderDisabled || index === total - 1}
              onClick={onMoveDown}
              aria-label="下移"
            >
              ↓
            </Button>
            <Button
              size="icon"
              variant={item.locked ? "default" : "ghost"}
              disabled={disabled}
              onClick={onToggleLock}
              aria-label={item.locked ? "解锁" : "锁定"}
            >
              🔒
            </Button>
            <Button
              size="icon"
              variant="ghost"
              disabled={disabled}
              onClick={onRemove}
              aria-label="删除"
              className="text-destructive"
            >
              ✕
            </Button>
          </div>
        </div>
      </div>
    </li>
  );
}

/**
 * One university in the grouped pool: the school line, and — when expanded — its majors.
 *
 * Grouping is done by the server (see `list_university_groups`), because one school's rows
 * span several pages and a client-side group would only describe the current page.
 */
function SchoolGroupRow({
  group,
  expanded,
  onToggle,
  majors,
  majorsLoading,
  selectedIds,
  disabled,
  onToggleMajor,
}: {
  group: UniversityGroup;
  expanded: boolean;
  onToggle: () => void;
  majors: RecommendationItem[];
  majorsLoading: boolean;
  selectedIds: number[];
  disabled: boolean;
  onToggleMajor: (itemId: number) => void;
}) {
  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start gap-3 p-3 text-left transition-colors hover:bg-accent/50"
      >
        <span className="mt-0.5 w-4 shrink-0 text-muted-foreground">{expanded ? "▾" : "▸"}</span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{group.university_name}</p>
              <p className="truncate text-xs text-muted-foreground">
                {group.province_name ?? "—"}
                {group.city_name ? ` · ${group.city_name}` : ""}
              </p>
            </div>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {group.item_count} 个专业
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {group.is_985 ? <Badge variant="secondary">985</Badge> : null}
            {group.is_211 ? <Badge variant="secondary">211</Badge> : null}
            {group.is_double_first_class ? <Badge variant="secondary">双一流</Badge> : null}
            <span className="text-xs tabular-nums text-muted-foreground">
              ACS {group.min_acs?.toFixed(1) ?? "—"}~{group.max_acs?.toFixed(1) ?? "—"}
            </span>
          </div>
        </div>
      </button>

      {expanded ? (
        <div className="space-y-2 border-t bg-muted/30 p-2">
          {majorsLoading ? (
            <p className="py-3 text-center text-xs text-muted-foreground">正在读取该校专业…</p>
          ) : majors.length === 0 ? (
            <p className="py-3 text-center text-xs text-muted-foreground">
              该校在当前筛选下没有可加入的专业。
            </p>
          ) : (
            majors.map((item) => (
              <PoolRow
                key={item.item_id}
                item={item}
                checked={selectedIds.includes(item.item_id)}
                disabled={disabled}
                onToggle={() => onToggleMajor(item.item_id)}
              />
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
