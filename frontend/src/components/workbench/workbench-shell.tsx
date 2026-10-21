"use client";

/**
 * 工作台外壳：顶栏 + 左侧流程导航 + 主区。
 *
 * 六页共用这一个壳。导航的完成状态来自 `useWorkbench` 的真实数据（档案版本、推荐版本、
 * 方案版本、体检版本），不是模板里写死的勾 —— 这是文档反复强调的一条。
 *
 * 页面用 Next.js 路由切换，不自己做 view 切换：这样深链接、前进后退、刷新都能用，
 * 也不会出现"两套导航状态"。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Icon } from "./icon";
import type { IconName } from "./icon";
import { ToastProvider } from "./toast";
import { useWorkbench } from "@/lib/workbench-state";

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  /** 业务步骤编号；主页不计入 01–05 */
  step?: "01" | "02" | "03" | "04" | "05";
  stepKey?: "profile" | "preferences" | "recommendations" | "editor" | "checkup";
}

const HOME: NavItem = { href: "/", label: "主页", icon: "home" };

const STEPS: NavItem[] = [
  { href: "/onboarding/score", label: "成绩与选科", icon: "profile", step: "01", stepKey: "profile" },
  { href: "/onboarding/preferences", label: "偏好画像", icon: "prefs", step: "02", stepKey: "preferences" },
  { href: "/recommendations", label: "推荐结果", icon: "search", step: "03", stepKey: "recommendations" },
  { href: "/schemes", label: "志愿编辑器", icon: "edit", step: "04", stepKey: "editor" },
  { href: "/health-check", label: "志愿体检", icon: "shield", step: "05", stepKey: "checkup" },
];

const PAGE_TITLES: Record<string, string> = {
  "/": "主页",
  "/onboarding/score": "成绩与选科",
  "/onboarding/preferences": "偏好画像",
  "/recommendations": "推荐结果",
  "/schemes": "志愿编辑器",
  "/health-check": "志愿体检",
};

function saveStateLabel(params: {
  hasProfile: boolean;
  hasPreference: boolean;
}): string {
  if (!params.hasProfile) return "尚未保存档案";
  if (!params.hasPreference) return "档案已保存，偏好未设置";
  return "本地已保存";
}

export function WorkbenchShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const wb = useWorkbench();

  const stepState = new Map(
    wb.steps.map((step) => [step.key, { badge: step.badge, status: step.status }]),
  );

  return (
    <ToastProvider>
      <>
        <aside className="sidebar" aria-label="主导航">
          <Link className="brand" href="/">
            <span className="brand-mark" aria-hidden="true" />
            <span>
              <span className="brand-name">山东高考智能志愿</span>
              <br />
              <small>SHANDONG · NEXT STEP</small>
            </span>
          </Link>

          <nav className="nav">
            <Link
              href={HOME.href}
              className={pathname === HOME.href ? "active" : ""}
              aria-current={pathname === HOME.href ? "page" : undefined}
            >
              <Icon name={HOME.icon} />
              <span>{HOME.label}</span>
            </Link>
          </nav>

          <div className="nav-divider" />
          <div className="nav-label">我的填报流程</div>

          <nav className="nav">
            {STEPS.map((item) => {
              const info = item.stepKey ? stepState.get(item.stepKey) : undefined;
              const active = pathname === item.href;
              const badge = info?.badge ?? item.step;
              const done = info?.status === "complete";
              const stale = info?.status === "stale";
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "active" : ""}
                  aria-current={active ? "page" : undefined}
                  title={done ? "已完成" : stale ? "需更新" : undefined}
                >
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                  <span
                    className={`step-state${done ? " is-done" : ""}${stale ? " is-stale" : ""}`}
                  >
                    {badge}
                  </span>
                </Link>
              );
            })}
          </nav>

          <div className="side-bottom">
            <div className="side-summary">
              <div className="row gap8 small" style={{ color: "#627b9f" }}>
                <Icon name="info" />
                <span>工作台</span>
              </div>
              <p className="small muted mt8">
                推荐依据的是已保存的档案与偏好；改过之后旧结果会标记为需更新。
              </p>
            </div>
            <div className="side-profile">
              <span className="avatar">SD</span>
              <div>
                <div className="small">山东 · 普通类</div>
                <div className="small muted">
                  {wb.cycleYear ? `${wb.cycleYear} 年` : "—"}
                  {wb.profile?.rank ? ` · 位次 ${wb.profile.rank.toLocaleString()}` : ""}
                </div>
              </div>
            </div>
          </div>
        </aside>

        <div className="stage">
          <header className="topbar">
            <div className="row">
              <div className="crumb">
                <span>志愿工作台</span>
                <span className="crumb-sep">/</span>
                <span className="crumb-current">{PAGE_TITLES[pathname] ?? "工作台"}</span>
              </div>
            </div>
            <div className="top-tools">
              <span className="small muted save-inline">
                {wb.loading
                  ? "正在读取…"
                  : saveStateLabel({
                      hasProfile: Boolean(wb.profile),
                      hasPreference: Boolean(wb.preference),
                    })}
              </span>
              <span className="pill year-tag">
                {wb.cycleYear ? `${wb.cycleYear} 招生年` : "未选定年份"}
              </span>
              <span className="pill blue">真实数据</span>
            </div>
          </header>

          <main className="content">{children}</main>

          <footer className="footer-note">
            <span>山东高考智能志愿 · 专业工作台</span>
            <span className="muted">
              ACS 是录取安全指数，不是录取概率；最终以省考试院官方发布为准。
            </span>
          </footer>
        </div>
      </>
    </ToastProvider>
  );
}
