"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { SeverityBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Select } from "@/components/ui/input";
import { healthApi, schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type { HealthCheck, HealthIssue, Severity } from "@/lib/api/types";

const SEVERITY_ORDER: Severity[] = ["ERROR", "WARNING", "INFO"];

export default function HealthCheckPage() {
  const router = useRouter();
  const { state, loaded } = useFlowState();
  const [recheck, setRecheck] = useState(false);
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [report, setReport] = useState<HealthCheck | null>(null);

  const scheme = useQuery({
    queryKey: ["scheme", state.schemeId],
    queryFn: () => schemeApi.get(state.schemeId!),
    enabled: loaded && Boolean(state.schemeId),
  });

  const runCheck = useMutation({
    mutationFn: () =>
      healthApi.run(state.schemeId!, { recheck_eligibility: recheck, persist: true }),
    onSuccess: setReport,
  });

  const exportScheme = useQuery({
    queryKey: ["scheme-export", state.schemeId],
    queryFn: () => schemeApi.exportRows(state.schemeId!),
    enabled: false,
  });

  const grouped = useMemo(() => {
    const issues = report?.issues ?? [];
    const filtered = severityFilter
      ? issues.filter((issue) => issue.severity === severityFilter)
      : issues;
    return SEVERITY_ORDER.map((severity) => ({
      severity,
      items: filtered.filter((issue) => issue.severity === severity),
    })).filter((group) => group.items.length > 0);
  }, [report, severityFilter]);

  if (loaded && !state.schemeId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>还没有志愿方案</CardTitle>
          <CardDescription>体检需要一份已生成的志愿方案。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push("/recommendations")}>先去生成推荐</Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">第 5 步 · 志愿体检</h1>
        <p className="text-sm text-muted-foreground">
          体检只报告问题，不会擅自修改你的方案。健康分只是产品指标，不是录取概率。
        </p>
      </header>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">运行体检</CardTitle>
          <CardDescription>
            {scheme.data
              ? `当前方案 v${scheme.data.current_version_no} · ${scheme.data.item_count} 个志愿`
              : "加载中…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={recheck} onChange={(event) => setRecheck(event.target.checked)} />
            按当前选科重新核对报考资格
          </label>
          <Button disabled={runCheck.isPending || !scheme.data} onClick={() => runCheck.mutate()}>
            {runCheck.isPending ? "体检中…" : "开始体检"}
          </Button>
          <Button
            variant="outline"
            onClick={async () => {
              const result = await exportScheme.refetch();
              const rows = result.data?.rows ?? [];
              if (!rows.length) return;
              const header = ["顺序", "专业", "学校", "风险层", "ACS", "可信度", "偏好", "学费", "计划"];
              const csv = [
                header.join(","),
                ...rows.map((row) =>
                  [
                    row.position,
                    `"${row.plan_name}"`,
                    `"${row.university_name}"`,
                    row.risk_bucket ?? "",
                    row.acs ?? "",
                    row.confidence ?? "",
                    row.preference_score ?? "",
                    row.tuition_fee ?? "",
                    row.plan_count ?? "",
                  ].join(","),
                ),
              ].join("\n");
              const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = `志愿方案-v${scheme.data?.current_version_no ?? 1}.csv`;
              link.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 CSV
          </Button>
        </CardContent>
      </Card>

      {runCheck.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(runCheck.error)}
        </p>
      ) : null}

      {report ? (
        <>
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">方案健康分</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold tabular-nums">{report.score}</p>
                <p className="mt-1 text-xs text-muted-foreground">满分 100</p>
              </CardContent>
            </Card>
            <StatCard label="必须处理" value={report.error_count} tone="destructive" />
            <StatCard label="建议处理" value={report.warning_count} tone="warning" />
            <StatCard label="提示" value={report.info_count} tone="muted" />
          </div>

          <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            {report.disclaimer}
          </p>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">11 项检查</CardTitle>
              <CardDescription>逐项结果</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {report.checks.map((check) => (
                <div
                  key={check.code}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{check.title}</p>
                    <p className="text-xs text-muted-foreground">{check.code}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {check.errors ? <Badge variant="destructive">{check.errors}</Badge> : null}
                    {check.warnings ? <Badge variant="warning">{check.warnings}</Badge> : null}
                    {check.infos ? <Badge variant="outline">{check.infos}</Badge> : null}
                    {check.passed ? <Badge variant="success">通过</Badge> : null}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between gap-4 pb-3">
              <div>
                <CardTitle className="text-sm">问题清单（{report.issues.length}）</CardTitle>
                <CardDescription>按严重程度分组，每条都给出建议</CardDescription>
              </div>
              <Select
                className="w-36"
                value={severityFilter}
                onChange={(event) => setSeverityFilter(event.target.value)}
              >
                <option value="">全部</option>
                <option value="ERROR">仅必须处理</option>
                <option value="WARNING">仅建议处理</option>
                <option value="INFO">仅提示</option>
              </Select>
            </CardHeader>
            <CardContent className="space-y-4">
              {grouped.map((group) => (
                <div key={group.severity} className="space-y-2">
                  <h3 className="text-sm font-medium">
                    {group.severity === "ERROR"
                      ? "必须处理"
                      : group.severity === "WARNING"
                        ? "建议处理"
                        : "提示"}
                    （{group.items.length}）
                  </h3>
                  {group.items.map((issue, index) => (
                    <IssueRow key={`${issue.code}-${issue.position ?? "x"}-${index}`} issue={issue} />
                  ))}
                </div>
              ))}
              {report.issues.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  没有发现问题。请注意：体检通过不等于一定录取，志愿顺序与保底仍需你自行判断。
                </p>
              ) : null}
            </CardContent>
          </Card>
        </>
      ) : null}

      <div className="flex gap-3">
        <Button variant="outline" onClick={() => router.push("/schemes")}>
          回到编辑器调整
        </Button>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "destructive" | "warning" | "muted";
}) {
  const color =
    tone === "destructive"
      ? "text-destructive"
      : tone === "warning"
        ? "text-amber-700"
        : "text-muted-foreground";
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className={`text-3xl font-semibold tabular-nums ${color}`}>{value}</p>
        <p className="mt-1 text-xs text-muted-foreground">条问题</p>
      </CardContent>
    </Card>
  );
}

function IssueRow({ issue }: { issue: HealthIssue }) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={issue.severity} />
            {issue.position !== null ? (
              <Badge variant="outline">第 {issue.position} 个志愿</Badge>
            ) : null}
            <span className="text-xs text-muted-foreground">{issue.code}</span>
          </div>
          <p className="text-sm">{issue.message}</p>
          {issue.suggestion ? (
            <p className="text-xs text-muted-foreground">建议：{issue.suggestion}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
