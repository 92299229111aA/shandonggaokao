/**
 * 工作台共享状态：六页共用一份派生数据。
 *
 * 为什么要有这一层：六页都要回答"我现在到哪一步、哪些结果过期了、下一步做什么"。
 * 如果每页各自判断，就会出现主页说"已完成"、体检页说"需更新"这种自相矛盾。
 * 所以所有派生逻辑集中在这里，页面只消费结果。
 *
 * 过期判断不改后端（后端没有 is_stale 字段），而是比对已有的版本号：
 *   推荐过期  ⟸ session.profile_version / preference_version 落后于当前档案 / 偏好
 *   报告过期  ⟸ check.version_no 落后于方案当前版本
 * 后端已经把这些版本号都返回了，所以判断是准确的、不是猜的。
 */

import { useQuery } from "@tanstack/react-query";

import type { HealthCheck, PreferenceProfile, ProfileDetail, RecommendationRun, Scheme } from "@/lib/api/types";
import { healthApi, metaApi, profileApi, recommendationApi, schemeApi } from "@/lib/api/endpoints";
import { useFlowState } from "@/lib/flow-state";

export type StepKey = "profile" | "preferences" | "recommendations" | "editor" | "checkup";

/** 文档要求把"未开始 / 草稿 / 完成 / 需更新"分清楚，不能只有一个布尔。 */
export type StepStatus = "empty" | "draft" | "complete" | "stale";

export interface StepState {
  key: StepKey;
  label: string;
  status: StepStatus;
  /** 侧栏显示的短状态：✓ / 草稿 / 需更新 / 编号 */
  badge: string;
  /** 给主页进度条用的一句话说明 */
  note: string;
}

export interface WorkbenchState {
  loaded: boolean;
  loading: boolean;
  error: Error | null;

  cycleId: number | null;
  cycleYear: number | null;
  applicationLimit: number | null;

  profile: ProfileDetail | null;
  preference: PreferenceProfile | null;
  session: RecommendationRun["session"] | null;
  scheme: Scheme | null;
  check: HealthCheck | null;

  recommendationStale: boolean;
  checkupStale: boolean;

  steps: StepState[];
  todos: Todo[];
  /** 文档 §4.3：主页主按钮随真实状态变化，不能写死 */
  primaryAction: { label: string; href: string };
}

export interface Todo {
  key: string;
  icon: "info" | "warn" | "clock" | "shield" | "edit" | "refresh";
  title: string;
  detail: string;
  actionLabel: string;
  href: string;
  severity: "normal" | "warn";
}

const STEP_LABELS: Record<StepKey, string> = {
  profile: "成绩与选科",
  preferences: "偏好画像",
  recommendations: "推荐结果",
  editor: "志愿编辑器",
  checkup: "志愿体检",
};

const STEP_ORDER: StepKey[] = ["profile", "preferences", "recommendations", "editor", "checkup"];

/** 档案是否达到"可用于推荐"的程度。用后端 status 与必填字段共同判断。 */
function profileComplete(profile: ProfileDetail | null): boolean {
  if (!profile) return false;
  if (profile.status === "DRAFT") return false;
  if (!profile.rank || profile.rank <= 0) return false;
  return Array.isArray(profile.subjects) && profile.subjects.length === 3;
}

