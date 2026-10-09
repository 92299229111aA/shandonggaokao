"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { schemeApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import { RiskBadge } from "@/components/badges";

export default function SchemeVersionsPage() {
  const router = useRouter();
  const { state, loaded } = useFlowState();
  const [previewVersion, setPreviewVersion] = useState<number | null>(null);

  const versions = useQuery({
    queryKey: ["scheme-versions", state.schemeId],
    queryFn: () => schemeApi.versions(state.schemeId!),
    enabled: loaded && Boolean(state.schemeId),
  });

  const preview = useQuery({
    queryKey: ["scheme", state.schemeId, previewVersion],
    queryFn: () => schemeApi.get(state.schemeId!, previewVersion!),
    enabled: Boolean(state.schemeId) && previewVersion !== null,
  });

  if (loaded && !state.schemeId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>还没有志愿方案</CardTitle>
          <CardDescription>生成方案后，这里会记录每一次修改。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push("/schemes")}>回到编辑器</Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">历史版本</h1>
        <p className="text-sm text-muted-foreground">
          每次修改都会产生一个不可变的新版本，旧版本始终可读，不会被覆盖。
        </p>
      </header>

      {versions.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(versions.error)}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">版本列表</CardTitle>
            <CardDescription>点击查看该版本的完整顺序</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {(versions.data ?? []).map((version) => (
              <button
                key={version.id}
                type="button"
                onClick={() => setPreviewVersion(version.version_no)}
                className={`w-full rounded-md border p-3 text-left transition-colors ${
                  previewVersion === version.version_no
                    ? "border-primary bg-accent"
                    : "border-input hover:bg-accent/60"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">v{version.version_no}</span>
                  <Badge variant="outline">{version.operation}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{version.change_summary ?? "—"}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {version.item_count} 个志愿 ·{" "}
                  {new Date(version.created_at).toLocaleString("zh-CN", { hour12: false })}
                </p>
                {version.optimizer_stats ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    优化目标值 {String(version.optimizer_stats.objective_value ?? "—")} · 选中{" "}
                    {String(version.optimizer_stats.selected_count ?? "—")}
                  </p>
                ) : null}
              </button>
            ))}
            {versions.data?.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">暂无版本记录。</p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              {previewVersion === null ? "选择左侧版本查看" : `v${previewVersion} 的志愿顺序`}
            </CardTitle>
            {preview.data ? (
              <CardDescription>
                共 {preview.data.item_count} 个志愿 · 风险模式 {preview.data.risk_mode}
                {preview.data.is_historical ? " · 历史版本（只读）" : ""}
              </CardDescription>
            ) : null}
          </CardHeader>
          <CardContent className="max-h-[60vh] space-y-2 overflow-auto">
            {(preview.data?.items ?? []).map((item) => (
              <div key={item.position} className="flex items-center gap-3 rounded-md border p-2 text-sm">
                <span className="w-6 text-center tabular-nums text-muted-foreground">{item.position}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate">
                    {item.plan_name}
                    {item.locked ? " 🔒" : ""}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{item.university_name}</p>
                </div>
                <RiskBadge bucket={item.risk_bucket} />
                <span className="w-14 text-right text-xs tabular-nums text-muted-foreground">
                  {item.acs?.toFixed(1) ?? "—"}
                </span>
              </div>
            ))}
            {previewVersion !== null && preview.data?.items.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">该版本没有志愿。</p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <div className="flex gap-3">
        <Button variant="outline" onClick={() => router.push("/schemes")}>
          回到编辑器
        </Button>
        <Button onClick={() => router.push("/health-check")}>进入志愿体检</Button>
      </div>
    </div>
  );
}
