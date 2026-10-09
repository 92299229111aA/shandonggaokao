import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Risk bucket -> display label. Kept in one place so no page invents its own. */
export const RISK_LABELS: Record<string, string> = {
  HIGH_REACH: "高冲",
  REACH: "冲",
  BORDER: "边界",
  STABLE: "稳",
  SAFE: "保",
  UNKNOWN: "未知",
};

export const RISK_ORDER = ["HIGH_REACH", "REACH", "BORDER", "STABLE", "SAFE"] as const;

export const RISK_CLASSES: Record<string, string> = {
  HIGH_REACH: "bg-[hsl(var(--risk-high-reach))] text-white",
  REACH: "bg-[hsl(var(--risk-reach))] text-white",
  BORDER: "bg-[hsl(var(--risk-border))] text-slate-900",
  STABLE: "bg-[hsl(var(--risk-stable))] text-white",
  SAFE: "bg-[hsl(var(--risk-safe))] text-white",
  UNKNOWN: "bg-muted text-muted-foreground",
};

export const CONFIDENCE_LABELS: Record<string, string> = {
  HIGH: "高",
  MEDIUM: "中",
  LOW: "低",
};

export const ELIGIBILITY_LABELS: Record<string, string> = {
  PASS: "符合报考条件",
  FAIL: "不符合报考条件",
  UNKNOWN: "存在待确认要求",
};

/** ACS is a safety index, never a probability. Every UI surface uses this text. */
export const ACS_DISCLAIMER =
  "ACS（录取安全指数）反映历史数据下的录取安全程度，范围 0~100，不是录取概率。";

export function formatRank(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}
