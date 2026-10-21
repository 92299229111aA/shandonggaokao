"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Input, Label, Select } from "@/components/ui/input";
import { catalogApi, metaApi, profileApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";

const SUBJECTS = [
  { code: "PHY", name: "物理" },
  { code: "CHM", name: "化学" },
  { code: "BIO", name: "生物" },
  { code: "POL", name: "政治" },
  { code: "HIS", name: "历史" },
  { code: "GEO", name: "地理" },
] as const;

const LANGUAGES = ["ENG", "JPN", "RUS", "GER", "FRA", "SPA"] as const;

/** Special conditions the rule engine can verify. Only collected when needed. */
const ATTRIBUTE_DEFS = [
  { key: "COLOR_VISION_NORMAL", label: "色觉正常（无色盲色弱）" },
  { key: "NO_COLOR_WEAKNESS", label: "无色弱" },
  { key: "HEIGHT_OK", label: "身高达标（报考航海/飞行等专业需满足章程要求）" },
  { key: "VISION_OK", label: "裸眼视力达标" },
  { key: "RURAL_SPECIAL_QUALIFIED", label: "具备高校专项计划报考资格" },
] as const;

export default function ScorePage() {
  const router = useRouter();
  const { state, update, loaded } = useFlowState();

  const cycles = useQuery({ queryKey: ["cycles"], queryFn: () => metaApi.cycles() });
  const [cycleId, setCycleId] = useState<number | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [score, setScore] = useState("");
  const [rank, setRank] = useState("");
  const [subjects, setSubjects] = useState<string[]>([]);
  const [language, setLanguage] = useState<string>("ENG");
  const [sex, setSex] = useState<string>("");
  const [subjectScores, setSubjectScores] = useState<Record<string, string>>({});
  const [attributes, setAttributes] = useState<Record<string, boolean>>({});
  const [convertNote, setConvertNote] = useState<string | null>(null);

  useEffect(() => {
    if (!loaded) return;
    if (state.cycleId) setCycleId(state.cycleId);
  }, [loaded, state.cycleId]);

  useEffect(() => {
    if (cycleId === null && cycles.data?.length) setCycleId(cycles.data[0].id);
  }, [cycleId, cycles.data]);

  const activeCycle = useMemo(
    () => cycles.data?.find((cycle) => cycle.id === cycleId) ?? cycles.data?.[0] ?? null,
    [cycleId, cycles.data],
  );

  const convertRank = useMutation({
    mutationFn: () => catalogApi.scoreToRank(activeCycle!.id, Number(score)),
    onSuccess: (result) => {
      setRank(String(result.rank));
      setConvertNote(
        result.source === "SEGMENT"
          ? `${result.exam_year} 年 ${result.score} 分对应累计位次 ${result.rank.toLocaleString("zh-CN")}`
          : (result.note ?? `使用相邻分数换算，位次 ${result.rank}`),
      );
    },
    onError: (error) => setConvertNote(errorMessage(error)),
  });

  const createProfile = useMutation({
    mutationFn: () =>
      profileApi.create({
        cycle_id: activeCycle!.id,
        display_name: displayName || null,
        score: score ? Number(score) : null,
        rank: rank ? Number(rank) : null,
        subjects,
        foreign_language: language,
        sex: sex || null,
        subject_scores: Object.entries(subjectScores)
          .filter(([, value]) => value !== "")
          .map(([code, value]) => ({ subject_code: code, score: Number(value) })),
        attributes: ATTRIBUTE_DEFS.filter((def) => attributes[def.key]).map((def) => ({
          attr_key: def.key,
          value_type: "BOOL",
          value_bool: true,
          source: "SELF_REPORTED",
        })),
      }),
    onSuccess: (profile) => {
      update({ profileId: profile.id, cycleId: profile.cycle_id, preferenceId: null, sessionId: null, schemeId: null });
      router.push("/onboarding/preferences");
    },
  });

  const canSubmit = Boolean(activeCycle) && subjects.length === 3 && (score !== "" || rank !== "");

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">第 1 步 · 成绩与选科</h1>
        <p className="text-sm text-muted-foreground">
          分数与位次至少填写一个。只填分数时，系统会用官方一分一段表换算位次——
          跨年份比较必须使用位次百分位，因此位次是计算 ACS 的基础。
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>基本信息</CardTitle>
            <CardDescription>
              当前周期：{activeCycle ? `${activeCycle.exam_year} 年 ${activeCycle.province_code} · ${activeCycle.exam_category}` : "加载中…"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="displayName">称呼（可选）</Label>
                <Input
                  id="displayName"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="例如：小明"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cycle">招生周期</Label>
                <Select
                  id="cycle"
                  value={cycleId ?? ""}
                  onChange={(event) => setCycleId(Number(event.target.value))}
                >
                  {(cycles.data ?? []).map((cycle) => (
                    <option key={cycle.id} value={cycle.id}>
                      {cycle.exam_year} 年 · 志愿上限 {cycle.application_limit} 个
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="score">高考总分</Label>
                <Input
                  id="score"
                  type="number"
                  min={0}
                  max={750}
                  value={score}
                  onChange={(event) => setScore(event.target.value)}
                  placeholder="0 - 750"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rank">位次</Label>
                <Input
                  id="rank"
                  type="number"
                  min={1}
                  value={rank}
                  onChange={(event) => setRank(event.target.value)}
                  placeholder="全省位次"
                />
              </div>
              <div className="flex items-end">
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  disabled={!score || !activeCycle || convertRank.isPending}
                  onClick={() => convertRank.mutate()}
                >
                  {convertRank.isPending ? "换算中…" : "用分数换算位次"}
                </Button>
              </div>
            </div>

            {convertNote ? (
              <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{convertNote}</p>
            ) : null}

            <div className="space-y-2">
              <Label>选考科目（必须选择 3 门）</Label>
              <div className="flex flex-wrap gap-2">
                {SUBJECTS.map((subject) => {
                  const active = subjects.includes(subject.code);
                  return (
                    <button
                      key={subject.code}
                      type="button"
                      onClick={() =>
                        setSubjects((previous) =>
                          active
                            ? previous.filter((code) => code !== subject.code)
                            : previous.length >= 3
                              ? previous
                              : [...previous, subject.code],
                        )
                      }
                      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-input hover:bg-accent"
                      }`}
                    >
                      {subject.name}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                已选 {subjects.length}/3。选科直接决定「能不能报」：要求「物理和化学」的专业，
                物化生与物化地都可以报，物生地不能报。
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="language">外语语种</Label>
                <Select id="language" value={language} onChange={(event) => setLanguage(event.target.value)}>
                  {LANGUAGES.map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="sex">性别（部分专业有要求，可选）</Label>
                <Select id="sex" value={sex} onChange={(event) => setSex(event.target.value)}>
                  <option value="">不填写</option>
                  <option value="MALE">男</option>
                  <option value="FEMALE">女</option>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>单科成绩（可选）</CardTitle>
              <CardDescription>部分专业要求英语或数学单科达标</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {SUBJECTS.filter((subject) => subjects.includes(subject.code)).map((subject) => (
                <div key={subject.code} className="flex items-center gap-3">
                  <span className="w-12 text-sm">{subject.name}</span>
                  <Input
                    type="number"
                    min={0}
                    max={150}
                    value={subjectScores[subject.code] ?? ""}
                    onChange={(event) =>
                      setSubjectScores((previous) => ({ ...previous, [subject.code]: event.target.value }))
                    }
                    placeholder="0 - 150"
                  />
                </div>
              ))}
              {subjects.length === 0 ? (
                <p className="text-xs text-muted-foreground">先选择选考科目。</p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>特殊条件（可选）</CardTitle>
              <CardDescription>
                只采集规则判断需要的信息。未填写的条件会被判为「待确认」，不会被当成符合。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {ATTRIBUTE_DEFS.map((def) => (
                <label key={def.key} className="flex items-start gap-2 text-sm">
                  <Checkbox
                    checked={attributes[def.key] ?? false}
                    onChange={(event) =>
                      setAttributes((previous) => ({ ...previous, [def.key]: event.target.checked }))
                    }
                  />
                  <span>{def.label}</span>
                </label>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      {createProfile.isError ? (
        <p className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">
          {errorMessage(createProfile.error)}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button disabled={!canSubmit || createProfile.isPending} onClick={() => createProfile.mutate()}>
          {createProfile.isPending ? "保存中…" : "保存并继续设置偏好"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {subjects.length !== 3
            ? "请选择 3 门选考科目"
            : score === "" && rank === ""
              ? "请填写分数或位次"
              : "下一步：专业、地域、学校、费用与风险偏好"}
        </span>
      </div>
    </div>
  );
}
