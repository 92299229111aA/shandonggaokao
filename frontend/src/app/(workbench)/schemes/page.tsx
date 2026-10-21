"use client";

/**
 * P04 志愿编辑器｜编排与版本
 *
 * 页面里最重要的不是拖拽，而是两条不能错的规则：
 *   1. 序号是**填报顺序**，不是显示排序。方案内任何筛选生效时禁止改序，
 *      否则会把看不见的项一起挪走。候选池的筛选不影响改序。
 *   2. 顺序操作作用于**完整集合**，所以首尾边界要正确，移动后要整表提交。
 */

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
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Icon } from "@/components/workbench/icon";
import { useToast } from "@/components/workbench/toast";
import type { Scheme, SchemeItem, SchemeVersionSummary } from "@/lib/api/types";
import { catalogApi, recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage, isVersionConflict } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import { formatAcs, formatFee, riskBarClass, riskClass, riskLabel } from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";
import { RISK_LABELS, RISK_ORDER } from "@/lib/utils";

const POOL_PAGE_SIZE = 50;

/**
 * `useSearchParams()` 在静态预渲染时必须处于 Suspense 边界内，
 * 否则 `next build` 会直接失败（CSR bailout）。所以外面包一层。
 */
export default function SchemeEditorPage() {
  return (
    <Suspense fallback={<EditorFallback />}>
      <EditorContent />
    </Suspense>
  );
}

function EditorFallback() {
  return (
    <div className="card" style={{ padding: 24 }}>
      <span className="skeleton title" />
      <span className="skeleton line w80" />
      <span className="skeleton line w60" />
    </div>
  );
}

