"use client";

/**
 * P05 志愿体检｜问题定位与复核
 *
 * 这一页最容易做错的两件事：
 *   1. 把「检查项 passed=false」直接显示成"未通过"。实际上很多规则只是**警告**
 *      （比如招生计划缩减），不是阻塞。所以状态与严重程度必须分开显示。
 *   2. 把健康分说成"录取把握"。健康分是产品指标，页面里必须写清它不是录取概率。
 *
 * 问题定位按 detail.plan_id 找当前方案里的对应志愿，而不是按报告里的旧序号 ——
 * 方案改过之后序号会漂移。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Icon } from "@/components/workbench/icon";
import { useToast } from "@/components/workbench/toast";
import type { HealthCheck, HealthIssue, Severity } from "@/lib/api/types";
import { healthApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import { riskBarClass } from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";
import { RISK_LABELS, RISK_ORDER } from "@/lib/utils";

/** 「待核验」类问题：不是失败，而是缺数据或缺规则，不能当成通过。 */
const UNKNOWN_CODES = new Set([
  "ELIGIBILITY_UNKNOWN",
  "UNKNOWN_REQUIREMENT",
  "UNKNOWN_IN_SAFETY_NET",
  "SNAPSHOT_TOO_OLD",
  "STALE_DATASET",
  "SNAPSHOT_NOT_PUBLISHED",
]);

const SEVERITY_LABEL: Record<Severity, string> = {
  ERROR: "阻塞",
  WARNING: "警告",
  INFO: "说明",
};

type CheckStatus = "pass" | "warn" | "fail" | "unknown";

const STATUS_META: Record<CheckStatus, { label: string; cls: string }> = {
  pass: { label: "通过", cls: "pass" },
  warn: { label: "有提示", cls: "unknown" },
  fail: { label: "未通过", cls: "fail" },
  unknown: { label: "待核验", cls: "unknown" },
};

/**
 * 单项检查的状态，完全由**严重程度**驱动，不看 passed 布尔：
 *
 *   - 有阻塞 → 未通过
 *   - 有「待核验」类问题 → 待核验（缺数据，不等于通过）
 *   - 有警告 → 有提示（引擎把"计划缩减""招生人数少"这类判为警告）
 *   - 只剩说明 → 通过
 *
 * 之前用 "infos > 0 就算有提示" 是错的：资格检查会产出一条
 * ELIGIBILITY_RECHECKED 说明（"已重新核验"），那是记录不是问题，
 * 结果"报考资格"这一项被显示成"有提示"，会误导用户以为资格有问题。
 */
function checkStatus(check: {
  passed: boolean;
  errors: number;
  warnings: number;
  infos: number;
  issue_codes: string[];
}): CheckStatus {
  if (check.errors > 0) return "fail";
  if (check.issue_codes.some((code) => UNKNOWN_CODES.has(code))) return "unknown";
  if (check.warnings > 0) return "warn";
  return "pass";
}

