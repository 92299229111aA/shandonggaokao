"use client";

/**
 * P02 偏好画像｜意向与限制
 *
 * 这一页最容易做错的地方是把「倾向」和「必须满足」混成一个控件。
 * 所以分成两组，并在界面上明说：
 *   - 排序倾向（INCLUDE）：影响匹配分与排序，不排除其他候选
 *   - 硬限制（EXCLUDE + is_hard_exclude）：真的会被过滤掉
 *
 * 另外两条底线：
 *   - 「不限」与具体选择互斥
 *   - 同一个地区不能既关注又排除；发生冲突时当场提示，而不是等推荐为空
 */

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Icon } from "@/components/workbench/icon";
import { useToast } from "@/components/workbench/toast";
import { catalogApi, profileApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import { riskModeLabel } from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";

const RISK_MODES = [
  {
    value: "STEADY",
    label: "稳妥型",
    detail: "优先关注相对安全的选择，仍需要看资格。",
  },
  {
    value: "BALANCED",
    label: "均衡型",
    detail: "兼顾不同风险层，保留探索与安全储备。",
    recommended: true,
  },
  {
    value: "AGGRESSIVE",
    label: "激进型",
    detail: "愿意接受更多挑战项，注意整体结构。",
  },
] as const;

export default function PreferencesPage() {
  const router = useRouter();
  const toast = useToast();
  const wb = useWorkbench();
  const queryClient = useQueryClient();
  const { state: flow, update, loaded } = useFlowState();

  const options = useQuery({
    queryKey: ["preference-options"],
    queryFn: () => catalogApi.preferenceOptions(),
    enabled: loaded,
  });

  // 地区 / 专业：关注 与 排除 分开存
  const [includeProvinces, setIncludeProvinces] = useState<string[]>([]);
  const [excludeProvinces, setExcludeProvinces] = useState<string[]>([]);
  const [includeDisciplines, setIncludeDisciplines] = useState<string[]>([]);
  const [excludeMajors, setExcludeMajors] = useState("");
  const [trait985, setTrait985] = useState(false);
  const [trait211, setTrait211] = useState(false);
  const [traitDfc, setTraitDfc] = useState(false);
  const [tuitionMode, setTuitionMode] = useState<"none" | "limit">("none");
  const [maxTuition, setMaxTuition] = useState("");
  const [excludePrivate, setExcludePrivate] = useState(false);
  const [excludeCooperative, setExcludeCooperative] = useState(false);
  const [riskMode, setRiskMode] = useState<"STEADY" | "BALANCED" | "AGGRESSIVE">("BALANCED");
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const hydrated = useRef(false);

  const provinces = useMemo(() => options.data?.provinces ?? [], [options.data]);
  const disciplines = useMemo(
    () => (options.data?.disciplines ?? []).filter((d) => d.available),
    [options.data],
  );

  // 已有偏好时回填（等偏好查询返回后再水合，避免和 P01 一样的竞态）
  useEffect(() => {
    if (!loaded || hydrated.current) return;
    const pref = wb.preference;
    if (!pref) {
      if (flow.profileId && wb.loading) return;
      hydrated.current = true;
      return;
    }
    const loc = pref.location_preferences ?? [];
    setIncludeProvinces(
      loc.filter((p) => p.polarity === "INCLUDE").map((p) => p.province_code ?? "").filter(Boolean),
    );
    setExcludeProvinces(
      loc.filter((p) => p.polarity === "EXCLUDE").map((p) => p.province_code ?? "").filter(Boolean),
    );
    setIncludeDisciplines(
      (pref.major_preferences ?? [])
        .filter((p) => p.polarity === "INCLUDE")
        .map((p) => p.discipline_code ?? "")
        .filter(Boolean),
    );
    setExcludeMajors(
      (pref.major_preferences ?? [])
        .filter((p) => p.polarity === "EXCLUDE")
        .map((p) => p.major_name ?? "")
        .filter(Boolean)
        .join("、"),
    );
    const traits = new Set(
      (pref.university_preferences ?? [])
        .filter((p) => p.polarity === "INCLUDE")
        .map((p) => p.trait),
    );
    setTrait985(traits.has("IS_985"));
    setTrait211(traits.has("IS_211"));
    setTraitDfc(traits.has("IS_DOUBLE_FIRST_CLASS"));
    if (pref.max_tuition) {
      setTuitionMode("limit");
      setMaxTuition(String(pref.max_tuition));
    }
    setExcludePrivate(Boolean(pref.exclude_private));
    setExcludeCooperative(Boolean(pref.exclude_cooperative));
    setRiskMode((pref.risk_mode as typeof riskMode) || "BALANCED");
    setSavedVersion(pref.version);
    hydrated.current = true;
  }, [loaded, wb.preference, wb.loading, flow.profileId]);

  const tuitionValid =
    tuitionMode === "none" || (/^\d+$/.test(maxTuition) && Number(maxTuition) > 0);

  // 冲突：同一省份不能既关注又排除（点击时自动迁移，这里再兜一层）
  const conflictProvinces = includeProvinces.filter((c) => excludeProvinces.includes(c));

  const toggleProvince = (code: string, kind: "include" | "exclude") => {
    if (kind === "include") {
      if (includeProvinces.includes(code)) {
        setIncludeProvinces(includeProvinces.filter((c) => c !== code));
        return;
      }
      if (excludeProvinces.includes(code)) {
        setExcludeProvinces(excludeProvinces.filter((c) => c !== code));
        toast.show("该地区已从「明确排除」移到「优先关注」", "warn");
      }
      setIncludeProvinces([...includeProvinces, code]);
      return;
    }
    if (excludeProvinces.includes(code)) {
      setExcludeProvinces(excludeProvinces.filter((c) => c !== code));
      return;
    }
    if (includeProvinces.includes(code)) {
      setIncludeProvinces(includeProvinces.filter((c) => c !== code));
      toast.show("该地区已从「优先关注」移到「明确排除」", "warn");
    }
    setExcludeProvinces([...excludeProvinces, code]);
  };

  const save = useMutation({
    mutationFn: async () => {
      if (!flow.profileId) throw new Error("还没有档案，请先完成成绩与选科");
      const excluded = excludeMajors
        .split(/[,，、\n]/)
        .map((name) => name.trim())
        .filter(Boolean);
      return profileApi.createPreference(flow.profileId, {
        risk_mode: riskMode,
        max_tuition: tuitionMode === "limit" ? Number(maxTuition) : null,
        exclude_private: excludePrivate,
        exclude_cooperative: excludeCooperative,
        major_preferences: [
          ...includeDisciplines.map((code) => ({
            discipline_code: code,
            polarity: "INCLUDE",
            weight: 80,
          })),
          ...excluded.map((name) => ({
            major_name: name,
            polarity: "EXCLUDE",
            is_hard_exclude: true,
            weight: 100,
          })),
        ],
        location_preferences: [
          ...includeProvinces.map((code) => ({
            province_code: code,
            province_name: provinces.find((p) => p.code === code)?.name ?? null,
            polarity: "INCLUDE",
            weight: 70,
          })),
          ...excludeProvinces.map((code) => ({
            province_code: code,
            province_name: provinces.find((p) => p.code === code)?.name ?? null,
            polarity: "EXCLUDE",
            is_hard_exclude: true,
            weight: 100,
          })),
        ],
        university_preferences: [
          ...(trait985 ? [{ trait: "IS_985", polarity: "INCLUDE", weight: 60 }] : []),
          ...(trait211 ? [{ trait: "IS_211", polarity: "INCLUDE", weight: 55 }] : []),
          ...(traitDfc ? [{ trait: "IS_DOUBLE_FIRST_CLASS", polarity: "INCLUDE", weight: 50 }] : []),
        ],
      });
    },
    onSuccess: async (preference) => {
      update({ preferenceId: preference.id });
      setSavedVersion(preference.version);
      toast.show("偏好已保存", "ok");
      // 偏好变了：推荐已过期，工作台摘要与推荐页的依据都要重新取
      await queryClient.invalidateQueries({ queryKey: ["wb"] });
      await queryClient.invalidateQueries({ queryKey: ["reco-items"] });
      router.push("/recommendations");
    },
    onError: (error) => toast.show(errorMessage(error), "error"),
  });

  const onSaveOnly = () => {
    if (!tuitionValid) {
      toast.show("学费上限必须是正整数", "error");
      return;
    }
    save.mutate();
  };


  /** 恢复全部不限：只重置本页，不清档案与方案 */
  function resetAll() {
    setIncludeProvinces([]);
    setExcludeProvinces([]);
    setIncludeDisciplines([]);
    setExcludeMajors("");
    setTrait985(false);
    setTrait211(false);
    setTraitDfc(false);
    setTuitionMode("none");
    setMaxTuition("");
    setExcludePrivate(false);
    setExcludeCooperative(false);
    setRiskMode("BALANCED");
    toast.show("已恢复为全部不限（尚未保存）", "warn");
  }

  const hardLimitCount =
    excludeProvinces.length +
    excludeMajors.split(/[,，、\n]/).filter((s) => s.trim()).length +
    (tuitionMode === "limit" ? 1 : 0) +
    (excludePrivate ? 1 : 0) +
    (excludeCooperative ? 1 : 0);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 02 / PREFERENCES</div>
          <h1>偏好画像</h1>
          <p className="page-sub">让选择更贴近你的意向，同时把不能接受的条件说清楚。</p>
        </div>
        <div className="row gap8">
          <button type="button" className="btn" onClick={() => resetAll()}>
            <Icon name="refresh" /> 恢复不限
          </button>
        </div>
      </div>

      {conflictProvinces.length > 0 ? (
        <div className="stale-banner" style={{ marginBottom: 18 }}>
          <Icon name="warn" />
          <span>
            这些地区同时出现在「优先关注」和「明确排除」里：{conflictProvinces.join("、")}。
            保存前需要只保留一边。
          </span>
        </div>
      ) : null}

      <div className="form-layout">
        <div>
          <div className="card">
            {/* ---- 01 地区 ---- */}
            <section className="form-block">
              <div className="block-title">
                <span className="section-number">01</span>
                <h2>想去哪些地区？</h2>
                <span className="pill blue push">排序倾向</span>
              </div>
              <p className="small muted">优先展示这些地区，不会因此排除其他候选。</p>
              <div className="chips mt16">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={includeProvinces.length === 0}
                  onClick={() => setIncludeProvinces([])}
                >
                  不限
                </button>
                {provinces.map((province) => (
                  <button
                    key={province.code}
                    type="button"
                    className="choice"
                    aria-pressed={includeProvinces.includes(province.code)}
                    onClick={() => toggleProvince(province.code, "include")}
                    title={`${province.university_count} 所院校`}
                  >
                    {province.name}
                    {province.is_home ? <span className="tag">本省</span> : null}
                  </button>
                ))}
              </div>

              <div className="block-title mt24">
                <h3>明确排除的地区</h3>
                <span className="pill warn">硬限制</span>
              </div>
              <p className="small muted">
                排除地区不会出现在更新后的候选中；已加入方案的会保留并提示复核。
              </p>
              <div className="chips mt16">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={excludeProvinces.length === 0}
                  onClick={() => setExcludeProvinces([])}
                >
                  不排除
                </button>
                {provinces.map((province) => (
                  <button
                    key={province.code}
                    type="button"
                    className="choice exclude"
                    aria-pressed={excludeProvinces.includes(province.code)}
                    onClick={() => toggleProvince(province.code, "exclude")}
                  >
                    {province.name}
                  </button>
                ))}
              </div>
            </section>

            {/* ---- 02 专业 ---- */}
            <section className="form-block">
              <div className="block-title">
                <span className="section-number">02</span>
                <h2>更感兴趣的专业</h2>
                <span className="pill blue push">排序倾向</span>
              </div>
              <p className="small muted">
                按专业门类选择，比填具体专业名称更稳（年度间专业名称会变）。
              </p>
              <div className="chips mt16">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={includeDisciplines.length === 0}
                  onClick={() => setIncludeDisciplines([])}
                >
                  不限
                </button>
                {disciplines.map((discipline) => (
                  <button
                    key={discipline.code}
                    type="button"
                    className="choice"
                    aria-pressed={includeDisciplines.includes(discipline.code)}
                    onClick={() =>
                      setIncludeDisciplines(
                        includeDisciplines.includes(discipline.code)
                          ? includeDisciplines.filter((c) => c !== discipline.code)
                          : [...includeDisciplines, discipline.code],
                      )
                    }
                  >
                    {discipline.name}
                  </button>
                ))}
              </div>

              <div className="field mt24">
                <label htmlFor="exclude-majors">
                  明确排除的专业 <span className="pill warn">硬限制</span>
                </label>
                <input
                  id="exclude-majors"
                  className="input"
                  value={excludeMajors}
                  onChange={(event) => setExcludeMajors(event.target.value)}
                  placeholder="用顿号或逗号分隔，例如：护理学、学前教育"
                />
                <span className="field-hint">
                  按专业名称匹配，设了就会被真的过滤掉，不是只影响排序。
                </span>
              </div>
            </section>

            {/* ---- 03 院校 ---- */}
            <section className="form-block">
              <div className="block-title">
                <span className="section-number">03</span>
                <h2>院校层次</h2>
                <span className="pill blue push">排序倾向</span>
              </div>
              <p className="small muted">
                985 / 211 / 双一流是<strong>有重叠</strong>的标签，不是互斥等级，可以同时选。
              </p>
              <div className="chips mt16">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={trait985}
                  onClick={() => setTrait985(!trait985)}
                >
                  优先 985
                </button>
                <button
                  type="button"
                  className="choice"
                  aria-pressed={trait211}
                  onClick={() => setTrait211(!trait211)}
                >
                  优先 211
                </button>
                <button
                  type="button"
                  className="choice"
                  aria-pressed={traitDfc}
                  onClick={() => setTraitDfc(!traitDfc)}
                >
                  优先双一流
                </button>
              </div>
            </section>

            {/* ---- 04 费用与项目 ---- */}
            <section className="form-block">
              <div className="block-title">
                <span className="section-number">04</span>
                <h2>费用与接受范围</h2>
              </div>
              <div className="field">
                <label>年度学费上限</label>
                <div className="row gap8">
                  <button
                    type="button"
                    className="choice"
                    aria-pressed={tuitionMode === "none"}
                    onClick={() => {
                      setTuitionMode("none");
                      setMaxTuition("");
                    }}
                  >
                    不限
                  </button>
                  <button
                    type="button"
                    className="choice"
                    aria-pressed={tuitionMode === "limit"}
                    onClick={() => setTuitionMode("limit")}
                  >
                    设置上限
                  </button>
                  {tuitionMode === "limit" ? (
                    <input
                      className={`input num${tuitionValid ? "" : " is-error"}`}
                      style={{ width: 160 }}
                      value={maxTuition}
                      inputMode="numeric"
                      placeholder="例如 8000"
                      onChange={(event) => setMaxTuition(event.target.value)}
                    />
                  ) : null}
                </div>
                <span className="field-hint">
                  空值表示不限，不是 0。未知费用不当作免费，也不当作满足预算。
                </span>
                {!tuitionValid ? (
                  <span className="field-error" role="alert">
                    学费上限必须是正整数
                  </span>
                ) : null}
              </div>

              <div className="attribute-list mt16">
                <label className="attribute-row">
                  <input
                    type="checkbox"
                    checked={excludeCooperative}
                    onChange={(event) => setExcludeCooperative(event.target.checked)}
                  />
                  <span>不接受中外合作办学 / 港澳台合作项目</span>
                </label>
                <label className="attribute-row">
                  <input
                    type="checkbox"
                    checked={excludePrivate}
                    onChange={(event) => setExcludePrivate(event.target.checked)}
                  />
                  <span>不接受民办院校</span>
                </label>
              </div>
              <p className="legal-mini mt8">
                这两项只在数据里有可靠标记时生效，不会因为学费高就推断成合作办学。
              </p>
            </section>

            {/* ---- 05 风险策略 ---- */}
            <section className="form-block">
              <div className="block-title">
                <span className="section-number">05</span>
                <h2>风险策略</h2>
              </div>
              <div className="strategy-grid">
                {RISK_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    className="strategy"
                    aria-pressed={riskMode === mode.value}
                    onClick={() => setRiskMode(mode.value)}
                  >
                    <strong>
                      {mode.label}
                      {"recommended" in mode && mode.recommended ? (
                        <span className="pill blue">默认</span>
                      ) : null}
                    </strong>
                    <p>{mode.detail}</p>
                  </button>
                ))}
              </div>
              <p className="legal-mini mt8">
                策略影响五档的构成倾向，不承诺录取概率，也不改变 ACS 的计算方式。
              </p>
            </section>
          </div>

          <div className="form-actions">
            <span className="small muted">
              <Icon name="info" />
              {savedVersion ? `当前偏好 v${savedVersion}` : "保存后才会用于推荐"}
            </span>
            <div className="row">
              <button type="button" className="btn" onClick={onSaveOnly} disabled={save.isPending}>
                保存偏好
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={onSaveOnly}
                disabled={save.isPending}
              >
                {save.isPending ? "保存中…" : "保存并查看推荐"}
                <Icon name="arrow" />
              </button>
            </div>
          </div>
        </div>

        <aside className="stack summary-aside summary-sticky">
          <div className="card">
            <div className="card-head">
              <h2>你的偏好摘要</h2>
              <Icon name="prefs" />
            </div>
            <div className="card-body">
              <div className="kv">
                <div className="kv-line">
                  <span>优先关注的地区</span>
                  <span>
                    {includeProvinces.length > 0
                      ? includeProvinces
                          .map((code) => provinces.find((p) => p.code === code)?.name)
                          .filter(Boolean)
                          .join("、")
                      : "不限"}
                  </span>
                </div>
                <div className="kv-line">
                  <span>优先关注的专业</span>
                  <span>
                    {includeDisciplines.length > 0
                      ? includeDisciplines
                          .map((code) => disciplines.find((d) => d.code === code)?.name)
                          .filter(Boolean)
                          .join("、")
                      : "不限"}
                  </span>
                </div>
                <div className="kv-line">
                  <span>明确排除</span>
                  <span>
                    {[
                      excludeProvinces.length > 0 ? `${excludeProvinces.length} 个地区` : null,
                      excludeMajors.trim() ? "排除专业" : null,
                      excludePrivate ? "民办" : null,
                      excludeCooperative ? "合作办学" : null,
                    ]
                      .filter(Boolean)
                      .join("、") || "无"}
                  </span>
                </div>
                <div className="kv-line">
                  <span>学费上限</span>
                  <span>
                    {tuitionMode === "limit" && tuitionValid
                      ? `${Number(maxTuition).toLocaleString("zh-CN")} 元/年`
                      : "不限"}
                  </span>
                </div>
                <div className="kv-line">
                  <span>风险策略</span>
                  <span>{riskModeLabel(riskMode)}</span>
                </div>
                <div className="kv-line">
                  <span>硬限制条数</span>
                  <span className="num">{hardLimitCount}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-body">
              <div className="row small" style={{ color: "#627b9f" }}>
                <Icon name="info" />
                <span>倾向与硬限制不一样</span>
              </div>
              <p className="legal-mini mt8">
                「优先关注」只影响排序，不排除其他候选；「明确排除」会真的过滤掉。
              </p>
              <p className="legal-mini mt8">
                保存偏好不会自动改写已编排的方案，但旧推荐会标记为需更新。
              </p>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
