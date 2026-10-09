"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { metaApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { ACS_DISCLAIMER } from "@/lib/utils";
import { useFlowState } from "@/lib/flow-state";

export default function HomePage() {
  const { state, update, reset } = useFlowState();
  const dataset = useQuery({ queryKey: ["dataset"], queryFn: () => metaApi.dataset() });

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold">山东高考智能志愿系统</h1>
        <p className="text-sm text-muted-foreground">
          V1 支持范围：山东省 · 夏季高考 · 普通类 · 常规批第 1 次志愿 · 本科计划。
          系统按官方规则判断报考资格，用历史数据计算录取安全指数，再按你的真实偏好生成最多
          {" "}
          {dataset.data?.cycle?.application_limit ?? 96} 个「专业（专业类）+学校」志愿方案。
        </p>
      </section>

      {dataset.isError ? (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">无法读取数据版本</CardTitle>
            <CardDescription>{errorMessage(dataset.error)}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>当前数据版本</CardTitle>
            <CardDescription>所有政策数字都从数据库读取</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label="数据年份" value={dataset.data?.data_year_label ?? "—"} />
            <Row label="数据快照" value={dataset.data?.snapshot?.code ?? "无已发布快照"} />
            <Row label="模型版本" value={dataset.data?.model_version ?? "—"} />
            <Row
              label="志愿数量上限"
              value={
                dataset.data?.cycle ? `${dataset.data.cycle.application_limit} 个` : "—"
              }
            />
            <Row label="可用招生计划" value={`${dataset.data?.plan_count ?? 0} 条`} />
            <Row label="覆盖院校" value={`${dataset.data?.university_count ?? 0} 所`} />
            {dataset.data?.snapshot?.published_at ? (
              <Row
                label="发布时间"
                value={new Date(dataset.data.snapshot.published_at).toLocaleDateString("zh-CN")}
              />
            ) : null}
          </CardContent>
        </Card>

        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>开始填报</CardTitle>
            <CardDescription>
              依次完成「成绩与选科 → 偏好画像 → 推荐结果 → 志愿编辑器 → 志愿体检」
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {dataset.data?.warnings?.length ? (
              <ul className="space-y-1 rounded-md border border-[hsl(var(--risk-border))] bg-amber-50 p-3 text-sm text-amber-900">
                {dataset.data.warnings.map((warning) => (
                  <li key={warning}>⚠ {warning}</li>
                ))}
              </ul>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                asChild
                onClick={() => {
                  if (dataset.data?.cycle) update({ cycleId: dataset.data.cycle.id });
                }}
              >
                <Link href="/onboarding/score">
                  {state.profileId ? "继续填写成绩与选科" : "开始填写成绩与选科"}
                </Link>
              </Button>
              {state.schemeId ? (
                <Button variant="outline" asChild>
                  <Link href="/schemes">回到我的志愿方案</Link>
                </Button>
              ) : null}
              {state.profileId ? (
                <Button variant="ghost" size="sm" onClick={reset}>
                  清除本机进度
                </Button>
              ) : null}
            </div>

            <div className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">三层严格分离</p>
              <p className="mt-1">
                资格（能不能报）· ACS（历史数据下有多安全）· 偏好（你有多想去）分别计算，互不覆盖。
                资格不符的志愿不会进入方案；不确定的志愿可以保留，但不会被算作保底。
              </p>
              <p className="mt-2">{ACS_DISCLAIMER}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">六步流程</h2>
        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
          {[
            { step: "1", title: "成绩与选科", detail: "分数/位次、三门选科、单科成绩、体检等特殊条件" },
            { step: "2", title: "偏好画像", detail: "专业、地域、学校、学费、升学与职业方向、风险模式" },
            { step: "3", title: "推荐结果", detail: "分层候选池，展示 ACS、可信度、偏好匹配与近年位次" },
            { step: "4", title: "志愿编辑器", detail: "拖拽排序、增删、锁定、重新优化、历史版本" },
            { step: "5", title: "志愿体检", detail: "资格、重复、数量上限、风险分布、顺序、数据时效" },
            { step: "6", title: "导出", detail: "导出最终顺序，供官方系统填报时参考" },
          ].map((item) => (
            <Card key={item.step}>
              <CardHeader className="p-4">
                <Badge variant="secondary" className="w-fit">
                  第 {item.step} 步
                </Badge>
                <CardTitle className="pt-2 text-sm">{item.title}</CardTitle>
                <CardDescription className="text-xs">{item.detail}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}
