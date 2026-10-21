import type { Metadata } from "next";

import { Providers } from "@/components/providers";

import "./globals.css";
import "./workbench.css";

export const metadata: Metadata = {
  title: "山东高考智能志愿｜专业工作台",
  description:
    "面向山东夏季高考普通类考生的志愿决策工作台：资格过滤、录取安全指数、偏好建模与 96 志愿组合优化。",
};

/**
 * 根布局只负责字体、样式与全局 Provider。
 *
 * 外壳（顶栏 + 侧边栏 + 主区）放在 (workbench) 分组里，因为六页共用它，
 * 而根布局还要能容纳未来不属于工作台的页面（例如独立的打印视图）。
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
