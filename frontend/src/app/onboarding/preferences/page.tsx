"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Input, Label } from "@/components/ui/input";
import { catalogApi, metaApi, profileApi, recommendationApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import type { RiskMode } from "@/lib/api/types";

// Fallbacks only — the live lists come from /catalog/preference-options, which derives
// them from the loaded snapshot. Hardcoding them here is what previously capped the
// province list at 9 while the data held 31 provincial-level regions.
const DISCIPLINES_FALLBACK = [
  { code: "01", name: "哲学" },
  { code: "02", name: "经济学" },
  { code: "03", name: "法学" },
  { code: "04", name: "教育学" },
  { code: "05", name: "文学" },
  { code: "06", name: "历史学" },
  { code: "07", name: "理学" },
  { code: "08", name: "工学" },
  { code: "09", name: "农学" },
  { code: "10", name: "医学" },
  { code: "11", name: "军事学" },
  { code: "12", name: "管理学" },
  { code: "13", name: "艺术学" },
  { code: "14", name: "交叉学科" },
] as const;

const PROVINCES_FALLBACK = [{ code: "37", name: "山东省" }] as const;

const RISK_MODES: Array<{ value: RiskMode; label: string; detail: string }> = [
  { value: "STEADY", label: "稳健", detail: "5 高冲 / 10 冲 / 15 边界 / 36 稳 / 30 保" },
  { value: "BALANCED", label: "均衡", detail: "8 高冲 / 17 冲 / 20 边界 / 31 稳 / 20 保" },
  { value: "AGGRESSIVE", label: "激进", detail: "15 高冲 / 25 冲 / 20 边界 / 24 稳 / 12 保" },
];

const CAREER_KEYS = [
  { key: "POSTGRAD_RATE", label: "更看重读研/升学率" },
  { key: "EMPLOYMENT_RATE", label: "更看重就业率" },
  { key: "CITY_OPPORTUNITY", label: "更看重实习与就业城市机会" },
] as const;

export default function PreferencesPage() {
  const router = useRouter();
  const { state, update, loaded } = useFlowState();

  const profile = useQuery({
    queryKey: ["profile", state.profileId],
    queryFn: () => profileApi.get(state.profileId!),
    enabled: loaded && Boolean(state.profileId),
  });
  const dataset = useQuery({ queryKey: ["dataset"], queryFn: () => metaApi.dataset() });

  const applicationLimit = dataset.data?.cycle?.application_limit ?? 96;

  // Provinces and disciplines come from the data, not from a hardcoded list. The old
  // list offered 9 provinces while the snapshot covers 31 provincial-level regions, so
  // 地域偏好 could not even name 河北.
  const preferenceOptions = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
  });

  const DISCIPLINES = preferenceOptions.data?.disciplines ?? DISCIPLINES_FALLBACK;
  const PROVINCES = preferenceOptions.data?.provinces ?? PROVINCES_FALLBACK;
  // Selecting a 学科 currently matches nothing: the plan files carry a per-school major
  // sequence number, not an MOE major code, and no 门类 column exists. Say so rather
  // than offer a control that silently does nothing.
  const disciplinesAvailable =
    preferenceOptions.data?.disciplines?.some((item) => item.available) ?? false;

  const [riskMode, setRiskMode] = useState<RiskMode>("BALANCED");
  const [targetCount, setTargetCount] = useState<number | null>(null);
  const [maxTuition, setMaxTuition] = useState("");
  const [excludeCooperative, setExcludeCooperative] = useState(false);
  const [excludePrivate, setExcludePrivate] = useState(false);
  const [minSafeCount, setMinSafeCount] = useState("");
  const [includeDisciplines, setIncludeDisciplines] = useState<string[]>([]);
  const [excludeMajors, setExcludeMajors] = useState("");
  const [includeProvinces, setIncludeProvinces] = useState<string[]>([]);
  const [excludeProvinces, setExcludeProvinces] = useState<string[]>([]);
  const [trait985, setTrait985] = useState(false);
  const [traitDoubleFirstClass, setTraitDoubleFirstClass] = useState(false);
  const [careerKeys, setCareerKeys] = useState<string[]>([]);

  useEffect(() => {
    if (targetCount === null && dataset.data?.cycle) setTargetCount(dataset.data.cycle.application_limit);
  }, [dataset.data?.cycle, targetCount]);

  const effectiveTarget = useMemo(
    () => Math.min(targetCount ?? applicationLimit, applicationLimit),
    [targetCount, applicationLimit],
  );

  const createPreference = useMutation({
    mutationFn: () => {
      const hardExcludeNames = excludeMajors
        .split(/[,，\n]/)
        .map((name) => name.trim())
        .filter(Boolean);
      return profileApi.createPreference(state.profileId!, {
        risk_mode: riskMode,
        target_count: effectiveTarget,
        max_tuition: maxTuition ? Number(maxTuition) : null,
        exclude_cooperative: excludeCooperative,
        exclude_private: excludePrivate,
        min_safe_count: minSafeCount ? Number(minSafeCount) : null,
        major_preferences: [
          ...includeDisciplines.map((code) => ({
            discipline_code: code,
            polarity: "INCLUDE",
            weight: 80,
          })),
          ...hardExcludeNames.map((name) => ({
            major_name: name,
            polarity: "EXCLUDE",
            is_hard_exclude: true,
            weight: 100,
          })),
        ],
        location_preferences: [
          ...includeProvinces.map((code) => ({
            province_code: code,
            province_name: PROVINCES.find((p) => p.code === code)?.name ?? null,
            polarity: "INCLUDE",
            weight: 70,
          })),
          ...excludeProvinces.map((code) => ({
            province_code: code,
            province_name: PROVINCES.find((p) => p.code === code)?.name ?? null,
            polarity: "EXCLUDE",
            is_hard_exclude: true,
            weight: 100,
          })),
        ],
        university_preferences: [
          ...(trait985 ? [{ trait: "IS_985", polarity: "INCLUDE", weight: 60 }] : []),
          ...(traitDoubleFirstClass
            ? [{ trait: "IS_DOUBLE_FIRST_CLASS", polarity: "INCLUDE", weight: 50 }]
            : []),
        ],
        other_preferences: careerKeys.map((key) => ({
          dimension: "CAREER",
          key,
          weight: 60,
          polarity: "INCLUDE",
        })),
      });
    },
    onSuccess: async (preference) => {
      update({ preferenceId: preference.id });
      // Generate the recommendation right away: the next page expects a session.
      try {
        const run = await recommendationApi.run({
          profile_id: state.profileId!,
          preference_id: preference.id,
        });
        update({ sessionId: run.session.id, inputWarnings: run.input_warnings ?? [] });
        router.push("/recommendations");
      } catch (error) {
        setRunError(errorMessage(error));
      }
    },
  });

  const [runError, setRunError] = useState<string | null>(null);

  if (loaded && !state.profileId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>请先填写成绩与选科</CardTitle>
          <CardDescription>偏好画像是基于你的档案建立的。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push("/onboarding/score")}>去填写成绩与选科</Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">第 2 步 · 偏好画像</h1>
        <p className="text-sm text-muted-foreground">
          偏好只影响「你有多想去」的排序，不会覆盖选科等硬性报考条件。
          标为「硬性排除」的条件在任何情况下都不会被推荐。
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>风险偏好</CardTitle>
            <CardDescription>风险层目标是软约束；最低安全数量是硬约束</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              {RISK_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  onClick={() => setRiskMode(mode.value)}
                  className={`w-full rounded-md border p-3 text-left transition-colors ${
                    riskMode === mode.value ? "border-primary bg-accent" : "border-input hover:bg-accent/60"
                  }`}
                >
                  <div className="text-sm font-medium">{mode.label}</div>
                  <div className="text-xs text-muted-foreground">{mode.detail}</div>
                </button>
              ))}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="targetCount">目标志愿数量（上限 {applicationLimit}）</Label>
              <Input
                id="targetCount"
                type="number"
                min={1}
                max={applicationLimit}
                value={effectiveTarget}
                onChange={(event) => setTargetCount(Number(event.target.value))}
              />
              {targetCount !== null && targetCount > applicationLimit ? (
                <p className="text-xs text-destructive">
                  超过政策上限，已调整为 {applicationLimit}（来自 admission_cycles）
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="minSafeCount">最低安全数量（稳+保，可留空）</Label>
              <Input
                id="minSafeCount"
                type="number"
                min={0}
                value={minSafeCount}
                onChange={(event) => setMinSafeCount(event.target.value)}
                placeholder="例如 20"
              />
              <p className="text-xs text-muted-foreground">
                资格待确认（UNKNOWN）的志愿不会被计入安全数量。
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>专业与学费</CardTitle>
            <CardDescription>专业偏好权重 35%，学费权重 10%</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>偏好的学科门类</Label>
              {preferenceOptions.data ? (
                <p className="text-xs text-muted-foreground">
                  {disciplinesAvailable
                    ? "按专业所属门类匹配，权重 35%。未填则不参与计算（不会按 0 分处理）。"
                    : "当前数据源缺少学科门类映射，选学科暂不影响结果，请改用下方「绝不接受的专业」。"}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {DISCIPLINES.map((discipline) => {
                  const active = includeDisciplines.includes(discipline.code);
                  return (
                    <button
                      key={discipline.code}
                      type="button"
                      onClick={() =>
                        setIncludeDisciplines((previous) =>
                          active
                            ? previous.filter((code) => code !== discipline.code)
                            : [...previous, discipline.code],
                        )
                      }
                      className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                        active ? "border-primary bg-primary text-primary-foreground" : "border-input hover:bg-accent"
                      }`}
                    >
                      {discipline.name}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="excludeMajors">绝不接受的专业（硬性排除，逗号分隔）</Label>
              <Input
                id="excludeMajors"
                value={excludeMajors}
                onChange={(event) => setExcludeMajors(event.target.value)}
                placeholder="例如：临床医学, 护理学"
              />
              <p className="text-xs text-muted-foreground">
                按专业名称匹配（整名或以括号等分隔符结尾），命中后永不进入方案。
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="maxTuition">学费上限（元/年）</Label>
              <Input
                id="maxTuition"
                type="number"
                min={0}
                value={maxTuition}
                onChange={(event) => setMaxTuition(event.target.value)}
                placeholder="例如 8000"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={excludeCooperative}
                onChange={(event) => setExcludeCooperative(event.target.checked)}
              />
              不接受中外合作办学
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={excludePrivate} onChange={(event) => setExcludePrivate(event.target.checked)} />
              不接受民办院校
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>地域与学校</CardTitle>
            <CardDescription>地域权重 15%，学校权重 15%，升学特征 10%</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>优先考虑的省份</Label>
              <p className="text-xs text-muted-foreground">
                {preferenceOptions.data
                  ? `${PROVINCES.length} 个省级行政区（按当前数据中可报院校数量排序）`
                  : "正在读取省份列表…"}
              </p>
              <div className="flex flex-wrap gap-2">
                {PROVINCES.map((province) => {
                  const active = includeProvinces.includes(province.code);
                  return (
                    <button
                      key={province.code}
                      type="button"
                      onClick={() =>
                        setIncludeProvinces((previous) =>
                          active
                            ? previous.filter((code) => code !== province.code)
                            : [...previous, province.code],
                        )
                      }
                      className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                        active ? "border-primary bg-primary text-primary-foreground" : "border-input hover:bg-accent"
                      }`}
                    >
                      {province.name}
                      {"university_count" in province ? (
                        <span className="ml-1 opacity-70">{province.university_count}</span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-2">
              <Label>绝不考虑的省份（硬性排除）</Label>
              <div className="flex flex-wrap gap-2">
                {PROVINCES.map((province) => {
                  const active = excludeProvinces.includes(province.code);
                  return (
                    <button
                      key={province.code}
                      type="button"
                      onClick={() =>
                        setExcludeProvinces((previous) =>
                          active
                            ? previous.filter((code) => code !== province.code)
                            : [...previous, province.code],
                        )
                      }
                      className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                        active
                          ? "border-destructive bg-destructive text-destructive-foreground"
                          : "border-input hover:bg-accent"
                      }`}
                    >
                      {province.name}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={trait985} onChange={(event) => setTrait985(event.target.checked)} />
              优先 985 院校
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={traitDoubleFirstClass}
                onChange={(event) => setTraitDoubleFirstClass(event.target.checked)}
              />
              优先双一流院校
            </label>

            <div className="space-y-2">
              <Label>职业与升学倾向</Label>
              {CAREER_KEYS.map((item) => (
                <label key={item.key} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={careerKeys.includes(item.key)}
                    onChange={(event) =>
                      setCareerKeys((previous) =>
                        event.target.checked
                          ? [...previous, item.key]
                          : previous.filter((key) => key !== item.key),
                      )
                    }
                  />
                  {item.label}
                </label>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {profile.data ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">将使用的考生档案</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 text-xs">
            <Badge variant="outline">位次 {profile.data.rank?.toLocaleString("zh-CN") ?? "—"}</Badge>
            <Badge variant="outline">总分 {profile.data.score ?? "—"}</Badge>
            <Badge variant="outline">选科 {profile.data.subjects.join(" / ")}</Badge>
            <Badge variant="outline">档案版本 v{profile.data.version}</Badge>
          </CardContent>
        </Card>
      ) : null}

      {createPreference.isError || runError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {runError ?? errorMessage(createPreference.error)}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          disabled={createPreference.isPending || !state.profileId}
          onClick={() => {
            setRunError(null);
            createPreference.mutate();
          }}
        >
          {createPreference.isPending ? "生成推荐中…" : "保存偏好并生成推荐"}
        </Button>
        <span className="text-xs text-muted-foreground">
          推荐会锁定档案版本、偏好版本、数据快照、政策版本与模型版本，可随时回溯。
        </span>
      </div>
    </div>
  );
}
