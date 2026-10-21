"use client";

/**
 * P00 主页｜总览工作台
 *
 * 回答一个问题："我填到哪一步、还缺什么、下一步做什么"。
 * 页面上的每个数字都来自真实接口；没有数据的地方显示可执行的空状态，不显示假统计。
 *
 * 主按钮按文档 §4.3 的优先级选择：先补基础输入，再处理下游待办。
 */

import Link from "next/link";

import { Icon } from "@/components/workbench/icon";
import {
  formatNumber,
  riskBarClass,
  riskClass,
  riskLabel,
  riskModeLabel,
  subjectList,
} from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";
import { RISK_ORDER, RISK_LABELS } from "@/lib/utils";
import type { Todo } from "@/lib/workbench-state";

const STEP_HREF: Record<string, string> = {
  profile: "/onboarding/score",
  preferences: "/onboarding/preferences",
  recommendations: "/recommendations",
  editor: "/schemes",
  checkup: "/health-check",
};

export default function HomePage() {
  const wb = useWorkbench();

  if (wb.loading) {
    return <HomeSkeleton />;
  }

  if (wb.error) {
    return (
      <div className="card" style={{ padding: 24 }}>
        <div className="row gap8" style={{ color: "var(--red)" }}>
          <Icon name="warn" />
          <strong>读取失败</strong>
        </div>
        <p className="small muted mt8">{wb.error.message}</p>
        <p className="small muted mt8">
          这与「没有数据」不同：接口没返回结果时不做任何推断。请确认后端已启动后刷新页面。
        </p>
      </div>
    );
  }

  const plan = wb.scheme;
  const items = plan?.items ?? [];
  const distribution = plan?.risk_distribution ?? {};
  const subjects = wb.profile?.subjects ?? [];
  const currentStepKey = pickCurrentStep(wb);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">MY WORKSPACE</div>
          <h1>我的志愿工作台</h1>
          <p className="page-sub">
            把成绩、偏好和选择连在一起，让每一步更清楚。
          </p>
        </div>
        <div className="row gap8">
          <Link className="btn primary" href={wb.primaryAction.href}>
            {wb.primaryAction.label}
            <Icon name="arrow" />
          </Link>
        </div>
      </div>

      {/* ---- 三个关键数字 ---- */}
      <div className="stats">
        <div className="card stat">
          <div className="stat-label">
            <span>全省位次</span>
            <span className="stat-icon">
              <Icon name="chart" />
            </span>
          </div>
          <div className="stat-value num">{formatNumber(wb.profile?.rank)}</div>
          <div className="stat-note">
            {subjectList(subjects)}
          </div>
        </div>

        <div className="card stat">
          <div className="stat-label">
            <span>我的志愿方案</span>
            <span className="stat-icon">
              <Icon name="file" />
            </span>
          </div>
          <div className="stat-value num">
            {plan?.item_count ?? 0}
            <small> / {wb.applicationLimit ?? "—"} 条</small>
          </div>
          <div className="stat-note">
            {plan
              ? `当前 v${plan.current_version_no} · ${items.filter((i) => i.locked).length} 条已锁定`
              : "还没有编排方案"}
          </div>
        </div>

        <div className="card stat">
          <div className="stat-label">
            <span>当前策略</span>
            <span className="stat-icon">
              <Icon name="shield" />
            </span>
          </div>
          <div className="stat-value">{riskModeLabel(wb.preference?.risk_mode)}</div>
          <div className="stat-note">{preferenceHeadline(wb)}</div>
        </div>
      </div>

      {/* ---- 下一步 ---- */}
      <section className="card journey">
        <div className="journey-top">
          <div>
            <div className="section-kicker">YOUR NEXT STEP</div>
            <div className="journey-title">{journeyTitle(wb, currentStepKey)}</div>
            <p className="small muted mt8">{journeyDetail(wb, currentStepKey)}</p>
          </div>
          <Link className="btn subtle" href={STEP_HREF[currentStepKey] ?? wb.primaryAction.href}>
            {wb.primaryAction.label}
            <Icon name="arrow" />
          </Link>
        </div>

        <div className="steps">
          {wb.steps.map((step, index) => {
            const href = STEP_HREF[step.key] ?? "/";
            const isCurrent = step.key === currentStepKey;
            const complete = step.status === "complete";
            const stale = step.status === "stale";
            return (
              <Link
                key={step.key}
                href={href}
                className={`journey-step${complete ? " complete" : ""}${isCurrent ? " current" : ""}`}
              >
                <div className="step-line">
                  <span className="step-circle">
                    {complete ? <Icon name="check" /> : String(index + 1)}
                  </span>
                </div>
                <strong>
                  {step.label}
                  {stale ? <span className="step-flag">需更新</span> : null}
                </strong>
                <small>{step.note}</small>
              </Link>
            );
          })}
        </div>
      </section>

      <div className="home-grid">
        <div className="stack">
          {/* ---- 我的志愿方案 ---- */}
          <section className="card">
            <div className="card-head">
              <div>
                <h2>我的志愿方案</h2>
                <small className="muted">更想去的，放在更前面</small>
              </div>
              <Link className="text-link" href="/schemes">
                {items.length > 0 ? "查看全部" : "去编排"}
                <Icon name="chevron" />
              </Link>
            </div>

            {items.length === 0 ? (
              <div className="empty" style={{ padding: "34px 20px" }}>
                <Icon name="file" />
                <p className="empty-title">还没有加入志愿</p>
                <p className="small muted">
                  从推荐结果里挑，或者在编辑器左侧候选池加入。方案为空时不能体检。
                </p>
                <Link className="btn subtle mt16" href="/recommendations">
                  去看推荐结果
                  <Icon name="arrow" />
                </Link>
              </div>
            ) : (
              <>
                {items.slice(0, 4).map((item) => (
                  <div className="plan-mini-row" key={item.recommendation_item_id}>
                    <span className="mini-order num">
                      {String(item.position).padStart(2, "0")}
                    </span>
                    <div className="school-info">
                      <div className="school-name">{item.university_name}</div>
                      <div className="major-name">{item.plan_name}</div>
                    </div>
                    <span className={`risk ${riskClass(item.risk_bucket)}`}>
                      {riskLabel(item.risk_bucket)}
                    </span>
                  </div>
                ))}

                <div className="card-body">
                  <div
                    className="balance"
                    role="img"
                    aria-label={`方案构成：${RISK_ORDER.map((k) => `${RISK_LABELS[k]} ${distribution[k] ?? 0}`).join("，")}`}
                  >
                    {RISK_ORDER.map((key) => {
                      const count = distribution[key] ?? 0;
                      if (count === 0) return null;
                      return (
                        <span
                          key={key}
                          className={riskBarClass(key)}
                          style={{ flexGrow: count }}
                        />
                      );
                    })}
                  </div>
                  <div className="balance-legend">
                    {RISK_ORDER.map((key) => (
                      <span key={key}>
                        <i className={`legend-dot ${riskBarClass(key)}`} />
                        {RISK_LABELS[key]} <b className="num">{distribution[key] ?? 0}</b>
                      </span>
                    ))}
                  </div>
                </div>

                <div className="card-footer">
                  <span>五档分布表示方案构成，不代表录取概率</span>
                  <span className="num">
                    {plan?.item_count ?? 0} / {wb.applicationLimit ?? "—"}
                  </span>
                </div>
              </>
            )}
          </section>

          <div className="notice">
            <Icon name="info" />
            <span>
              推荐与排序依据已保存的档案和偏好。改动其中任何一项，旧推荐会标记为需更新，
              已编排的方案会保留但需要复核。
            </span>
          </div>
        </div>

        <div className="stack">
          {/* ---- 待办 ---- */}
          <section className="card">
            <div className="card-head">
              <h2>待办与提醒</h2>
              <span className={`pill ${wb.todos.length > 0 ? "warn" : "ok"}`}>
                {wb.todos.length > 0 ? `${wb.todos.length} 项` : "无待办"}
              </span>
            </div>
            <div className="card-body">
              {wb.todos.length === 0 ? (
                <p className="small muted">
                  当前没有需要处理的事项。可以继续调整方案，或重新跑一次体检。
                </p>
              ) : (
                wb.todos.map((todo) => <TodoRow key={todo.key} todo={todo} />)
              )}
            </div>
          </section>

          {/* ---- 偏好摘要 ---- */}
          <section className="card">
            <div className="card-head">
              <h2>我的偏好画像</h2>
              <Link className="text-link" href="/onboarding/preferences">
                {wb.preference ? "修改" : "去设置"}
              </Link>
            </div>
            <div className="card-body">
              {wb.preference ? (
                <PreferenceSummary wb={wb} />
              ) : (
                <p className="small muted">
                  还没有保存偏好。全部「不限」也可以保存 —— 需要显式保存一次，推荐才会用上它。
                </p>
              )}
            </div>
          </section>

          {/* ---- 最近体检 ---- */}
          <section className="card">
            <div className="card-head">
              <h2>最近体检</h2>
              {wb.check ? (
                <Link className="text-link" href="/health-check">
                  查看报告
                </Link>
              ) : null}
            </div>
            <div className="card-body">
              {wb.check ? (
                <div className="stack" style={{ gap: 10 }}>
                  <div className="kv">
                    <div className="kv-line">
                      <span>检查时间</span>
                      <span className="num">
                        {new Date(wb.check.created_at ?? Date.now()).toLocaleString("zh-CN")}
                      </span>
                    </div>
                    <div className="kv-line">
                      <span>对应方案版本</span>
                      <span className="num">v{wb.check.version_no ?? "—"}</span>
                    </div>
                    <div className="kv-line">
                      <span>问题数</span>
                      <span className="num">
                        {wb.check.error_count} 阻塞 · {wb.check.warning_count} 警告 ·{" "}
                        {wb.check.info_count} 说明
                      </span>
                    </div>
                    <div className="kv-line">
                      <span>有效性</span>
                      <span>{wb.checkupStale ? "已过期，需重新检查" : "对应当前方案"}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="small muted">
                  还没有体检报告。方案非空后可以做一次结构检查。
                </p>
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-body">
              <div className="row small" style={{ color: "#627b9f" }}>
                <Icon name="info" />
                <span>先看信息，再做决定</span>
              </div>
              <p className="legal-mini mt8">
                ACS 是录取安全指数（0–100），用于比较同一批次内的相对安全程度，
                不是录取概率。招生代码、计划数、选科要求以省考试院当年官方发布为准。
              </p>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

function TodoRow({ todo }: { todo: Todo }) {
  return (
    <div className="todo">
      <span className={`todo-icon${todo.severity === "warn" ? " warn" : ""}`}>
        <Icon name={todo.icon} />
      </span>
      <div className="body">
        <div className="todo-title">{todo.title}</div>
        <p>{todo.detail}</p>
      </div>
      <Link className="text-link" href={todo.href}>
        {todo.actionLabel}
      </Link>
    </div>
  );
}

function PreferenceSummary({ wb }: { wb: ReturnType<typeof useWorkbench> }) {
  const pref = wb.preference;
  if (!pref) return null;

  const regions = (pref.location_preferences ?? [])
    .filter((p) => p.polarity === "INCLUDE")
    .map((p) => p.province_name ?? p.city_name ?? "—");
  const excludedRegions = (pref.location_preferences ?? [])
    .filter((p) => p.polarity === "EXCLUDE")
    .map((p) => p.province_name ?? p.city_name ?? "—");
  const majors = (pref.major_preferences ?? [])
    .filter((p) => p.polarity === "INCLUDE")
    .map((p) => p.major_name ?? p.discipline_code ?? "—");

  return (
    <div className="kv">
      <div className="kv-line">
        <span>意向地区</span>
        <span>{regions.length > 0 ? regions.join("、") : "不限"}</span>
      </div>
      <div className="kv-line">
        <span>明确排除</span>
        <span>{excludedRegions.length > 0 ? excludedRegions.join("、") : "无"}</span>
      </div>
      <div className="kv-line">
        <span>意向专业</span>
        <span>{majors.length > 0 ? majors.join("、") : "不限"}</span>
      </div>
      <div className="kv-line">
        <span>学费上限</span>
        <span>{pref.max_tuition ? `${pref.max_tuition.toLocaleString("zh-CN")} 元/年` : "不限"}</span>
      </div>
      <div className="kv-line">
        <span>风险策略</span>
        <span>{riskModeLabel(pref.risk_mode)}</span>
      </div>
      <div className="kv-line">
        <span>偏好版本</span>
        <span className="num">v{pref.version}</span>
      </div>
    </div>
  );
}

/** 当前所处步骤：最靠后的"未完成"步骤；都完成则停在体检 */
function pickCurrentStep(wb: ReturnType<typeof useWorkbench>): string {
  const unfinished = wb.steps.find((s) => s.status !== "complete");
  return unfinished?.key ?? "checkup";
}

function journeyTitle(wb: ReturnType<typeof useWorkbench>, key: string): string {
  if (wb.steps.every((s) => s.status === "complete")) return "五步都已完成，可以复核方案";
  if (key === "profile") return wb.profile ? "继续完善你的档案" : "从成绩与选科开始";
  if (key === "preferences") return "把这批志愿的范围缩小一点";
  if (key === "recommendations") return "生成并浏览推荐结果";
  if (key === "editor") return "继续完善你的志愿方案";
  return "给当前方案做一次体检";
}

function journeyDetail(wb: ReturnType<typeof useWorkbench>, key: string): string {
  if (key === "profile") {
    return wb.profile
      ? "档案已保存，但还缺必填项（位次或恰好 3 门选考科目）。"
      : "位次与选科决定资格判断和推荐范围，先补齐这一步。";
  }
  if (key === "preferences") {
    return "偏好决定推荐排序与硬限制。全部「不限」也可以保存。";
  }
  if (key === "recommendations") {
    return wb.recommendationStale
      ? "档案或偏好改过，当前推荐依据的是旧版本，需要重新生成。"
      : "基于已保存的档案与偏好，从全部候选里筛选。";
  }
  if (key === "editor") {
    return `已加入 ${wb.scheme?.item_count ?? 0} 条志愿，顺序可以在编辑器里随时调整。`;
  }
  return "对当前方案版本做结构、容量、重复项与已实现规则的检查。";
}

function preferenceHeadline(wb: ReturnType<typeof useWorkbench>): string {
  const pref = wb.preference;
  if (!pref) return "未设置偏好";
  const regions = (pref.location_preferences ?? [])
    .filter((p) => p.polarity === "INCLUDE")
    .map((p) => p.province_name ?? p.city_name)
    .filter(Boolean);
  const majors = (pref.major_preferences ?? [])
    .filter((p) => p.polarity === "INCLUDE")
    .map((p) => p.major_name)
    .filter(Boolean);
  const parts: string[] = [];
  if (regions.length > 0) parts.push(regions.slice(0, 3).join("、"));
  if (majors.length > 0) parts.push(majors.slice(0, 2).join("、"));
  return parts.length > 0 ? parts.join(" · ") : "地区与专业均不限";
}

function HomeSkeleton() {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">MY WORKSPACE</div>
          <h1>我的志愿工作台</h1>
          <p className="page-sub">正在读取你的档案与方案…</p>
        </div>
      </div>
      <div className="stats">
        {[0, 1, 2].map((i) => (
          <div className="card stat" key={i}>
            <span className="skeleton title" />
            <span className="skeleton line w60" />
            <span className="skeleton line w40" />
          </div>
        ))}
      </div>
      <div className="card" style={{ padding: 24 }}>
        <span className="skeleton title" />
        <span className="skeleton line w80" />
        <span className="skeleton line w60" />
      </div>
    </>
  );
}
