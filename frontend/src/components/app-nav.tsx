"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** The six-step V1 flow. Step numbers make the linear journey obvious. */
export const FLOW_STEPS = [
  { href: "/", label: "首页", step: 0 },
  { href: "/onboarding/score", label: "成绩与选科", step: 1 },
  { href: "/onboarding/preferences", label: "偏好画像", step: 2 },
  { href: "/recommendations", label: "推荐结果", step: 3 },
  { href: "/schemes", label: "志愿编辑器", step: 4 },
  { href: "/health-check", label: "志愿体检", step: 5 },
] as const;

export function AppNav() {
  const pathname = usePathname();

  return (
    <header className="border-b bg-card">
      <div className="container flex h-14 items-center gap-6">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded bg-primary text-xs text-primary-foreground">
            鲁
          </span>
          山东高考智能志愿
        </Link>
        <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
          {FLOW_STEPS.filter((step) => step.step > 0).map((step) => {
            const active = pathname === step.href || pathname.startsWith(`${step.href}/`);
            return (
              <Link
                key={step.href}
                href={step.href}
                className={cn(
                  "whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition-colors",
                  active ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground hover:bg-accent/60",
                )}
              >
                <span className="mr-1 text-xs opacity-60">{step.step}</span>
                {step.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