export default function HealthCheckPage() {
  const router = useRouter();
  const toast = useToast();
  const wb = useWorkbench();
  const { state: flow, loaded } = useFlowState();

  const [recheckEligibility, setRecheckEligibility] = useState(true);
  const [severityFilter, setSeverityFilter] = useState<"" | Severity>("");

  const scheme = useQuery({
    queryKey: ["checkup-scheme", flow.schemeId],
    queryFn: () => schemeApi.get(flow.schemeId!),
    enabled: loaded && Boolean(flow.schemeId),
  });

  const history = useQuery({
    queryKey: ["checkup-history", flow.schemeId],
    queryFn: () => healthApi.list(flow.schemeId!),
    enabled: loaded && Boolean(flow.schemeId),
  });

  const report = history.data && history.data.length > 0 ? history.data[0] : null;

  const runCheck = useMutation({
    mutationFn: () =>
      healthApi.run(flow.schemeId!, { recheck_eligibility: recheckEligibility, persist: true }),
    onSuccess: (result) => {
      toast.show(`检查完成：${result.error_count} 阻塞 / ${result.warning_count} 警告`, "ok");
      history.refetch();
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  // 用 useMemo 固定引用，避免下面两个 useMemo 每次都重算
  const schemeItems = useMemo(() => scheme.data?.items ?? [], [scheme.data]);
  const staleReport = Boolean(
    report && scheme.data && report.version_no !== scheme.data.current_version_no,
  );

  // 问题 → 当前志愿：按 plan_id 定位，不用报告里的旧序号
  const located = useMemo(() => {
    const map = new Map<number, { position: number; itemId: number }>();
    for (const item of schemeItems) {
      if (!map.has(item.plan_id)) {
        map.set(item.plan_id, { position: item.position, itemId: item.recommendation_item_id });
      }
    }
    return map;
  }, [schemeItems]);

  const issues = useMemo(() => report?.issues ?? [], [report]);

  const grouped = useMemo(() => {
    const order: Severity[] = ["ERROR", "WARNING", "INFO"];
    return order
      .filter((severity) => severityFilter === "" || severityFilter === severity)
      .map((severity) => ({
        severity,
        items: issues.filter((issue) => issue.severity === severity),
      }));
  }, [issues, severityFilter]);

  const overall = useMemo(() => {
    if (!report) return null;
    const unknown = report.checks.filter((c) => checkStatus(c) === "unknown").length;
    const blocking = report.checks.filter((c) => c.errors > 0).length;
    const warned = report.checks.filter((c) => checkStatus(c) === "warn").length;
    if (staleReport) {
      return {
        tone: "unknown" as const,
        title: "报告已过期",
        detail: `报告对应 v${report.version_no ?? "?"}，当前方案已是 v${scheme.data?.current_version_no ?? "?"}。需要重新检查才能作为当前结论。`,
      };
    }
    if (blocking > 0) {
      return {
        tone: "fail" as const,
        title: "存在阻塞问题",
        detail: `${blocking} 项检查发现阻塞问题（共 ${report.error_count} 条阻塞）。处理后再重新检查。`,
      };
    }
    if (unknown > 0) {
      return {
        tone: "unknown" as const,
        title: "部分检查待核验",
        detail: `${unknown} 项检查缺少数据或规则，无法判定。这不等于通过。`,
      };
    }
    if (warned > 0) {
      return {
        tone: "warn" as const,
        title: "必检项已通过，但有提示",
        detail: `${warned} 项检查发现非阻塞问题（${report.warning_count} 条警告、${report.info_count} 条说明），建议逐条看一下。`,
      };
    }
    return {
      tone: "pass" as const,
      title: "已通过当前规则检查",
      detail: "所有适用的必检项都已执行并通过，报告对应当前方案版本。",
    };
  }, [report, staleReport, scheme.data]);

  const exportCsv = useMutation({
    mutationFn: () => schemeApi.exportRows(flow.schemeId!),
    onSuccess: (payload) => {
      const rows = payload.rows as Array<Record<string, unknown>>;
      const header = ["序号", "院校", "专业", "专业组", "风险层", "ACS（安全指数）", "置信度"];
      const lines = [header.join(",")];
      for (const row of rows) {
        lines.push(
          [
            row.position,
            row.university_name,
            row.plan_name,
            row.program_label,
            RISK_LABELS[String(row.risk_bucket)] ?? "",
            row.acs,
            row.confidence,
          ]
            .map(csvCell)
            .join(","),
        );
      }
      lines.push("");
      lines.push(
        csvCell(
          `数据说明：ACS 是录取安全指数（0-100），不是录取概率。方案 v${payload.version_no}，` +
            `导出时间 ${new Date().toLocaleString("zh-CN")}。` +
            (staleReport ? "本报告已过期，仅作历史参考。" : ""),
        ),
      );
      // BOM 让 Excel 正确识别 UTF-8
      const blob = new Blob(["\uFEFF" + lines.join("\r\n")], {
        type: "text/csv;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `志愿方案-v${payload.version_no}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.show(`已导出 ${rows.length} 条志愿`, "ok");
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  const goToItem = (issue: HealthIssue) => {
    const planId = Number((issue.detail as Record<string, unknown> | null)?.plan_id);
    const hit = Number.isFinite(planId) ? located.get(planId) : undefined;
    if (!hit) {
      toast.show("这条志愿已经不在当前方案里了，无法定位", "warn");
      return;
    }
    router.push(`/schemes?focus=${hit.itemId}`);
  };

  // ---- 分支 ----
  if (loaded && !wb.loading && !flow.schemeId) {
    return (
      <Page>
        <EmptyCard
          title="还没有志愿方案"
          detail="体检针对的是已保存的方案。先排出 96 条志愿，再回来检查。"
          action={
            <Link className="btn primary" href="/recommendations">
              去推荐结果
            </Link>
          }
        />
      </Page>
    );
  }

  if (scheme.isPending) {
    return (
      <Page>
        <div className="card" style={{ padding: 24 }}>
          <span className="skeleton title" />
          <span className="skeleton line w80" />
          <span className="skeleton line w60" />
        </div>
      </Page>
    );
  }

  if (scheme.isError) {
    return (
      <Page>
        <div className="card" style={{ padding: 24 }}>
          <div className="row gap8" style={{ color: "var(--red)" }}>
            <Icon name="warn" />
            <strong>无法读取方案</strong>
          </div>
          <p className="small muted mt8">{errorMessage(scheme.error)}</p>
        </div>
      </Page>
    );
  }

  const itemCount = scheme.data?.item_count ?? 0;

  return (
    <Page
      actions={
        <>
          <button
            type="button"
            className="btn"
            onClick={() => exportCsv.mutate()}
            disabled={exportCsv.isPending || itemCount === 0}
          >
            <Icon name="download" /> 导出方案 CSV
          </button>
          <button type="button" className="btn" onClick={() => router.push("/schemes")}>
            返回编辑器
          </button>
        </>
      }
    >
      {/* ---- 方案摘要 ---- */}
      <div className="card" style={{ padding: "14px 20px", marginBottom: 18 }}>
        <div className="row" style={{ gap: 20 }}>
          <div className="mini-stat">
            <b className="num">{itemCount}</b>
            <small>条志愿</small>
          </div>
          <div className="summary-divider" />
          <div className="mini-stat">
            <b className="num">v{scheme.data?.current_version_no ?? "—"}</b>
            <small>当前方案版本</small>
          </div>
          <div className="summary-divider" />
          <div className="mini-stat">
            <b className="num">{report ? `v${report.version_no ?? "?"}` : "—"}</b>
            <small>报告对应版本</small>
          </div>
          <div className="summary-divider" />
          <div className="mini-stat">
            <b className="num">
              {report?.created_at ? new Date(report.created_at).toLocaleString("zh-CN") : "未检查"}
            </b>
            <small>检查时间</small>
          </div>
        </div>
      </div>

      {itemCount === 0 ? (
        <EmptyCard
          title="方案是空的"
          detail="空方案没有可检查的内容。先加入志愿，再运行体检。"
          action={
            <Link className="btn primary" href="/schemes">
              去编排志愿
            </Link>
          }
        />
      ) : !report ? (
        <section className="card">
          <div className="empty">
            <div className="report-symbol">
              <Icon name="shield" />
            </div>
            <h2>为当前方案做一次结构检查</h2>
            <p className="mt16">
              当前方案 v{scheme.data?.current_version_no}，共 {itemCount} 条志愿。
              <br />
              将检查数量、顺序、重复项、资格、选科要求、硬性偏好与数据完整性。
            </p>
            <div className="row gap8" style={{ justifyContent: "center", marginTop: 18 }}>
              <button
                type="button"
                className="btn primary"
                disabled={runCheck.isPending}
                onClick={() => runCheck.mutate()}
              >
                {runCheck.isPending ? "检查中…" : "开始检查"}
                <Icon name="arrow" />
              </button>
            </div>
            <label className="check mt16" style={{ display: "inline-flex", gap: 6, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={recheckEligibility}
                onChange={(event) => setRecheckEligibility(event.target.checked)}
                style={{ accentColor: "var(--blue)" }}
              />
              同时重新核验报考资格
            </label>
            <div className="small muted mt16">
              检查只做诊断，不会修改你的方案。招生代码、计划与资格的官方真实性以省考试院发布为准。
            </div>
          </div>
        </section>
      ) : (
        <>
          {staleReport ? (
            <div className="stale-banner" style={{ marginBottom: 16 }}>
              <Icon name="warn" />
              <span>
                这份报告检查的是 <strong>v{report.version_no ?? "?"}</strong>，当前方案已是{" "}
                <strong>v{scheme.data?.current_version_no}</strong>。下面的结论只代表历史版本，
                不能当作当前有效结论。
                <button
                  type="button"
                  className="text-link"
                  style={{ marginLeft: 6 }}
                  onClick={() => runCheck.mutate()}
                >
                  重新检查
                </button>
              </span>
            </div>
          ) : null}

          {/* ---- 整体结论 ---- */}
          <section className="card" style={{ marginBottom: 18 }}>
            <div className="card-body">
              <div className="row" style={{ gap: 16, alignItems: "flex-start" }}>
                <div className="report-symbol" style={toneStyle(overall?.tone)}>
                  <Icon
                    name={overall?.tone === "pass" ? "check" : overall?.tone === "fail" ? "warn" : "clock"}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <h2>{overall?.title}</h2>
                  <p className="small muted mt8">{overall?.detail}</p>
                  <div className="row gap8 mt16">
                    <span className="pill bad">阻塞 {report.error_count}</span>
                    <span className="pill warn">警告 {report.warning_count}</span>
                    <span className="pill">说明 {report.info_count}</span>
                    <span className="pill blue">健康分 {report.score}</span>
                  </div>
                  <p className="legal-mini mt8">
                    健康分是产品指标（由问题数量与严重程度折算），
                    <strong>不是录取概率，也不代表录取结果</strong>。
                  </p>
                </div>
                <button
                  type="button"
                  className="btn"
                  disabled={runCheck.isPending}
                  onClick={() => runCheck.mutate()}
                >
                  <Icon name="refresh" /> {runCheck.isPending ? "检查中…" : "重新检查"}
                </button>
              </div>
            </div>
          </section>

          <div className="report-layout">
            <div className="stack">
              {/* ---- 逐项检查 ---- */}
              <section className="card">
                <div className="card-head">
                  <div>
                    <h2>逐项检查</h2>
                    <small className="muted">
                      共执行 {report.checks.length} 项规则。状态与严重程度分开：
                      状态说明这一项查出了什么，严重程度说明要不要立刻处理。
                    </small>
                  </div>
                </div>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>检查项</th>
                        <th style={{ width: 96 }}>状态</th>
                        <th className="align-right" style={{ width: 64 }}>
                          阻塞
                        </th>
                        <th className="align-right" style={{ width: 64 }}>
                          警告
                        </th>
                        <th className="align-right" style={{ width: 64 }}>
                          说明
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.checks.length === 0 ? (
                        <tr>
                          <td colSpan={5} className="small muted">
                            这份报告没有保存逐项明细（旧版本生成的报告）。重新检查一次即可看到。
                          </td>
                        </tr>
                      ) : (
                        report.checks.map((check) => {
                          const status = checkStatus(check);
                          const meta = STATUS_META[status];
                          return (
                            <tr key={check.code}>
                              <td>
                                <span className="cell-strong">{check.title || check.code}</span>
                                <span className="cell-sub">{check.code}</span>
                              </td>
                              <td>
                                <span className={`tristate ${meta.cls}`}>{meta.label}</span>
                              </td>
                              <td className="align-right num">{check.errors || "—"}</td>
                              <td className="align-right num">{check.warnings || "—"}</td>
                              <td className="align-right num">{check.infos || "—"}</td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* ---- 问题列表 ---- */}
              <section className="card">
                <div className="card-head">
                  <div>
                    <h2>问题 {issues.length} 条</h2>
                    <small className="muted">
                      每条给出受影响志愿、原因与下一步；定位按稳定 ID 找当前方案里的那一项。
                    </small>
                  </div>
                  <select
                    className="select filter-select"
                    value={severityFilter}
                    aria-label="按严重程度过滤"
                    onChange={(event) => setSeverityFilter(event.target.value as "" | Severity)}
                  >
                    <option value="">全部级别</option>
                    <option value="ERROR">仅阻塞</option>
                    <option value="WARNING">仅警告</option>
                    <option value="INFO">仅说明</option>
                  </select>
                </div>

                {issues.length === 0 ? (
                  <div className="empty">
                    <Icon name="check" />
                    <p className="empty-title">没有发现问题</p>
                    <p className="small muted">当前规则下所有检查项都通过了。</p>
                  </div>
                ) : (
                  grouped.map((group) =>
                    group.items.length === 0 ? null : (
                      <div key={group.severity}>
                        <div
                          className="small muted"
                          style={{ padding: "10px 20px", background: "#f7f9fc" }}
                        >
                          {SEVERITY_LABEL[group.severity]} · {group.items.length} 条
                        </div>
                        {group.items.slice(0, 60).map((issue, index) => {
                          const planId = Number(
                            (issue.detail as Record<string, unknown> | null)?.plan_id,
                          );
                          const hit = Number.isFinite(planId) ? located.get(planId) : undefined;
                          return (
                            <div className="issue" key={`${issue.code}-${index}`}>
                              <div className="issue-heading">
                                <h3>
                                  {issue.position != null ? `第 ${issue.position} 位 · ` : ""}
                                  {issue.code}
                                </h3>
                                <span
                                  className={`tristate ${
                                    issue.severity === "ERROR"
                                      ? "fail"
                                      : issue.severity === "WARNING"
                                        ? "unknown"
                                        : "na"
                                  }`}
                                >
                                  {SEVERITY_LABEL[issue.severity]}
                                </span>
                              </div>
                              <p>{issue.message}</p>
                              {issue.suggestion ? (
                                <p className="issue-sub">建议：{issue.suggestion}</p>
                              ) : null}
                              <div className="row gap8">
                                <span className="issue-sub">
                                  {hit
                                    ? `当前方案第 ${hit.position} 位`
                                    : "该项已不在当前方案中"}
                                </span>
                                <button
                                  type="button"
                                  className="text-link"
                                  onClick={() => goToItem(issue)}
                                  disabled={!hit}
                                >
                                  定位到编辑器
                                </button>
                              </div>
                            </div>
                          );
                        })}
                        {group.items.length > 60 ? (
                          <div className="small muted" style={{ padding: "10px 20px" }}>
                            仅显示前 60 条，共 {group.items.length} 条。
                          </div>
                        ) : null}
                      </div>
                    ),
                  )
                )}
              </section>
            </div>

            <div className="stack">
              {/* ---- 风险结构 ---- */}
              <section className="card">
                <div className="card-head">
                  <h2>风险结构</h2>
                </div>
                <div className="card-body">
                  {(() => {
                    const riskCounts = (((report.summary as Record<string, unknown> | null)
                      ?.risk_counts ?? {}) as Record<string, number>);
                    return (
                      <>
                        <div className="balance">
                          {RISK_ORDER.map((key) => {
                            const count = riskCounts[key] ?? 0;
                            return count > 0 ? (
                              <span
                                key={key}
                                className={riskBarClass(key)}
                                style={{ flexGrow: count }}
                              />
                            ) : null;
                          })}
                        </div>
                        <div className="kv mt16">
                          {RISK_ORDER.map((key) => (
                            <div className="kv-line" key={key}>
                              <span>
                                <i className={`legend-dot ${riskBarClass(key)}`} />
                                {RISK_LABELS[key]}
                              </span>
                              <span className="num">{riskCounts[key] ?? 0}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    );
                  })()}
                  <p className="legal-mini mt8">
                    只展示构成。当前没有权威的「安全储备达标线」，因此不自行判定达标与否。
                  </p>
                </div>
              </section>

              {/* ---- 核验清单 ---- */}
              <section className="card">
                <div className="card-head">
                  <h2>仍需人工核验</h2>
                </div>
                <div className="card-body">
                  <ul className="legal-mini" style={{ margin: 0, paddingLeft: 16, lineHeight: 1.9 }}>
                    <li>招生代码与计划数是否与当年官方计划一致</li>
                    <li>选科要求是否满足目标院校当年招生章程</li>
                    <li>
                      体检只覆盖当前规则与数据可得的部分；缺数据的项一律标为「待核验」，不算通过
                    </li>
                  </ul>
                </div>
              </section>

              {history.data && history.data.length > 1 ? (
                <section className="card">
                  <div className="card-head">
                    <h2>历史报告</h2>
                  </div>
                  <div className="card-body">
                    {history.data.slice(0, 5).map((item: HealthCheck) => (
                      <div className="kv-line" key={item.id}>
                        <span>
                          v{item.version_no ?? "?"} ·{" "}
                          {item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "—"}
                        </span>
                        <span className="num">
                          {item.error_count}/{item.warning_count}/{item.info_count}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          </div>
        </>
      )}
    </Page>
  );
}

function toneStyle(tone?: "pass" | "warn" | "fail" | "unknown") {
  if (tone === "pass") return { color: "var(--green)", background: "var(--green-bg)" };
  if (tone === "fail") return { color: "var(--red)", background: "var(--red-bg)" };
  return { color: "var(--gold)", background: "var(--gold-bg)" };
}

function Page({
  children,
  actions,
}: {
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 05 / CHECKUP</div>
          <h1>志愿体检</h1>
          <p className="page-sub">先检查可验证的问题，再逐项核对正式招生信息。</p>
        </div>
        <div className="row gap8">{actions}</div>
      </div>
      {children}
    </>
  );
}

function EmptyCard({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: React.ReactNode;
}) {
  return (
    <section className="card">
      <div className="empty">
        <div className="report-symbol">
          <Icon name="shield" />
        </div>
        <h2>{title}</h2>
        <p className="mt16">{detail}</p>
        {action ? (
          <div className="row gap8" style={{ justifyContent: "center", marginTop: 18 }}>
            {action}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** CSV 单元格：转义引号与逗号，并阻断以 = + - @ 开头的公式注入。 */
function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  const guarded = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  if (/[",\r\n]/.test(guarded)) return `"${guarded.replace(/"/g, '""')}"`;
  return guarded;
}
