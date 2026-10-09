"use client";

import { RISK_CLASSES, RISK_LABELS, CONFIDENCE_LABELS, cn } from "@/lib/utils";

export function RiskBadge({ bucket, className }: { bucket: string | null; className?: string }) {
  const key = bucket ?? "UNKNOWN";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        RISK_CLASSES[key] ?? RISK_CLASSES.UNKNOWN,
        className,
      )}
    >
      {RISK_LABELS[key] ?? key}
    </span>
  );
}

export function ConfidenceBadge({ label, value }: { label: string | null; value: number | null }) {
  const key = label ?? "LOW";
  const tone =
    key === "HIGH"
      ? "border-[hsl(var(--risk-safe))] text-[hsl(var(--risk-safe))]"
      : key === "MEDIUM"
        ? "border-[hsl(var(--risk-border))] text-amber-700"
        : "border-destructive text-destructive";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs", tone)}>
      可信度 {CONFIDENCE_LABELS[key] ?? key}
      {value !== null ? ` (${value.toFixed(0)})` : ""}
    </span>
  );
}

export function EligibilityBadge({ eligibility }: { eligibility: string }) {
  const map: Record<string, { text: string; className: string }> = {
    PASS: { text: "符合报考条件", className: "border-transparent bg-[hsl(var(--risk-safe))] text-white" },
    UNKNOWN: { text: "待确认要求", className: "border-transparent bg-[hsl(var(--risk-border))] text-slate-900" },
    FAIL: { text: "不符合报考条件", className: "border-transparent bg-destructive text-destructive-foreground" },
  };
  const item = map[eligibility] ?? { text: eligibility, className: "border-input" };
  return (
    <span className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs font-medium", item.className)}>
      {item.text}
    </span>
  );
}

/**
 * ACS display. Deliberately labelled 安全指数 and paired with the disclaimer so
 * no screen can imply a calibrated admission probability.
 */
export function AcsMeter({ acs, className }: { acs: number | null; className?: string }) {
  if (acs === null) {
    return <span className={cn("text-sm text-muted-foreground", className)}>ACS 无数据</span>;
  }
  const clamped = Math.max(0, Math.min(100, acs));
  const tone =
    clamped < 30
      ? "hsl(var(--risk-high-reach))"
      : clamped < 45
        ? "hsl(var(--risk-reach))"
        : clamped < 60
          ? "hsl(var(--risk-border))"
          : clamped < 80
            ? "hsl(var(--risk-stable))"
            : "hsl(var(--risk-safe))";
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-baseline gap-1">
        <span className="text-lg font-semibold tabular-nums">{clamped.toFixed(1)}</span>
        <span className="text-xs text-muted-foreground">/100 安全指数</span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="录取安全指数"
      >
        <div className="h-full rounded-full" style={{ width: `${clamped}%`, backgroundColor: tone }} />
      </div>
    </div>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, string> = {
    ERROR: "border-transparent bg-destructive text-destructive-foreground",
    WARNING: "border-transparent bg-[hsl(var(--risk-border))] text-slate-900",
    INFO: "border-input text-muted-foreground",
  };
  const text: Record<string, string> = { ERROR: "必须处理", WARNING: "建议处理", INFO: "提示" };
  return (
    <span className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs font-medium", map[severity])}>
      {text[severity] ?? severity}
    </span>
  );
}
