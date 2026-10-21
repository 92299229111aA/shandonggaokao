/**
 * 风险层与资格的统一呈现。
 *
 * 文档反复强调：风险、资格、置信度是三件事，不能互相替代，也不能只用颜色表达。
 * 所以这里只给"中文标签 + 字母类名"，不给概率、百分比或进度条。
 */

import { RISK_LABELS, RISK_ORDER } from "@/lib/utils";

/** 风险层 → 参考稿的 r0..r4 类名（高冲→保） */
export function riskClass(bucket: string | null | undefined): string {
  const index = bucket ? RISK_ORDER.indexOf(bucket as (typeof RISK_ORDER)[number]) : -1;
  return index >= 0 ? `r${index}` : "r_unknown";
}

export function riskLabel(bucket: string | null | undefined): string {
  if (!bucket) return "未分层";
  return RISK_LABELS[bucket] ?? "未分层";
}

/** 五档的完整中文名，用于图例与筛选标签 */
export const RISK_FULL_LABELS: Record<string, string> = {
  HIGH_REACH: "高冲",
  REACH: "冲",
  BORDER: "边界",
  STABLE: "稳",
  SAFE: "保",
};

export const ELIGIBILITY_TEXT: Record<string, string> = {
  PASS: "资格通过",
  FAIL: "资格不符",
  UNKNOWN: "待核验",
};

/** 资格状态落到与风险层不同的色系上：绿/红/金，但用 .tristate 而不是 .risk */
export function eligibilityClass(value: string | null | undefined): string {
  if (value === "PASS") return "pass";
  if (value === "FAIL") return "fail";
  return "unknown";
}

export function riskModeLabel(mode: string | null | undefined): string {
  if (mode === "STEADY") return "稳妥型";
  if (mode === "AGGRESSIVE") return "激进型";
  if (mode === "BALANCED") return "均衡型";
  return "未设置";
}

/** 金额展示：缺失就是缺失，绝不当 0 */
export function formatFee(value: number | null | undefined): string {
  if (value === null || value === undefined) return "费用待核验";
  if (value === 0) return "未公开";
  return `${value.toLocaleString("zh-CN")} 元/年`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

/** ACS 只保留一位小数，不加百分号 —— 它不是概率 */
export function formatAcs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(1);
}

/** 构成条用的实色类名（b0..b4），与徽标的 r0..r4 区分开 */
export function riskBarClass(bucket: string | null | undefined): string {
  const index = bucket ? RISK_ORDER.indexOf(bucket as (typeof RISK_ORDER)[number]) : -1;
  return index >= 0 ? `b${index}` : "b_unknown";
}

/** 选考科目代码 → 中文。后端存的是 PHY/CHM 这类代码，界面要给家长看中文。 */
export const SUBJECT_LABELS: Record<string, string> = {
  PHY: "物理",
  CHM: "化学",
  BIO: "生物",
  POL: "思想政治",
  HIS: "历史",
  GEO: "地理",
};

export function subjectLabel(code: string): string {
  return SUBJECT_LABELS[code] ?? code;
}

export function subjectList(codes: readonly string[] | null | undefined): string {
  if (!codes || codes.length === 0) return "未填写";
  return codes.map(subjectLabel).join(" / ");
}

