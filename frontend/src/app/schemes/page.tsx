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
import { recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage, isVersionConflict } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type { RecommendationItem, RiskMode, Scheme, SchemeItem } from "@/lib/api/types";
import { ACS_DISCLAIMER } from "@/lib/utils";

export default function SchemeEditorPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { state, loaded } = useFlowState();

  const [conflict, setConflict] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [poolFilter, setPoolFilter] = useState("");
  const [schemeFilter, setSchemeFilter] = useState("");
  const [selectedPoolIds, setSelectedPoolIds] = useState<number[]>([]);
  const [riskMode, setRiskMode] = useState<RiskMode>("BALANCED");
  const [targetCount, setTargetCount] = useState<number | null>(null);
  const [minSafeCount, setMinSafeCount] = useState<string>("");
  const [localOrder, setLocalOrder] = useState<SchemeItem[] | null>(null);

  const scheme = useQuery({
    queryKey: ["scheme", state.schemeId],
    queryFn: () => schemeApi.get(state.schemeId!),
    enabled: loaded && Boolean(state.schemeId),
  });

  const pool = useQuery({
    queryKey: ["recommendation-items", state.sessionId, "editor"],
    queryFn: () => recommendationApi.items(state.sessionId!, { limit: 500 }),
    enabled: loaded && Boolean(state.sessionId),
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
      const oldIndex = items.findIndex((item) => item.recommendation_item_id === active.id);
      const newIndex = items.findIndex((item) => item.recommendation_item_id === over.id);
      if (oldIndex < 0 || newIndex < 0) return;

      const next = arrayMove(items, oldIndex, newIndex);
      setLocalOrder(next);
      reorder.mutate({ order: next, summary: `拖拽调整：第 ${oldIndex + 1} 位移到第 ${newIndex + 1} 位` });
    },
    [items, reorder, scheme.data],
  );

  const move = useCallback(
    (position: number, direction: -1 | 1) => {
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
    [items, reorder],
  );

  const poolItems = useMemo(() => {
    const inScheme = new Set(items.map((item) => item.recommendation_item_id));
    return (pool.data?.items ?? []).filter((item) => {
      if (inScheme.has(item.item_id)) return false;
      if (!poolFilter) return true;
      const needle = poolFilter.trim();
      return item.plan_name.includes(needle) || item.university_name.includes(needle);
    });
  }, [items, pool.data?.items, poolFilter]);

  const visibleSchemeItems = useMemo(() => {
    if (!schemeFilter) return items;
    const needle = schemeFilter.trim();
    return items.filter(
      (item) => item.plan_name.includes(needle) || item.university_name.includes(needle),
    );
  }, [items, schemeFilter]);

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
              资格不符的志愿不会出现在这里。勾选后可批量加入方案。
            </CardDescription>
            <div className="flex gap-2 pt-2">
              <Input
                placeholder="筛选学校或专业"
                value={poolFilter}
                onChange={(event) => setPoolFilter(event.target.value)}
              />
              <Button
                size="sm"
                disabled={!selectedPoolIds.length || addItems.isPending}
                onClick={() => addItems.mutate(selectedPoolIds)}
              >
                加入({selectedPoolIds.length})
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex-1 space-y-2 overflow-auto">
            {poolItems.map((item) => (
              <PoolRow
                key={item.item_id}
                item={item}
                checked={selectedPoolIds.includes(item.item_id)}
                onToggle={() =>
                  setSelectedPoolIds((previous) =>
                    previous.includes(item.item_id)
                      ? previous.filter((id) => id !== item.item_id)
                      : [...previous, item.item_id],
                  )
                }
              />
            ))}
            {poolItems.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                候选池中没有可加入的志愿。
              </p>
            ) : null}
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
            <div className="pt-2">
              <Input
                placeholder="筛选方案内志愿"
                value={schemeFilter}
                onChange={(event) => setSchemeFilter(event.target.value)}
              />
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-auto">
            {items.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                方案为空。请从左侧候选池加入志愿，或点击“重新优化”。
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
  onToggle,
}: {
  item: RecommendationItem;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors hover:bg-accent/50">
      <Checkbox checked={checked} onChange={onToggle} className="mt-1" />
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
  onMoveUp,
  onMoveDown,
  onToggleLock,
  onRemove,
}: {
  item: SchemeItem;
  index: number;
  total: number;
  disabled: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onToggleLock: () => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.recommendation_item_id,
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
          className="mt-1 cursor-grab touch-none select-none rounded px-1 text-muted-foreground hover:bg-accent"
          aria-label={`拖动第 ${item.position} 个志愿`}
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
            <Button size="icon" variant="ghost" disabled={disabled || index === 0} onClick={onMoveUp} aria-label="上移">
              ↑
            </Button>
            <Button
              size="icon"
              variant="ghost"
              disabled={disabled || index === total - 1}
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