function EditorContent() {
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { state: flow, loaded } = useFlowState();
  const wb = useWorkbench();

  const [selected, setSelected] = useState<number[]>([]);
  const [localOrder, setLocalOrder] = useState<SchemeItem[] | null>(null);
  const [riskMode, setRiskMode] = useState<"STEADY" | "BALANCED" | "AGGRESSIVE">("BALANCED");
  const [targetCount, setTargetCount] = useState<number | null>(null);
  const [minSafe, setMinSafe] = useState("");
  const [optimizeOpen, setOptimizeOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  // 体检报告「定位到编辑器」通过 ?focus=<recommendation_item_id> 传入稳定 ID
  const searchParams = useSearchParams();
  const focusId = Number(searchParams.get("focus")) || null;
  const [highlightId, setHighlightId] = useState<number | null>(focusId);

  // 候选池筛选（后端）
  const [poolSearch, setPoolSearch] = useState("");
  const [poolDebounced, setPoolDebounced] = useState("");
  const [poolBucket, setPoolBucket] = useState("");
  const [poolProvince, setPoolProvince] = useState("");
  const [poolPage, setPoolPage] = useState(0);

  // 方案内筛选（本地）
  const [schemeSearch, setSchemeSearch] = useState("");
  const [schemeBucket, setSchemeBucket] = useState("");
  const [schemeProvince, setSchemeProvince] = useState("");
  const [schemeUniversity, setSchemeUniversity] = useState("");
  const [schemeOnlyUnlocked, setSchemeOnlyUnlocked] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setPoolDebounced(poolSearch), 350);
    return () => clearTimeout(timer);
  }, [poolSearch]);

  useEffect(() => {
    setPoolPage(0);
  }, [poolDebounced, poolBucket, poolProvince]);

  const scheme = useQuery({
    queryKey: ["editor-scheme", flow.schemeId],
    queryFn: () => schemeApi.get(flow.schemeId!),
    enabled: loaded && Boolean(flow.schemeId),
  });

  useEffect(() => {
    if (scheme.data) {
      setLocalOrder(scheme.data.items);
      setRiskMode(scheme.data.risk_mode as typeof riskMode);
      setTargetCount(scheme.data.target_count);
    }
  }, [scheme.data]);

  // 用 useMemo 固定引用：下面多个 useMemo/useCallback 都依赖它，
  // 直接写表达式会让依赖每次渲染都变化，等于没有缓存。
  const items = useMemo(
    () => localOrder ?? scheme.data?.items ?? [],
    [localOrder, scheme.data],
  );
  const capacity = scheme.data?.target_count ?? wb.applicationLimit ?? 96;
  const atCapacity = items.length >= capacity;

  const pool = useQuery({
    queryKey: ["editor-pool", flow.sessionId, flow.schemeId, scheme.data?.current_version_no,
      poolDebounced, poolBucket, poolProvince, poolPage],
    queryFn: () =>
      recommendationApi.items(flow.sessionId!, {
        limit: POOL_PAGE_SIZE,
        offset: poolPage * POOL_PAGE_SIZE,
        sort: "RISK_MIX",
        exclude_scheme_id: flow.schemeId ?? undefined,
        risk_bucket: poolBucket || undefined,
        search: poolDebounced || undefined,
        province_name: poolProvince || undefined,
      }),
    enabled: loaded && Boolean(flow.sessionId) && Boolean(flow.schemeId),
  });

  const options = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
    enabled: loaded,
  });

  // ---- 方案内筛选 ----
  const visibleScheme = useMemo(() => {
    const needle = schemeSearch.trim();
    return items.filter((item) => {
      if (schemeBucket && item.risk_bucket !== schemeBucket) return false;
      if (schemeProvince && item.province_name !== schemeProvince) return false;
      if (schemeUniversity && item.university_name !== schemeUniversity) return false;
      if (schemeOnlyUnlocked && item.locked) return false;
      if (!needle) return true;
      return item.plan_name.includes(needle) || item.university_name.includes(needle);
    });
  }, [items, schemeBucket, schemeProvince, schemeUniversity, schemeSearch, schemeOnlyUnlocked]);

  const schemeFilterCount =
    (schemeBucket ? 1 : 0) +
    (schemeProvince ? 1 : 0) +
    (schemeUniversity ? 1 : 0) +
    (schemeSearch.trim() ? 1 : 0) +
    (schemeOnlyUnlocked ? 1 : 0);
  const schemeFiltered = schemeFilterCount > 0;

  const bucketCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      const key = item.risk_bucket ?? "";
      if (key) counts[key] = (counts[key] ?? 0) + 1;
    }
    return counts;
  }, [items]);

  const provinceOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      if (!item.province_name) continue;
      counts.set(item.province_name, (counts.get(item.province_name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  const universityOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      if (!item.university_name) continue;
      if (schemeProvince && item.province_name !== schemeProvince) continue;
      counts.set(item.university_name, (counts.get(item.university_name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items, schemeProvince]);

  // ---- 变更 ----
  const onError = useCallback(
    (error: unknown) => {
      if (isVersionConflict(error)) {
        toast.show(`版本冲突：${errorMessage(error)}。已刷新为最新版本，请重试。`, "error");
        queryClient.invalidateQueries({ queryKey: ["editor-scheme"] });
        return;
      }
      toast.show(errorMessage(error), "error");
    },
    [queryClient, toast],
  );

  const syncSuccess = useCallback(
    (updated: Scheme, message?: string) => {
      setLocalOrder(updated.items);
      queryClient.setQueryData(["editor-scheme", updated.id], updated);
      queryClient.invalidateQueries({ queryKey: ["editor-pool"] });
      queryClient.invalidateQueries({ queryKey: ["wb"] });
      if (message) toast.show(message, "ok");
    },
    [queryClient, toast],
  );

  const reorder = useMutation({
    mutationFn: (payload: { order: SchemeItem[]; summary: string }) =>
      schemeApi.updateOrder(flow.schemeId!, {
        version: scheme.data!.lock_version,
        change_summary: payload.summary,
        items: payload.order.map((item, index) => ({
          recommendation_item_id: item.recommendation_item_id,
          position: index + 1,
          locked: item.locked,
        })),
      }),
    onSuccess: (updated) => syncSuccess(updated, "顺序已保存"),
    onError: (error) => {
      setLocalOrder(scheme.data?.items ?? null);
      onError(error);
    },
  });

  const addItems = useMutation({
    mutationFn: (ids: number[]) =>
      schemeApi.addItems(flow.schemeId!, { version: scheme.data!.lock_version, recommendation_item_ids: ids }),
    onSuccess: (updated) => {
      setSelected([]);
      syncSuccess(updated, `已加入 ${updated.item_count} 条`);
    },
    onError,
  });

  const removeItem = useMutation({
    mutationFn: (position: number) =>
      schemeApi.removeItem(flow.schemeId!, position, scheme.data!.lock_version),
    onSuccess: (updated) => syncSuccess(updated, "已移除"),
    onError,
  });

  const toggleLock = useMutation({
    mutationFn: (payload: { position: number; locked: boolean }) =>
      schemeApi.setLock(flow.schemeId!, payload.position, scheme.data!.lock_version, payload.locked),
    onSuccess: (updated) => syncSuccess(updated),
    onError,
  });

  const optimize = useMutation({
    mutationFn: () =>
      schemeApi.optimize(flow.schemeId!, {
        version: scheme.data!.lock_version,
        risk_mode: riskMode,
        target_count: targetCount ?? undefined,
        min_safe_count: minSafe === "" ? null : Number(minSafe),
      }),
    onSuccess: (updated) => {
      syncSuccess(updated);
      setOptimizeOpen(false);
      toast.show(
        `已重新优化：${updated.item_count} 条，锁定项已保留`,
        "ok",
      );
    },
    onError,
  });

  // 筛选生效时不允许改序：拖拽把索引算在完整列表上，
  // 只显示部分行时会挪动用户看不见的项。
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id || !scheme.data || schemeFiltered) return;
      const from = items.findIndex((i) => i.recommendation_item_id === active.id);
      const to = items.findIndex((i) => i.recommendation_item_id === over.id);
      if (from < 0 || to < 0) return;
      const next = arrayMove(items, from, to);
      setLocalOrder(next);
      reorder.mutate({ order: next, summary: `拖拽：第 ${from + 1} 位移到第 ${to + 1} 位` });
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
        summary: `上下移：第 ${position} 位调整到第 ${target + 1} 位`,
      });
    },
    [items, reorder, schemeFiltered],
  );

  // 定位：目标可能被方案内筛选藏起来，先清筛选再滚动过去。
  // 只用稳定 ID，不用序号 —— 序号在方案变动后会漂移。
  useEffect(() => {
    if (!focusId || items.length === 0) return;
    const target = items.find((item) => item.recommendation_item_id === focusId);
    if (!target) {
      setHighlightId(null);
      return;
    }
    setSchemeBucket("");
    setSchemeProvince("");
    setSchemeUniversity("");
    setSchemeSearch("");
    setSchemeOnlyUnlocked(false);
    setHighlightId(focusId);
    const timer = setTimeout(() => {
      document
        .querySelector(`[data-item-id="${focusId}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 120);
    const clear = setTimeout(() => setHighlightId(null), 3600);
    return () => {
      clearTimeout(timer);
      clearTimeout(clear);
    };
  }, [focusId, items]);

  // ---- 缺方案 ----
  if (loaded && !wb.loading && !flow.schemeId) {
    return (
      <>
        <div className="page-head">
          <div>
            <div className="eyebrow">STEP 04 / EDITOR</div>
            <h1>志愿编辑器</h1>
            <p className="page-sub">顺序就是填报顺序，把你更想去的放在前面。</p>
          </div>
        </div>
        <div className="card" style={{ maxWidth: 620 }}>
          <div className="card-body">
            <h2>还没有志愿方案</h2>
            <p className="small muted mt8">
              方案由推荐结果一次性生成（按当前策略生成 {capacity} 条），之后再在这里调整顺序、
              锁定与替换。
            </p>
            <div className="row gap8 mt16">
              <Link className="btn primary" href="/recommendations">
                去推荐结果生成方案
                <Icon name="arrow" />
              </Link>
            </div>
          </div>
        </div>
      </>
    );
  }

  if (scheme.isError) {
    return (
      <div className="card" style={{ padding: 24 }}>
        <div className="row gap8" style={{ color: "var(--red)" }}>
          <Icon name="warn" />
          <strong>无法读取方案</strong>
        </div>
        <p className="small muted mt8">{errorMessage(scheme.error)}</p>
        <button type="button" className="btn mt16" onClick={() => scheme.refetch()}>
          重试
        </button>
      </div>
    );
  }

  const poolItems = pool.data?.items ?? [];
  const poolTotal = pool.data?.total ?? 0;
  const poolLastPage = Math.max(0, Math.ceil(poolTotal / POOL_PAGE_SIZE) - 1);
  const riskTotal = RISK_ORDER.reduce((sum, key) => sum + (bucketCounts[key] ?? 0), 0);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 04 / EDITOR</div>
          <h1>志愿编辑器</h1>
          <p className="page-sub">顺序就是填报顺序，把你更想去的放在前面。</p>
        </div>
        <div className="row gap8">
          <button type="button" className="btn" onClick={() => setHistoryOpen(true)}>
            <Icon name="clock" /> 历史版本
          </button>
          <button type="button" className="btn primary" onClick={() => router.push("/health-check")}>
            进入志愿体检
            <Icon name="arrow" />
          </button>
        </div>
      </div>

      {/* ---- 方案概况 ---- */}
      <div className="card" style={{ padding: "16px 20px" }}>
        <div className="row" style={{ gap: 18 }}>
          <div>
            <small className="muted">当前方案</small>
            <div className="row gap8" style={{ marginTop: 2 }}>
              <b style={{ fontSize: 15 }}>
                {scheme.data?.title || "我的志愿方案"}
              </b>
              <span className="pill">v{scheme.data?.current_version_no ?? "—"}</span>
            </div>
          </div>
          <div className="summary-divider" />
          <div className="mini-stat">
            <b className="num">
              {items.length} <span>/ {capacity}</span>
            </b>
            <small>已编排志愿</small>
          </div>

          <div style={{ flex: 1, minWidth: 220 }}>
            <div className="balance" role="img" aria-label="五档构成">
              {RISK_ORDER.map((key) => {
                const count = bucketCounts[key] ?? 0;
                return count > 0 ? (
                  <span key={key} className={riskBarClass(key)} style={{ flexGrow: count }} />
                ) : null;
              })}
            </div>
            <div className="balance-legend">
              {RISK_ORDER.map((key) => (
                <span key={key}>
                  <i className={`legend-dot ${riskBarClass(key)}`} />
                  {RISK_LABELS[key]} <b className="num">{bucketCounts[key] ?? 0}</b>
                </span>
              ))}
            </div>
          </div>

          <button
            type="button"
            className="btn"
            onClick={() => setOptimizeOpen((open) => !open)}
            aria-expanded={optimizeOpen}
          >
            <Icon name="settings" /> 优化条件
          </button>
        </div>

        {optimizeOpen ? (
          <div className="scheme-filters" style={{ borderBottom: 0, marginTop: 14, paddingBottom: 0 }}>
            <div className="row" style={{ gap: 12 }}>
              <div className="field" style={{ width: 150 }}>
                <label htmlFor="opt-mode">风险模式</label>
                <select
                  id="opt-mode"
                  className="select"
                  value={riskMode}
                  onChange={(event) => setRiskMode(event.target.value as typeof riskMode)}
                >
                  <option value="STEADY">稳妥</option>
                  <option value="BALANCED">均衡</option>
                  <option value="AGGRESSIVE">激进</option>
                </select>
              </div>
              <div className="field" style={{ width: 120 }}>
                <label htmlFor="opt-target">目标数量</label>
                <input
                  id="opt-target"
                  className="input num"
                  value={targetCount ?? ""}
                  inputMode="numeric"
                  onChange={(event) =>
                    setTargetCount(event.target.value === "" ? null : Number(event.target.value))
                  }
                />
              </div>
              <div className="field" style={{ width: 140 }}>
                <label htmlFor="opt-min">最低安全数量</label>
                <input
                  id="opt-min"
                  className="input num"
                  value={minSafe}
                  placeholder="可留空"
                  inputMode="numeric"
                  onChange={(event) => setMinSafe(event.target.value)}
                />
              </div>
              <button
                type="button"
                className="btn primary"
                style={{ alignSelf: "flex-end" }}
                disabled={optimize.isPending}
                onClick={() => optimize.mutate()}
              >
                {optimize.isPending ? "优化中…" : "重新优化"}
              </button>
            </div>
            <p className="legal-mini mt8">
              锁定的志愿在任何情况下都会被保留；资格待确认的志愿不计入保底数量。
              目标数量不能突破当年上限 {wb.applicationLimit ?? capacity}。
            </p>
          </div>
        ) : null}
      </div>

      <div className="editor-grid" style={{ marginTop: 18 }}>
        {/* ---- 左：候选池 ---- */}
        <section className="card">
          <div className="card-head">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>
                候选池 <span className="num muted">({poolTotal.toLocaleString("zh-CN")})</span>
              </h2>
              <button
                type="button"
                className="btn primary"
                disabled={selected.length === 0 || addItems.isPending || atCapacity}
                title={atCapacity ? `方案已满 ${capacity} 条` : undefined}
                onClick={() => addItems.mutate(selected)}
              >
                加入({selected.length})
              </button>
            </div>
            <p className="small muted mt8">
              资格不符的不会出现在这里；已加入方案的项目也不再显示。
            </p>
          </div>

          <div className="scheme-filters">
            <div className="chips">
              <button
                type="button"
                className="filter-chip"
                aria-pressed={poolBucket === ""}
                onClick={() => setPoolBucket("")}
              >
                全部
              </button>
              {RISK_ORDER.map((key) => (
                <button
                  key={key}
                  type="button"
                  className="filter-chip"
                  aria-pressed={poolBucket === key}
                  onClick={() => setPoolBucket(poolBucket === key ? "" : key)}
                >
                  {RISK_LABELS[key]}
                </button>
              ))}
            </div>
            <div className="row gap8 mt8">
              <div className="searchbox" style={{ flex: 1 }}>
                <Icon name="search" />
                <input
                  className="input"
                  placeholder="搜索学校或专业"
                  value={poolSearch}
                  onChange={(event) => setPoolSearch(event.target.value)}
                />
              </div>
              <select
                className="select filter-select"
                value={poolProvince}
                onChange={(event) => setPoolProvince(event.target.value)}
                aria-label="候选池地区"
              >
                <option value="">全部地区</option>
                {(options.data?.provinces ?? []).map((p) => (
                  <option key={p.code} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="small muted mt8">
              {pool.isPending
                ? "正在读取候选池…"
                : poolTotal === 0
                  ? "当前筛选下没有可加入的志愿"
                  : `可加入 ${poolTotal.toLocaleString("zh-CN")} 条`}
            </div>
          </div>

          {pool.isPending ? (
            <div style={{ padding: 16 }}>
              <span className="skeleton title" />
              <span className="skeleton line w80" />
              <span className="skeleton line w60" />
            </div>
          ) : poolItems.length === 0 ? (
            <div className="empty">
              <Icon name="search" />
              <p className="empty-title">没有可加入的志愿</p>
              <p className="small muted">放宽筛选条件试试，或者检查方案是否已满。</p>
            </div>
          ) : (
            poolItems.map((item) => (
              <label className="pool-row row" key={item.item_id} style={{ gap: 10 }}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.item_id)}
                  disabled={atCapacity}
                  onChange={() =>
                    setSelected((prev) =>
                      prev.includes(item.item_id)
                        ? prev.filter((id) => id !== item.item_id)
                        : [...prev, item.item_id],
                    )
                  }
                  style={{ accentColor: "var(--blue)", marginTop: 3 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="school-name">
                    {item.university_name}
                    <span className="muted small"> · {item.province_name ?? "—"}</span>
                  </div>
                  <div className="major-name">{item.plan_name}</div>
                  <div className="small muted" style={{ marginTop: 2 }}>
                    计划 {item.plan_count > 0 ? item.plan_count : "暂无数据"} · {formatFee(item.tuition_fee)} ·
                    ACS {formatAcs(item.acs)}
                  </div>
                </div>
                <span className={`risk ${riskClass(item.risk_bucket)}`}>
                  {riskLabel(item.risk_bucket)}
                </span>
              </label>
            ))
          )}

          <div className="pager">
            <span className="small muted">
              {poolTotal === 0 ? "共 0 条" : `共 ${poolTotal.toLocaleString("zh-CN")} 条`}
            </span>
            <div className="row gap8">
              <button
                type="button"
                className="btn icon"
                aria-label="上一页"
                disabled={poolPage === 0}
                onClick={() => setPoolPage((p) => Math.max(0, p - 1))}
              >
                <Icon name="up" style={{ transform: "rotate(-90deg)" }} />
              </button>
              <span className="num">
                {poolPage + 1} / {poolLastPage + 1}
              </span>
              <button
                type="button"
                className="btn icon"
                aria-label="下一页"
                disabled={poolPage >= poolLastPage}
                onClick={() => setPoolPage((p) => Math.min(poolLastPage, p + 1))}
              >
                <Icon name="down" style={{ transform: "rotate(-90deg)" }} />
              </button>
            </div>
          </div>
        </section>

        {/* ---- 右：我的方案 ---- */}
        <section className="card">
          <div className="card-head">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>
                我的方案{" "}
                <span className="num muted">
                  ({items.length} / {capacity})
                </span>
              </h2>
              <span className={`pill ${atCapacity ? "warn" : "good"}`}>
                {atCapacity ? "已满" : "本地已保存"}
              </span>
            </div>
            <p className="small muted mt8">
              序号就是填报顺序。解锁后可移除；重新优化会保留锁定项。
            </p>
          </div>

          <div className="scheme-filters">
            <div className="chips">
              <button
                type="button"
                className="filter-chip"
                aria-pressed={schemeBucket === ""}
                onClick={() => setSchemeBucket("")}
              >
                全部 <span>{items.length}</span>
              </button>
              {RISK_ORDER.map((key) => (
                <button
                  key={key}
                  type="button"
                  className="filter-chip"
                  aria-pressed={schemeBucket === key}
                  onClick={() => setSchemeBucket(schemeBucket === key ? "" : key)}
                >
                  {RISK_LABELS[key]} <span>{bucketCounts[key] ?? 0}</span>
                </button>
              ))}
            </div>

            <div className="row gap8 mt8">
              <div className="searchbox" style={{ flex: 1 }}>
                <Icon name="search" />
                <input
                  className="input"
                  placeholder="搜索方案内志愿"
                  value={schemeSearch}
                  onChange={(event) => setSchemeSearch(event.target.value)}
                />
              </div>
              <select
                className="select filter-select"
                value={schemeProvince}
                aria-label="方案内省份"
                onChange={(event) => {
                  setSchemeProvince(event.target.value);
                  setSchemeUniversity("");
                }}
              >
                <option value="">全部省份</option>
                {provinceOptions.map(([name, count]) => (
                  <option key={name} value={name}>
                    {name}（{count}）
                  </option>
                ))}
              </select>
              <select
                className="select filter-select"
                value={schemeUniversity}
                aria-label="方案内学校"
                onChange={(event) => setSchemeUniversity(event.target.value)}
              >
                <option value="">全部学校</option>
                {universityOptions.map(([name, count]) => (
                  <option key={name} value={name}>
                    {name}（{count}）
                  </option>
                ))}
              </select>
            </div>

            <label className="check mt8" style={{ display: "inline-flex", gap: 6, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={schemeOnlyUnlocked}
                onChange={(event) => setSchemeOnlyUnlocked(event.target.checked)}
                style={{ accentColor: "var(--blue)" }}
              />
              只看未锁定
            </label>

            {schemeFiltered ? (
              <div className="stale-banner mt8">
                <Icon name="warn" />
                <span>
                  筛选生效时不能调整顺序（拖拽与上下移已禁用），否则隐藏的志愿会一起位移。
                  <button
                    type="button"
                    className="text-link"
                    style={{ marginLeft: 6 }}
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
                </span>
              </div>
            ) : null}

            <div className="small muted mt8">
              显示 {visibleScheme.length} / {items.length} 条（真实序号不变）
            </div>
          </div>

          {items.length === 0 ? (
            <div className="empty">
              <Icon name="file" />
              <p className="empty-title">方案还是空的</p>
              <p className="small muted">从左侧候选池加入，或者去推荐结果重新生成方案。</p>
            </div>
          ) : visibleScheme.length === 0 ? (
            <div className="empty">
              <Icon name="search" />
              <p className="empty-title">没有符合筛选的志愿</p>
              <p className="small muted">清除筛选后可以继续调整顺序。</p>
            </div>
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              modifiers={[restrictToVerticalAxis, restrictToParentElement]}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={items.map((i) => i.recommendation_item_id)}
                strategy={verticalListSortingStrategy}
              >
                <div>
                  {visibleScheme.map((item, index) => (
                    <SchemeRow
                      key={item.recommendation_item_id}
                      item={item}
                      isFirst={items.findIndex((i) => i.position === item.position) === 0}
                      isLast={
                        items.findIndex((i) => i.position === item.position) === items.length - 1
                      }
                      reorderDisabled={schemeFiltered}
                      busy={reorder.isPending || removeItem.isPending || toggleLock.isPending}
                      onMoveUp={() => move(item.position, -1)}
                      onMoveDown={() => move(item.position, 1)}
                      onToggleLock={() =>
                        toggleLock.mutate({ position: item.position, locked: !item.locked })
                      }
                      onRemove={() => removeItem.mutate(item.position)}
                      index={index}
                      highlighted={highlightId === item.recommendation_item_id}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          )}

          <div className="card-footer">
            <span>
              五档构成 {riskTotal} 条 · 目标 {capacity}
            </span>
            <span className="num">
              {scheme.data?.item_count === items.length
                ? `v${scheme.data?.current_version_no}`
                : "保存中…"}
            </span>
          </div>
        </section>
      </div>

      <div className="help-note">
        <Icon name="info" />
        <span>
          方案内任何筛选（风险、省份、学校、搜索、只看未锁定）生效时都不能调序，
          因为拖动会连带移动当前看不见的志愿。候选池的筛选不影响方案排序。
        </span>
      </div>

      {historyOpen ? (
        <HistoryDialog schemeId={flow.schemeId!} onClose={() => setHistoryOpen(false)} />
      ) : null}
    </>
  );
}

function SchemeRow({
  item,
  isFirst,
  isLast,
  reorderDisabled,
  busy,
  onMoveUp,
  onMoveDown,
  onToggleLock,
  onRemove,
  highlighted,
}: {
  item: SchemeItem;
  isFirst: boolean;
  isLast: boolean;
  reorderDisabled: boolean;
  busy: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onToggleLock: () => void;
  onRemove: () => void;
  index: number;
  highlighted?: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.recommendation_item_id,
    disabled: reorderDisabled,
  });

  return (
    <div
      ref={setNodeRef}
      data-item-id={item.recommendation_item_id}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`scheme-row${isDragging || highlighted ? " highlight" : ""}`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        disabled={reorderDisabled}
        className="grip"
        style={{
          background: "none",
          border: 0,
          cursor: reorderDisabled ? "not-allowed" : "grab",
          opacity: reorderDisabled ? 0.3 : 1,
        }}
        aria-label={
          reorderDisabled ? "筛选生效时不能拖动排序" : `拖动第 ${item.position} 个志愿`
        }
        title={reorderDisabled ? "筛选生效时不能拖动排序" : "拖动调整顺序"}
      >
        ⠿
      </button>

      <span className="index num">{item.position}</span>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="school-name">{item.university_name}</div>
        <div className="major-name">{item.plan_name}</div>
      </div>

      <div className="scheme-score">
        <b className="num" style={{ color: `var(--${riskVar(item.risk_bucket)})` }}>
          {formatAcs(item.acs)}
        </b>
        <small>ACS</small>
      </div>

      <span className={`risk ${riskClass(item.risk_bucket)}`}>{riskLabel(item.risk_bucket)}</span>

      <div className="scheme-acts">
        <button
          type="button"
          className="btn icon"
          aria-label="上移"
          disabled={reorderDisabled || isFirst || busy}
          onClick={onMoveUp}
        >
          <Icon name="up" />
        </button>
        <button
          type="button"
          className="btn icon"
          aria-label="下移"
          disabled={reorderDisabled || isLast || busy}
          onClick={onMoveDown}
        >
          <Icon name="down" />
        </button>
        <button
          type="button"
          className={`btn icon${item.locked ? " locked" : ""}`}
          aria-label={item.locked ? "解锁" : "锁定（重新优化时保留）"}
          disabled={busy}
          onClick={onToggleLock}
        >
          <Icon name={item.locked ? "lock" : "unlock"} />
        </button>
        <button
          type="button"
          className="btn icon"
          aria-label="移除"
          disabled={busy}
          onClick={onRemove}
        >
          <Icon name="close" />
        </button>
      </div>
    </div>
  );
}

function riskVar(bucket: string | null | undefined): string {
  const index = bucket ? RISK_ORDER.indexOf(bucket as (typeof RISK_ORDER)[number]) : -1;
  return ["red", "orange", "gold", "teal", "green"][index] ?? "muted";
}

function HistoryDialog({ schemeId, onClose }: { schemeId: number; onClose: () => void }) {
  const versions = useQuery({
    queryKey: ["scheme-versions", schemeId],
    queryFn: () => schemeApi.versions(schemeId),
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className="drawer"
        role="dialog"
        aria-label="历史版本"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-head">
          <div>
            <h2>历史版本</h2>
            <p className="small muted">每次修改都会生成新版本；当前版本在编辑器里编辑。</p>
          </div>
          <button type="button" className="btn icon" aria-label="关闭" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="drawer-body">
          {versions.isPending ? (
            <span className="skeleton line w80" />
          ) : (versions.data ?? []).length === 0 ? (
            <p className="small muted">还没有版本记录。</p>
          ) : (
            (versions.data ?? []).map((version: SchemeVersionSummary) => (
              <div className="kv-line" key={version.version_no}>
                <span>
                  v{version.version_no} · {version.operation}
                  {version.change_summary ? ` · ${version.change_summary}` : ""}
                </span>
                <span className="num">{version.item_count} 条</span>
              </div>
            ))
          )}
          <p className="legal-mini mt16">
            目前只提供查看，不支持回滚到历史版本 —— 后端还没有对应的恢复接口。
            需要回退时可以在编辑器里手动调整。
          </p>
        </div>
      </aside>
    </div>
  );
}