export function deriveWorkbench(input: {
  profile: ProfileDetail | null;
  preference: PreferenceProfile | null;
  session: RecommendationRun["session"] | null;
  scheme: Scheme | null;
  check: HealthCheck | null;
  applicationLimit: number | null;
}): Omit<WorkbenchState, "loaded" | "loading" | "error" | "cycleId" | "cycleYear"> {
  const { profile, preference, session, scheme, check, applicationLimit } = input;

  const profileOk = profileComplete(profile);

  // 推荐是否过期：会话记录的输入版本 vs 当前版本
  const recommendationStale = Boolean(
    session &&
      profile &&
      (session.profile_version !== profile.version ||
        (preference && session.preference_version !== preference.version)),
  );

  // 报告是否过期：报告检查的方案版本 vs 方案当前版本
  const checkupStale = Boolean(
    check && scheme && check.version_no !== null && check.version_no !== scheme.current_version_no,
  );

  const schemeHasItems = Boolean(scheme && scheme.item_count > 0);

  const statuses: Record<StepKey, StepStatus> = {
    profile: !profile ? "empty" : profileOk ? "complete" : "draft",
    preferences: !preference ? "empty" : profileOk ? "complete" : "draft",
    recommendations: !session ? "empty" : recommendationStale ? "stale" : "complete",
    editor: !schemeHasItems ? "empty" : "complete",
    checkup: !check ? "empty" : checkupStale ? "stale" : "complete",
  };

  const notes: Record<StepKey, string> = {
    profile: statuses.profile === "complete" ? "已保存" : statuses.profile === "draft" ? "待完善" : "未填写",
    preferences:
      statuses.preferences === "complete" ? "已保存" : statuses.preferences === "draft" ? "待完善" : "未设置",
    recommendations:
      statuses.recommendations === "complete"
        ? "可查看"
        : statuses.recommendations === "stale"
          ? "需更新"
          : "未生成",
    editor: schemeHasItems ? "编排中" : "未开始",
    checkup:
      statuses.checkup === "complete" ? "已检查" : statuses.checkup === "stale" ? "需重检" : "尚未检查",
  };

  // 侧栏状态标记：文档要求完成标记来自真实状态，不能写死 ✓
  const badges: Record<StepKey, string> = {
    profile: statuses.profile === "complete" ? "✓" : statuses.profile === "draft" ? "草稿" : "01",
    preferences: statuses.preferences === "complete" ? "✓" : statuses.preferences === "draft" ? "草稿" : "02",
    recommendations:
      statuses.recommendations === "complete" ? "✓" : statuses.recommendations === "stale" ? "需更新" : "03",
    editor: statuses.editor === "complete" ? "✓" : "04",
    checkup: statuses.checkup === "complete" ? "✓" : statuses.checkup === "stale" ? "需重检" : "05",
  };

  const steps: StepState[] = STEP_ORDER.map((key) => ({
    key,
    label: STEP_LABELS[key],
    status: statuses[key],
    badge: badges[key],
    note: notes[key],
  }));

  // ---- 待办 ----
  const todos: Todo[] = [];
  if (!profile) {
    todos.push({
      key: "no-profile",
      icon: "edit",
      title: "先填写成绩与选科",
      detail: "位次与选科是资格判断和推荐的基础，缺了后面都跑不起来。",
      actionLabel: "去填写",
      href: "/onboarding/score",
      severity: "normal",
    });
  } else if (!profileOk) {
    todos.push({
      key: "profile-incomplete",
      icon: "warn",
      title: "档案还没填完",
      detail: "需要位次与恰好 3 门选考科目，才能进入推荐。",
      actionLabel: "继续完善",
      href: "/onboarding/score",
      severity: "warn",
    });
  }
  if (profileOk && !preference) {
    todos.push({
      key: "no-preference",
      icon: "info",
      title: "设置偏好画像",
      detail: "全部不限也可以保存，但需要显式保存一次才会用于推荐。",
      actionLabel: "去设置",
      href: "/onboarding/preferences",
      severity: "normal",
    });
  }
  if (recommendationStale) {
    todos.push({
      key: "reco-stale",
      icon: "refresh",
      title: "推荐结果已过期",
      detail: "档案或偏好改过，当前推荐依据的是旧版本，需要重新生成。",
      actionLabel: "更新推荐",
      href: "/recommendations",
      severity: "warn",
    });
  }
  if (schemeHasItems && !check) {
    todos.push({
      key: "no-check",
      icon: "shield",
      title: "方案还没体检",
      detail: "可以检查结构、容量、重复项与已实现的资格规则。",
      actionLabel: "去体检",
      href: "/health-check",
      severity: "normal",
    });
  }
  if (checkupStale) {
    todos.push({
      key: "check-stale",
      icon: "clock",
      title: "体检报告已过期",
      detail: `报告对应的是 v${check?.version_no ?? "?"}，当前方案已是 v${scheme?.current_version_no ?? "?"}。`,
      actionLabel: "重新检查",
      href: "/health-check",
      severity: "warn",
    });
  }

  // ---- 主按钮（文档 §4.3 的判定顺序：先补基础输入，再处理下游）----
  let primaryAction = { label: "开始填写成绩与选科", href: "/onboarding/score" };
  if (!profile) {
    primaryAction = { label: "开始填写成绩与选科", href: "/onboarding/score" };
  } else if (!profileOk) {
    primaryAction = { label: "继续完善档案", href: "/onboarding/score" };
  } else if (!preference) {
    primaryAction = { label: "设置偏好画像", href: "/onboarding/preferences" };
  } else if (!session || recommendationStale) {
    primaryAction = {
      label: recommendationStale ? "更新推荐结果" : "查看推荐结果",
      href: "/recommendations",
    };
  } else if (!schemeHasItems) {
    primaryAction = { label: "开始编排志愿", href: "/recommendations" };
  } else if (!check) {
    primaryAction = { label: "检查当前方案", href: "/health-check" };
  } else if (checkupStale) {
    primaryAction = { label: "重新体检", href: "/health-check" };
  } else {
    primaryAction = { label: "查看方案与报告", href: "/health-check" };
  }

  return {
    profile,
    preference,
    session,
    scheme,
    check,
    recommendationStale,
    checkupStale,
    steps,
    todos,
    primaryAction,
    applicationLimit,
  };
}

