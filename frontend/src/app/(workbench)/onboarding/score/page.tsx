"use client";

/**
 * P01 成绩与选科｜考生档案
 *
 * 三件事必须分清，否则用户会被误导：
 *   1. 本地草稿 ≠ 已完成业务步骤。草稿只写本机，「保存并进入」才真的入库。
 *   2. 位次校验只做格式检查。没有匹配年份的一分一段就不编造"范围通过"。
 *   3. 改已保存的档案会影响下游：旧推荐失效、旧体检失效，但方案保留。
 */

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Icon } from "@/components/workbench/icon";
import { useToast } from "@/components/workbench/toast";
import { catalogApi, profileApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import { useFlowState } from "@/lib/flow-state";
import { subjectList } from "@/lib/workbench-format";
import { useWorkbench } from "@/lib/workbench-state";

const SUBJECTS = [
  { code: "PHY", name: "物理" },
  { code: "CHM", name: "化学" },
  { code: "BIO", name: "生物" },
  { code: "POL", name: "思想政治" },
  { code: "HIS", name: "历史" },
  { code: "GEO", name: "地理" },
] as const;

const LANGUAGES = [
  { code: "ENG", name: "英语" },
  { code: "JPN", name: "日语" },
  { code: "RUS", name: "俄语" },
  { code: "GER", name: "德语" },
  { code: "FRA", name: "法语" },
  { code: "SPA", name: "西班牙语" },
] as const;

/** 资格补充：只收集规则引擎真的会用到的字段，收无关身份信息没有意义。 */
const ATTRIBUTE_DEFS = [
  { key: "COLOR_VISION_NORMAL", label: "色觉正常（无色盲色弱）" },
  { key: "NO_COLOR_WEAKNESS", label: "无色弱" },
  { key: "HEIGHT_OK", label: "身高达标（航海/飞行等专业需满足章程要求）" },
  { key: "VISION_OK", label: "裸眼视力达标" },
  { key: "RURAL_SPECIAL_QUALIFIED", label: "具备高校专项计划报考资格" },
] as const;

const DRAFT_KEY = "sdgk.profile.draft.v1";

interface Draft {
  cycleId: number | null;
  displayName: string;
  score: string;
  rank: string;
  subjects: string[];
  language: string;
  attributes: Record<string, boolean>;
}

interface FieldErrors {
  rank?: string;
  score?: string;
  subjects?: string;
}

/** 位次解析：允许用户粘贴「60,000」这类带分隔的写法，但拒绝 0、负数、小数。 */
function parseRank(raw: string): { value: number | null; error?: string } {
  const cleaned = raw.replace(/[,，\s]/g, "");
  if (cleaned === "") return { value: null };
  if (!/^\d+$/.test(cleaned)) return { value: null, error: "位次必须是正整数（不要带小数点或其他字符）" };
  const value = Number(cleaned);
  if (value <= 0) return { value: null, error: "位次必须大于 0" };
  return { value };
}

function parseScore(raw: string): { value: number | null; error?: string } {
  const cleaned = raw.replace(/[,，\s]/g, "");
  if (cleaned === "") return { value: null };
  if (!/^\d+$/.test(cleaned)) return { value: null, error: "分数必须是整数" };
  const value = Number(cleaned);
  if (value < 0 || value > 750) return { value: null, error: "分数应在 0–750 之间" };
  return { value };
}

export default function ProfilePage() {
  const router = useRouter();
  const toast = useToast();
  const wb = useWorkbench();
  const queryClient = useQueryClient();
  const { state: flow, update, loaded } = useFlowState();

  const editing = Boolean(flow.profileId);
  const existing = wb.profile;

  const [cycleId, setCycleId] = useState<number | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [score, setScore] = useState("");
  const [rank, setRank] = useState("");
  const [subjects, setSubjects] = useState<string[]>([]);
  const [language, setLanguage] = useState("ENG");
  const [attributes, setAttributes] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<FieldErrors>({});
  const [convertNote, setConvertNote] = useState<string | null>(null);
  const [replaceWith, setReplaceWith] = useState<string | null>(null);
  const [draftRestored, setDraftRestored] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const hydrated = useRef(false);

  // ---- 初始化：优先用已保存的档案，其次用本机草稿 ----
  //
  // 必须等档案查询真的返回后再水合：之前只看 loaded，会在请求还没回来时
  // 就走进「没有档案」分支，把 hydrated 标记成 true，之后档案到了也不再回填，
  // 结果是编辑模式下表单全空、右侧摘要却有值。
  useEffect(() => {
    if (!loaded || hydrated.current) return;
    if (flow.profileId && !existing) return;

    if (existing) {
      setCycleId(existing.cycle_id);
      setDisplayName(existing.display_name ?? "");
      setScore(existing.score != null ? String(existing.score) : "");
      setRank(existing.rank != null ? String(existing.rank) : "");
      setSubjects([...existing.subjects]);
      setLanguage(existing.foreign_language ?? "ENG");
      setSavedAt(existing.updated_at ?? null);
      hydrated.current = true;
      return;
    }

    try {
      const raw = window.localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw) as Draft;
        if (draft && typeof draft === "object") {
          setCycleId(draft.cycleId ?? null);
          setDisplayName(draft.displayName ?? "");
          setScore(draft.score ?? "");
          setRank(draft.rank ?? "");
          setSubjects(Array.isArray(draft.subjects) ? draft.subjects : []);
          setLanguage(draft.language ?? "ENG");
          setAttributes(draft.attributes ?? {});
          setDraftRestored(true);
        }
      }
    } catch {
      // 草稿损坏就丢弃，不能让它阻断填写
    }
    hydrated.current = true;
  }, [loaded, existing, flow.profileId]);

  // 没有档案也没有草稿时，默认选中当前招生周期
  useEffect(() => {
    if (cycleId === null && wb.cycleId !== null) setCycleId(wb.cycleId);
  }, [cycleId, wb.cycleId]);

  const rankParsed = parseRank(rank);
  const scoreParsed = parseScore(score);

  const canComplete =
    Boolean(cycleId) &&
    rankParsed.value !== null &&
    !rankParsed.error &&
    !scoreParsed.error &&
    subjects.length === 3;

  // ---- 选科：满 3 科后再点，必须让用户明确选择替换哪一科 ----
  const toggleSubject = (code: string) => {
    if (subjects.includes(code)) {
      setSubjects(subjects.filter((s) => s !== code));
      setReplaceWith(null);
      setErrors((e) => ({ ...e, subjects: undefined }));
      return;
    }
    if (subjects.length < 3) {
      setSubjects([...subjects, code]);
      setErrors((e) => ({ ...e, subjects: undefined }));
      return;
    }
    setReplaceWith(code);
  };

  const confirmReplace = (drop: string) => {
    if (!replaceWith) return;
    setSubjects([...subjects.filter((s) => s !== drop), replaceWith]);
    setReplaceWith(null);
  };

  const convertRank = useMutation({
    mutationFn: () => catalogApi.scoreToRank(cycleId!, scoreParsed.value!),
    onSuccess: (result) => {
      setRank(String(result.rank));
      setConvertNote(
        result.source === "SEGMENT"
          ? `${result.exam_year} 年 ${result.score} 分对应累计位次 ${result.rank.toLocaleString("zh-CN")}（已填入位次框，可手动改回）`
          : (result.note ?? `按相邻分数换算：${result.rank}`),
      );
    },
    onError: (error) => setConvertNote(errorMessage(error)),
  });

  const saveDraft = () => {
    const draft: Draft = { cycleId, displayName, score, rank, subjects, language, attributes };
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      toast.show("草稿已保存到本机（尚未提交，其他页面读不到）", "warn");
    } catch {
      toast.show("浏览器拒绝写入本地存储，草稿未保存", "error");
    }
  };

  const save = useMutation({
    mutationFn: async () => {
      const attributesPayload = ATTRIBUTE_DEFS.filter((d) => attributes[d.key]).map((d) => ({
        attr_key: d.key,
        value_type: "BOOL" as const,
        value_bool: true,
        source: "SELF_REPORTED" as const,
      }));

      if (editing && existing) {
        const updated = await profileApi.update(existing.id, {
          rank: rankParsed.value,
          score: scoreParsed.value,
          subjects,
          foreign_language: language,
          display_name: displayName || null,
          expected_version: existing.version,
        });
        // 资格补充走单独的接口，逐个 upsert
        for (const attr of attributesPayload) {
          await profileApi.setAttribute(existing.id, attr);
        }
        return updated;
      }

      return profileApi.create({
        cycle_id: cycleId!,
        display_name: displayName || null,
        score: scoreParsed.value,
        rank: rankParsed.value,
        subjects,
        foreign_language: language,
        attributes: attributesPayload,
      });
    },
    onSuccess: async (saved) => {
      try {
        window.localStorage.removeItem(DRAFT_KEY);
      } catch {
        // ignore
      }
      update({
        profileId: saved.id,
        cycleId: saved.cycle_id,
        // 档案变了，下游结果就不可再当作当前有效
        ...(editing ? {} : { preferenceId: null, sessionId: null, schemeId: null }),
      });
      toast.show(editing ? "档案已更新，旧推荐与报告需重新生成" : "档案已保存", "ok");
      // 档案变了，工作台里缓存的档案/偏好/推荐状态全部失效
      await queryClient.invalidateQueries({ queryKey: ["wb"] });
      router.push("/onboarding/preferences");
    },
    onError: (error) => {
      setErrors((e) => ({ ...e, rank: errorMessage(error) }));
      toast.show(errorMessage(error), "error");
    },
  });

  const onComplete = () => {
    const next: FieldErrors = {};
    if (rankParsed.value === null) next.rank = rankParsed.error ?? "请填写全省位次";
    if (scoreParsed.error) next.score = scoreParsed.error;
    if (subjects.length !== 3) next.subjects = `需要恰好 3 门选考科目，当前 ${subjects.length} 门`;
    setErrors(next);
    if (Object.keys(next).length > 0) {
      const firstKey = Object.keys(next)[0];
      document.getElementById(`field-${firstKey}`)?.focus();
      return;
    }
    if (!canComplete) return;
    save.mutate();
  };

  const downstreamAffected = editing && (wb.session || wb.scheme);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 01 / PROFILE</div>
          <h1>成绩与选科</h1>
          <p className="page-sub">
            完善报考基础，后续推荐将使用已保存的档案。
          </p>
        </div>
        <div className="row gap8">
          <span className="pill">{editing ? "编辑已保存档案" : "尚未保存"}</span>
        </div>
      </div>

      {draftRestored ? (
        <div className="stale-banner" style={{ marginBottom: 18 }}>
          <Icon name="info" />
          <span>
            已恢复上次未提交的草稿。草稿只存在本机，<strong>不算完成本步骤</strong>，
            需要点「保存并设置偏好」才会真正入库。
          </span>
        </div>
      ) : null}

      {downstreamAffected ? (
        <div className="stale-banner" style={{ marginBottom: 18 }}>
          <Icon name="warn" />
          <span>
            修改位次或选科后，<strong>已生成的推荐与体检报告会标记为过期</strong>；
            已经编排的方案会保留，但需要重新推荐并复核。
          </span>
        </div>
      ) : null}

      <div className="form-layout">
        <div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onComplete();
            }}
          >
            <div className="card">
              {/* ---- 01 报考范围 ---- */}
              <section className="form-block">
                <div className="block-title">
                  <span className="section-number">01</span>
                  <h2>报考范围</h2>
                </div>
                <div className="fields">
                  <div className="field">
                    <label htmlFor="cycle">
                      招生年份 <span className="req">*</span>
                    </label>
                    <select
                      id="cycle"
                      className="select"
                      value={cycleId ?? ""}
                      disabled={editing}
                      onChange={(event) => setCycleId(Number(event.target.value))}
                    >
                      <option value={wb.cycleId ?? ""}>
                        {wb.cycleYear ? `${wb.cycleYear} 年` : "读取中…"}
                      </option>
                    </select>
                    <span className="field-hint">
                      {editing
                        ? "已保存的档案不能改年份；需要换年份请新建档案。"
                        : "只提供系统实际载入招生数据的年份。"}
                    </span>
                  </div>

                  <div className="field">
                    <label>高考省份</label>
                    <input className="input" value="山东省 · 夏季高考普通类" readOnly />
                    <span className="field-hint">本期只服务山东普通类，其他省份/类别未接入。</span>
                  </div>

                  <div className="field">
                    <label htmlFor="displayName">档案名称（可选）</label>
                    <input
                      id="displayName"
                      className="input"
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      placeholder="例如：小明"
                    />
                    <span className="field-hint">只用于区分多个档案，不影响推荐。</span>
                  </div>

                  <div className="field">
                    <label htmlFor="language">外语语种</label>
                    <select
                      id="language"
                      className="select"
                      value={language}
                      onChange={(event) => setLanguage(event.target.value)}
                    >
                      {LANGUAGES.map((item) => (
                        <option key={item.code} value={item.code}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                    <span className="field-hint">部分专业限英语，资格判断会用到。</span>
                  </div>
                </div>
              </section>

              {/* ---- 02 成绩与位次 ---- */}
              <section className="form-block">
                <div className="block-title">
                  <span className="section-number">02</span>
                  <h2>成绩与位次</h2>
                </div>
                <div className="fields">
                  <div className="field">
                    <label htmlFor="field-rank">
                      全省位次 <span className="req">*</span>
                    </label>
                    <input
                      id="field-rank"
                      className={`input num${errors.rank ? " is-error" : ""}`}
                      value={rank}
                      inputMode="numeric"
                      placeholder="例如 60,000"
                      onChange={(event) => {
                        setRank(event.target.value);
                        setErrors((e) => ({ ...e, rank: undefined }));
                      }}
                    />
                    <span className="field-hint">
                      填一分一段表里的累计人数。带千位分隔也可以。
                    </span>
                    {errors.rank ? (
                      <span className="field-error" role="alert">
                        {errors.rank}
                      </span>
                    ) : null}
                  </div>

                  <div className="field">
                    <label htmlFor="field-score">
                      高考分数 <span className="small muted">（可选）</span>
                    </label>
                    <input
                      id="field-score"
                      className={`input num${errors.score ? " is-error" : ""}`}
                      value={score}
                      inputMode="numeric"
                      placeholder="0–750"
                      onChange={(event) => {
                        setScore(event.target.value);
                        setErrors((e) => ({ ...e, score: undefined }));
                      }}
                    />
                    <span className="field-hint">
                      只做 0–750 格式检查；有匹配年份的一分一段表时才可用于换算位次。
                    </span>
                    {errors.score ? (
                      <span className="field-error" role="alert">
                        {errors.score}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      className="btn subtle mt8"
                      disabled={scoreParsed.value === null || convertRank.isPending || !cycleId}
                      onClick={() => convertRank.mutate()}
                    >
                      <Icon name="refresh" />
                      {convertRank.isPending ? "换算中…" : "用分数换算位次"}
                    </button>
                    {convertNote ? <span className="field-hint mt8">{convertNote}</span> : null}
                  </div>
                </div>
              </section>

              {/* ---- 03 选考科目 ---- */}
              <section className="form-block">
                <div className="block-title">
                  <span className="section-number">03</span>
                  <h2>选考科目</h2>
                  <span className="pill blue push">已选 {subjects.length} / 3</span>
                </div>
                <div className="subjects">
                  {SUBJECTS.map((item) => {
                    const selected = subjects.includes(item.code);
                    return (
                      <button
                        key={item.code}
                        type="button"
                        className={`subject${selected ? " selected" : ""}`}
                        aria-pressed={selected}
                        onClick={() => toggleSubject(item.code)}
                      >
                        <span>{item.name}</span>
                        <span className="pick-circle">{selected ? <Icon name="check" /> : null}</span>
                      </button>
                    );
                  })}
                </div>

                {replaceWith ? (
                  <div className="replace-prompt" role="alertdialog" aria-label="选择要替换的科目">
                    <p className="small">
                      已经选满 3 科。要把
                      <strong> {SUBJECTS.find((s) => s.code === replaceWith)?.name} </strong>
                      替换成哪一科？
                    </p>
                    <div className="row gap8 mt8">
                      {subjects.map((code) => (
                        <button
                          key={code}
                          type="button"
                          className="btn subtle"
                          onClick={() => confirmReplace(code)}
                        >
                          替换 {SUBJECTS.find((s) => s.code === code)?.name}
                        </button>
                      ))}
                      <button type="button" className="btn" onClick={() => setReplaceWith(null)}>
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="field-hint mt16">
                    恰好选择 3 科。选满后再点其他科目，会先让你确认替换哪一科，不会悄悄改动。
                  </p>
                )}

                {errors.subjects ? (
                  <span className="field-error mt8" role="alert">
                    {errors.subjects}
                  </span>
                ) : null}
              </section>

              {/* ---- 资格补充 ---- */}
              <section className="form-block">
                <div className="row gap8">
                  <Icon name="shield" />
                  <h3>资格补充</h3>
                  <span className="pill warn push">可选</span>
                </div>
                <p className="legal-mini mt8">
                  只收集规则引擎真的会用到的条件。不确定就留空 ——
                  <strong>未填写按「待确认」处理，不会被当成符合</strong>。
                </p>
                <div className="attribute-list mt16">
                  {ATTRIBUTE_DEFS.map((def) => (
                    <label key={def.key} className="attribute-row">
                      <input
                        type="checkbox"
                        checked={Boolean(attributes[def.key])}
                        onChange={(event) =>
                          setAttributes((prev) => ({ ...prev, [def.key]: event.target.checked }))
                        }
                      />
                      <span>{def.label}</span>
                    </label>
                  ))}
                </div>
              </section>
            </div>

            <div className="form-actions">
              <span className="small muted">
                <Icon name="info" />
                {savedAt
                  ? `上次保存：${new Date(savedAt).toLocaleString("zh-CN")}`
                  : "当前编辑尚未保存到服务器"}
              </span>
              <div className="row">
                <button type="button" className="btn" onClick={saveDraft}>
                  保存草稿
                </button>
                <button type="submit" className="btn primary" disabled={save.isPending}>
                  {save.isPending ? "保存中…" : "保存并设置偏好"}
                  <Icon name="arrow" />
                </button>
              </div>
            </div>
          </form>
        </div>

        {/* ---- 右侧摘要 ---- */}
        <aside className="stack summary-aside summary-sticky">
          <div className="card">
            <div className="card-head">
              <h2>当前档案预览</h2>
              <span className="pill blue">{wb.cycleYear ? `${wb.cycleYear} 年` : "—"}</span>
            </div>
            <div className="card-body">
              <small className="muted">全省位次</small>
              <div className="summary-number num">
                {rankParsed.value !== null ? rankParsed.value.toLocaleString("zh-CN") : "—"}
              </div>
              <div className="kv">
                <div className="kv-line">
                  <span>高考分数</span>
                  <span className="num">
                    {scoreParsed.value !== null ? scoreParsed.value : "尚未填写"}
                  </span>
                </div>
                <div className="kv-line">
                  <span>选考科目</span>
                  <span>{subjects.length > 0 ? subjectList(subjects) : "未选择"}</span>
                </div>
                <div className="kv-line">
                  <span>外语语种</span>
                  <span>{LANGUAGES.find((l) => l.code === language)?.name ?? "英语"}</span>
                </div>
                <div className="kv-line">
                  <span>档案状态</span>
                  <span>{editing ? `已保存 v${existing?.version ?? "?"}` : "未保存"}</span>
                </div>
              </div>
              <div className="chips mt16">
                {subjects.length === 0 ? <span className="summary-tag">未选科目</span> : null}
                {subjects.map((code) => (
                  <span className="summary-tag" key={code}>
                    {SUBJECTS.find((s) => s.code === code)?.name}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-body">
              <h3>
                <Icon name="info" /> 填写提示
              </h3>
              <p className="legal-mini mt16">
                位次与分数以实际成绩信息为准。没有匹配年份的一分一段表时，系统只做格式校验，
                不会编造「范围通过」的结论。
              </p>
              <p className="legal-mini mt8">
                修改已保存的档案后，原方案会保留，但旧推荐与体检报告需要重新生成。
              </p>
              <p className="legal-mini mt8">
                选满 3 科不等于所有专业都符合 —— 每个专业还有自己的选科要求，由资格规则单独判断。
              </p>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
