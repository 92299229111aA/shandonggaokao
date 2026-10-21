"use client";

/**
 * P03 推荐结果｜探索与比较
 *
 * 三条硬规则：
 *   1. 搜索与筛选都作用在**全候选池**上（后端 query），不是只筛当前页。
 *   2. 加入只认稳定的志愿 ID；同校不同专业不会互相去重。
 *   3. 满额就说满额，把原因和下一步讲清楚，不静默丢弃。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Icon } from "@/components/workbench/icon";
import { useToast } from "@/components/workbench/toast";
import type { RecommendationItem } from "@/lib/api/types";
import { catalogApi, recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import {
  ELIGIBILITY_TEXT,
  eligibilityClass,
  formatAcs,
  formatFee,
  riskClass,
  riskLabel,
  riskModeLabel,
  subjectList,
} from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";
import { RISK_LABELS, RISK_ORDER } from "@/lib/utils";

const PAGE_SIZE = 50;

const SORT_OPTIONS = [
  { value: "PREFERENCE", label: "偏好优先" },
  { value: "ACS_DESC", label: "ACS 从高到低" },
  { value: "ACS_ASC", label: "ACS 从低到高" },
  { value: "PLAN_COUNT_DESC", label: "招生计划从多到少" },
] as const;

export default function RecommendationsPage() {
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const wb = useWorkbench();
  const { state: flow, update, loaded } = useFlowState();

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [bucket, setBucket] = useState("");
  const [province, setProvince] = useState("");
  const [trait985, setTrait985] = useState(false);
  const [trait211, setTrait211] = useState(false);
  const [traitDfc, setTraitDfc] = useState(false);
  const [sort, setSort] = useState<string>("PREFERENCE");
  const [page, setPage] = useState(0);
  const [detail, setDetail] = useState<RecommendationItem | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(timer);
  }, [search]);

  // 任一筛选变化都回到第一页，否则会停在一张空页上
  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, bucket, province, trait985, trait211, traitDfc, sort]);

  const options = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
    enabled: loaded,
  });

  const sessionId = flow.sessionId;

  const items = useQuery({
    queryKey: [
      "reco-items",
      sessionId,
      page,
      sort,
      debouncedSearch,
      bucket,
      province,
      trait985,
      trait211,
      traitDfc,
    ],
    queryFn: () =>
      recommendationApi.items(sessionId!, {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort,
        search: debouncedSearch || undefined,
        risk_bucket: bucket || undefined,
        province_name: province || undefined,
        is_985: trait985 || undefined,
        is_211: trait211 || undefined,
        is_double_first_class: traitDfc || undefined,
      }),
    enabled: loaded && Boolean(sessionId),
  });

  const generate = useMutation({
    mutationFn: async () => {
      if (!flow.profileId || !flow.preferenceId) throw new Error("缺少档案或偏好");
      return recommendationApi.run({
        profile_id: flow.profileId,
        preference_id: flow.preferenceId,
      });
    },
    onSuccess: (run) => {
      update({ sessionId: run.session.id });
      toast.show(`已生成推荐，候选 ${run.pool_size.toLocaleString()} 条`, "ok");
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  // 已加入集合：用稳定 ID 判断，不用名称
  const inScheme = useMemo(() => {
    const set = new Set<number>();
    for (const item of wb.scheme?.items ?? []) set.add(item.recommendation_item_id);
    return set;
  }, [wb.scheme]);

  const capacity = wb.applicationLimit ?? 96;
  const used = wb.scheme?.item_count ?? 0;
  const full = !wb.scheme || used >= capacity;

  const addOne = useMutation({
    mutationFn: async (item: RecommendationItem) => {
      // 没有方案时不能「顺手建一个再加入」：建方案会跑优化器直接生成满额方案，
      // 接着再加入必然因为超容量失败。这里明确拒绝，让用户走「生成方案」。
      if (!flow.schemeId || !wb.scheme) {
        throw new Error("还没有方案，请先点右上角「生成 96 条方案」");
      }
      return schemeApi.addItems(flow.schemeId, {
        version: wb.scheme.lock_version,
        recommendation_item_ids: [item.item_id],
      });
    },
    onSuccess: async (scheme, item) => {
      update({ schemeId: scheme.id });
      await queryClient.invalidateQueries({ queryKey: ["wb"] });
      await queryClient.invalidateQueries({ queryKey: ["reco-items"] });
      toast.show(`已加入：${item.university_name} · ${item.plan_name}`, "ok");
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  const openEditor = useMutation({
    mutationFn: async () => {
      if (flow.schemeId) return wb.scheme!;
      const created = await schemeApi.create({ recommendation_session_id: sessionId! });
      update({ schemeId: created.id });
      return created;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["wb"] });
      router.push("/schemes");
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  const total = items.data?.total ?? 0;
  const shown = items.data?.items.length ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = page * PAGE_SIZE + shown;
  const counts = items.data?.bucket_counts ?? {};

  const activeFilters: Array<{ label: string; clear: () => void }> = [];
  if (bucket) activeFilters.push({ label: RISK_LABELS[bucket] ?? bucket, clear: () => setBucket("") });
  if (province) activeFilters.push({ label: province, clear: () => setProvince("") });
  if (trait985) activeFilters.push({ label: "985", clear: () => setTrait985(false) });
  if (trait211) activeFilters.push({ label: "211", clear: () => setTrait211(false) });
  if (traitDfc) activeFilters.push({ label: "双一流", clear: () => setTraitDfc(false) });
  if (debouncedSearch)
    activeFilters.push({ label: `搜索「${debouncedSearch}」`, clear: () => setSearch("") });

  // ---- 未生成：先讲清依据，再给入口 ----
  if (loaded && !wb.loading && !sessionId) {
    return (
      <>
        <div className="page-head">
          <div>
            <div className="eyebrow">STEP 03 / RECOMMENDATIONS</div>
            <h1>推荐结果</h1>
            <p className="page-sub">从院校与专业出发，找到值得加入方案的选择。</p>
          </div>
        </div>

        <div className="card" style={{ maxWidth: 720 }}>
          <div className="card-body">
            <h2>还没有生成推荐</h2>
            <p className="small muted mt8">
              推荐会基于你已保存的档案与偏好，在全部候选专业中做资格过滤与评分。
              生成过程不改动你已有的方案。
            </p>

            <div className="kv mt16">
              <div className="kv-line">
                <span>档案版本</span>
                <span className="num">
                  {wb.profile ? `v${wb.profile.version} · 位次 ${wb.profile.rank?.toLocaleString() ?? "—"}` : "缺少档案"}
                </span>
              </div>
              <div className="kv-line">
                <span>选考科目</span>
                <span>{subjectList(wb.profile?.subjects)}</span>
              </div>
              <div className="kv-line">
                <span>偏好版本</span>
                <span className="num">{wb.preference ? `v${wb.preference.version}` : "缺少偏好"}</span>
              </div>
              <div className="kv-line">
                <span>风险策略</span>
                <span>{riskModeLabel(wb.preference?.risk_mode)}</span>
              </div>
              <div className="kv-line">
                <span>数据年份</span>
                <span>{wb.cycleYear ? `${wb.cycleYear} 年` : "—"}</span>
              </div>
            </div>

            <div className="row gap8 mt16">
              <button
                type="button"
                className="btn primary"
                disabled={!wb.profile || !wb.preference || generate.isPending}
                onClick={() => generate.mutate()}
              >
                {generate.isPending ? "生成中…" : "生成推荐"}
                <Icon name="arrow" />
              </button>
              {!wb.profile ? (
                <Link className="btn" href="/onboarding/score">
                  去填写成绩与选科
                </Link>
              ) : !wb.preference ? (
                <Link className="btn" href="/onboarding/preferences">
                  去设置偏好画像
                </Link>
              ) : null}
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 03 / RECOMMENDATIONS</div>
          <h1>推荐结果</h1>
          <p className="page-sub">从院校与专业出发，找到值得加入方案的选择。</p>
        </div>
        <div className="row gap8">
          <button
            type="button"
            className="btn"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
          >
            <Icon name="refresh" />
            {generate.isPending ? "重新生成中…" : "重新生成"}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => openEditor.mutate()}
            disabled={openEditor.isPending}
          >
            {openEditor.isPending
              ? "正在生成方案…"
              : wb.scheme
                ? "进入志愿编辑器"
                : "生成 96 条方案"}
            <Icon name="arrow" />
          </button>
        </div>
      </div>

      {/* ---- 依据摘要 ---- */}
      <div className="card reco-summary">
        <div className="summary-profile">
          <small className="muted">当前档案</small>
          <div className="row gap8">
            <b className="num">
              位次 {wb.profile?.rank?.toLocaleString("zh-CN") ?? "—"}
            </b>
            <span className="small muted">{subjectList(wb.profile?.subjects)}</span>
          </div>
        </div>
        <div className="summary-divider" />
        <div className="mini-stat">
          <b className="num">{total.toLocaleString("zh-CN")}</b>
          <small>条符合条件的候选</small>
        </div>
        <div className="mini-stat">
          <b>{riskModeLabel(wb.preference?.risk_mode)}</b>
          <small>当前偏好策略</small>
        </div>
        <div className="mini-stat push">
          <b className="num">
            {used} <span>/ {capacity}</span>
          </b>
          <small>已加入方案</small>
        </div>
      </div>

      {!wb.scheme ? (
        <div className="stale-banner" style={{ marginTop: 16 }}>
          <Icon name="info" />
          <span>
            <strong>还没有志愿方案。</strong>
            「生成方案」会按当前策略一次性生成 {capacity} 条并进入编辑器；
            逐条「加入方案」要等方案生成且有空位后才能用（方案满额时先在编辑器里删除）。
          </span>
        </div>
      ) : null}

      {wb.recommendationStale ? (
        <div className="stale-banner" style={{ marginTop: 16 }}>
          <Icon name="warn" />
          <span>
            档案或偏好改过了，<strong>这批推荐是旧版本生成的</strong>。
            可以继续浏览，但结论未必对应当前输入 —— 建议重新生成。
          </span>
        </div>
      ) : null}

      <section className="card" style={{ marginTop: 16 }}>
        <div className="filter-wrap">
          <div className="search-row">
            <div className="searchbox">
              <Icon name="search" />
              <input
                className="input"
                aria-label="搜索院校或专业（全候选池）"
                placeholder="搜索院校或专业（全候选池，不只是当前页）"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <select
              className="select filter-select"
              aria-label="院校所在地"
              value={province}
              onChange={(event) => setProvince(event.target.value)}
            >
              <option value="">全部地区</option>
              {(options.data?.provinces ?? []).map((p) => (
                <option key={p.code} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              className="select filter-select"
              aria-label="结果排序"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="choice"
              aria-pressed={trait985}
              onClick={() => setTrait985(!trait985)}
            >
              985
            </button>
            <button
              type="button"
              className="choice"
              aria-pressed={trait211}
              onClick={() => setTrait211(!trait211)}
            >
              211
            </button>
            <button
              type="button"
              className="choice"
              aria-pressed={traitDfc}
              onClick={() => setTraitDfc(!traitDfc)}
            >
              双一流
            </button>
          </div>

          <div className="risk-filters">
            <button
              type="button"
              className="filter-chip"
              aria-pressed={bucket === ""}
              onClick={() => setBucket("")}
            >
              全部 <span>{total.toLocaleString("zh-CN")}</span>
            </button>
            {RISK_ORDER.map((key) => (
              <button
                key={key}
                type="button"
                className="filter-chip"
                aria-pressed={bucket === key}
                onClick={() => setBucket(bucket === key ? "" : key)}
              >
                {RISK_LABELS[key]} <span>{(counts[key] ?? 0).toLocaleString("zh-CN")}</span>
              </button>
            ))}
          </div>

          {activeFilters.length > 0 ? (
            <div className="chips" style={{ marginTop: 12 }}>
              {activeFilters.map((filter) => (
                <span className="active-filter" key={filter.label}>
                  {filter.label}
                  <button type="button" aria-label={`移除 ${filter.label}`} onClick={filter.clear}>
                    ×
                  </button>
                </span>
              ))}
              <button
                type="button"
                className="text-link"
                onClick={() => {
                  setBucket("");
                  setProvince("");
                  setTrait985(false);
                  setTrait211(false);
                  setTraitDfc(false);
                  setSearch("");
                }}
              >
                清除全部
              </button>
            </div>
          ) : null}
        </div>

        <div className="list-header">
          <span>院校与专业</span>
          <span>风险 / 安全指数</span>
          <span>计划与费用</span>
          <span>方案操作</span>
        </div>

        {items.isPending ? (
          <div style={{ padding: 20 }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} style={{ marginBottom: 18 }}>
                <span className="skeleton title" />
                <span className="skeleton line w60" />
              </div>
            ))}
          </div>
        ) : items.isError ? (
          <div style={{ padding: 24 }}>
            <div className="alert alert-bad" style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <Icon name="warn" />
              <span>读取推荐结果失败：{errorMessage(items.error)}</span>
            </div>
            <button type="button" className="btn mt16" onClick={() => items.refetch()}>
              重试
            </button>
          </div>
        ) : shown === 0 ? (
          <div className="empty">
            <Icon name="search" />
            <p className="empty-title">没有符合条件的志愿</p>
            <p className="small muted">
              {activeFilters.length > 0
                ? "当前筛选太窄。可以移除几个条件，或回到偏好画像放宽硬限制。"
                : "当前候选池为空。请确认档案与偏好是否完整，或重新生成推荐。"}
            </p>
            <div className="row gap8 mt16">
              {activeFilters.length > 0 ? (
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    setBucket("");
                    setProvince("");
                    setTrait985(false);
                    setTrait211(false);
                    setTraitDfc(false);
                    setSearch("");
                  }}
                >
                  清除全部筛选
                </button>
              ) : null}
              <Link className="btn" href="/onboarding/preferences">
                去调整偏好
              </Link>
            </div>
          </div>
        ) : (
          <div>
            {items.data!.items.map((item) => {
              const added = inScheme.has(item.item_id);
              return (
                <article className="candidate-row" key={item.item_id}>
                  <div className="candidate-main">
                    <div className="school-emblem">
                      {(item.university_name ?? "?").slice(0, 1)}
                    </div>
                    <div className="school-info">
                      <div className="school-name">{item.university_name}</div>
                      <div className="major-name">{item.plan_name}</div>
                      <div className="tags">
                        <span className="tag">{item.province_name ?? "—"}</span>
                        <span className="tag">{item.city_name ?? "—"}</span>
                        {item.is_985 ? <span className="tag">985</span> : null}
                        {item.is_211 ? <span className="tag">211</span> : null}
                        {item.is_double_first_class ? <span className="tag">双一流</span> : null}
                        <span className="tag">
                          {item.subject_requirement_text || "选科要求待核验"}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="risk-cell">
                    <span className={`risk ${riskClass(item.risk_bucket)}`}>
                      {riskLabel(item.risk_bucket)}
                    </span>
                    <div className="acs-value num">
                      {formatAcs(item.acs)} <small>ACS</small>
                    </div>
                  </div>

                  <div className="candidate-meta">
                    <b className="num">
                      计划 {item.plan_count > 0 ? item.plan_count : "暂无数据"}
                    </b>
                    <br />
                    <b className="num">{formatFee(item.tuition_fee)}</b>
                  </div>

                  <div className="row-actions">
                    {added ? (
                      <span className="btn subtle" aria-disabled="true">
                        <Icon name="check" /> 已加入
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn"
                        disabled={full || addOne.isPending}
                        title={
                          !wb.scheme
                            ? `先生成方案；方案会一次性生成 ${capacity} 条`
                            : full
                              ? `方案已满 ${capacity} 条，请先在编辑器里移除再回来加入`
                              : undefined
                        }
                        onClick={() => addOne.mutate(item)}
                      >
                        <Icon name="plus" /> 加入方案
                      </button>
                    )}
                    <button type="button" className="text-link" onClick={() => setDetail(item)}>
                      查看详情
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        <div className="pager">
          <span className="small muted">
            {shown === 0
              ? "共 0 条"
              : `共 ${total.toLocaleString("zh-CN")} 条，当前 ${rangeStart}–${rangeEnd} 条`}
          </span>
          <div className="row gap8">
            <button
              type="button"
              className="btn icon"
              aria-label="上一页"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <Icon name="up" style={{ transform: "rotate(-90deg)" }} />
            </button>
            <span className="num">
              {page + 1} / {lastPage + 1}
            </span>
            <button
              type="button"
              className="btn icon"
              aria-label="下一页"
              disabled={page >= lastPage}
              onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
            >
              <Icon name="down" style={{ transform: "rotate(-90deg)" }} />
            </button>
          </div>
        </div>
      </section>

      <div className="help-note">
        <Icon name="info" />
        <span>
          风险分组数量是「其他筛选生效后、风险筛选生效前」的统计；切换到某一层会收窄列表，
          但标签上的数字不会跟着变，方便你横向比较。
        </span>
      </div>

      {detail ? <DetailDrawer item={detail} onClose={() => setDetail(null)} /> : null}
    </>
  );
}

/**
 * 详情右侧抽屉。
 *
 * 文档要求"桌面使用右侧面板、关闭后恢复焦点与查询状态"：
 * 所以抽屉是覆盖层，列表状态（筛选/页码/滚动）完全不动。
 */
function DetailDrawer({ item, onClose }: { item: RecommendationItem; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows: Array<[string, string]> = [
    ["院校", item.university_name],
    ["专业", item.plan_name],
    ["专业组 / 专业代码", item.program_label ?? "—"],
    ["院校专业序号", item.major_sequence ?? "暂无数据"],
    ["所在地", `${item.province_name ?? "—"} · ${item.city_name ?? "—"}`],
    ["选考要求", item.subject_requirement_text || "待核验"],
    ["学制", item.duration_years ? `${item.duration_years} 年` : "暂无数据"],
    ["招生计划", item.plan_count > 0 ? `${item.plan_count} 人` : "暂无数据"],
    ["学费", formatFee(item.tuition_fee)],
    ["偏好分", item.preference_score != null ? item.preference_score.toFixed(1) : "—"],
    ["置信度", item.confidence != null ? `${(item.confidence * 100).toFixed(0)}%` : "—"],
    ["合作办学", item.cooperative ? String(item.cooperative) : "无标记"],
    ["特殊要求", item.special_requirements || "无"],
  ];

  return (
    <div className="drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className="drawer"
        role="dialog"
        aria-label={`${item.university_name} ${item.plan_name} 详情`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-head">
          <div>
            <h2>{item.university_name}</h2>
            <p className="small muted">{item.plan_name}</p>
          </div>
          <button type="button" className="btn icon" aria-label="关闭详情" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>

        <div className="drawer-body">
          <div className="row gap8" style={{ marginBottom: 14 }}>
            <span className={`risk ${riskClass(item.risk_bucket)}`}>
              {riskLabel(item.risk_bucket)}
            </span>
            <span className="acs-value num" style={{ fontSize: 18 }}>
              {formatAcs(item.acs)} <small>ACS</small>
            </span>
            <span className={`tristate ${eligibilityClass(item.eligibility)}`}>
              {ELIGIBILITY_TEXT[item.eligibility] ?? item.eligibility}
            </span>
          </div>

          <div className="kv">
            {rows.map(([key, value]) => (
              <div className="kv-line" key={key}>
                <span>{key}</span>
                <span className={/\d/.test(value) && value.length < 14 ? "num" : undefined}>
                  {value}
                </span>
              </div>
            ))}
          </div>

          {item.eligibility !== "PASS" ? (
            <div className="stale-banner" style={{ marginTop: 14 }}>
              <Icon name="warn" />
              <span>
                资格结论是「{ELIGIBILITY_TEXT[item.eligibility] ?? item.eligibility}」，
                不代表一定不符合，而是有要求未能自动核验。填报前请按院校招生章程核对。
              </span>
            </div>
          ) : null}

          <p className="legal-mini mt16">
            ACS 是录取安全指数，用于比较同一批次内的相对安全程度，<strong>不是录取概率</strong>。
            计划数与学费以省考试院当年官方发布为准。
          </p>
        </div>
      </aside>
    </div>
  );
}