/**
 * 读取六页共用的数据。全部走真实接口，没有演示分支。
 *
 * 用 useQuery 串起来：后一环依赖前一环的 id，缺 id 时自动跳过，
 * 所以未开始的流程不会发出无意义的请求。
 */
export function useWorkbench(): WorkbenchState {
  const { state: flow, loaded } = useFlowState();

  const cycles = useQuery({
    queryKey: ["wb", "cycles"],
    queryFn: () => metaApi.cycles(),
    enabled: loaded,
  });

  const activeCycle = cycles.data
    ? ([...cycles.data].filter((c) => c.is_active).sort((a, b) => b.exam_year - a.exam_year)[0] ??
      cycles.data[0] ??
      null)
    : null;

  const profileId = flow.profileId;

  const profile = useQuery({
    queryKey: ["wb", "profile", profileId],
    queryFn: () => profileApi.get(profileId!),
    enabled: loaded && Boolean(profileId),
  });

  // 用列表接口而不是 /latest：新档案还没有偏好时 /latest 返回 404，
  // 会在每次打开页面时留下一条控制台错误，且容易被误读成"接口挂了"。
  // 列表接口返回空数组，语义更准确。
  const preferenceList = useQuery({
    queryKey: ["wb", "preference", profileId],
    queryFn: () => profileApi.preferences(profileId!),
    enabled: loaded && Boolean(profileId),
  });

  const preference = {
    ...preferenceList,
    data:
      preferenceList.data && preferenceList.data.length > 0
        ? [...preferenceList.data].sort((a, b) => b.version - a.version)[0]
        : null,
  };

  const session = useQuery({
    queryKey: ["wb", "session", flow.sessionId],
    queryFn: () => recommendationApi.session(flow.sessionId!),
    enabled: loaded && Boolean(flow.sessionId),
  });

  const scheme = useQuery({
    queryKey: ["wb", "scheme", flow.schemeId],
    queryFn: () => schemeApi.get(flow.schemeId!),
    enabled: loaded && Boolean(flow.schemeId),
  });

  const checks = useQuery({
    queryKey: ["wb", "checks", flow.schemeId],
    queryFn: () => healthApi.list(flow.schemeId!),
    enabled: loaded && Boolean(flow.schemeId),
  });

  const latestCheck = checks.data && checks.data.length > 0 ? checks.data[0] : null;

  const derived = deriveWorkbench({
    profile: profile.data ?? null,
    preference: preference.data ?? null,
    session: session.data ?? null,
    scheme: scheme.data ?? null,
    check: latestCheck,
    applicationLimit: activeCycle?.application_limit ?? null,
  });

  const error =
    (cycles.error as Error | null) ??
    (profile.error as Error | null) ??
    (session.error as Error | null) ??
    (scheme.error as Error | null) ??
    null;

  const loading =
    !loaded ||
    cycles.isPending ||
    (Boolean(profileId) && profile.isPending) ||
    (Boolean(flow.sessionId) && session.isPending) ||
    (Boolean(flow.schemeId) && scheme.isPending);

  return {
    loaded,
    loading,
    error,
    cycleId: activeCycle?.id ?? null,
    cycleYear: activeCycle?.exam_year ?? null,
    ...derived,
  };
}

/** 供页面查询时复用的 queryKey 片段，避免各页自己拼出不一致的缓存键。 */
export const workbenchKeys = {
  cycles: ["wb", "cycles"] as const,
  profile: (id: number | null) => ["wb", "profile", id] as const,
  preference: (id: number | null) => ["wb", "preference", id] as const,
  scheme: (id: number | null) => ["wb", "scheme", id] as const,
};

