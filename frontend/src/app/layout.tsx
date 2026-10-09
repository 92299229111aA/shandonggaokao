import type { Metadata } from "next";

import { AppNav } from "@/components/app-nav";
import { Providers } from "@/components/providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "山东高考智能志愿系统 V1",
  description:
    "面向山东夏季高考普通类考生的志愿决策系统：资格过滤、录取安全指数、偏好建模与 96 志愿组合优化。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <Providers>
          <AppNav />
          <main className="container py-6">{children}</main>
          <footer className="container border-t py-6 text-xs text-muted-foreground">
            <p>
              本系统输出的是决策辅助方案，不是录取保证。ACS（录取安全指数）反映历史数据下的录取安全程度，
              不是录取概率。最终请以山东省教育招生考试院官方系统为准。
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
