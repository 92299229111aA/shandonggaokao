import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 桌面版把前端导出成纯静态文件，由 FastAPI 一起提供（只有一个后端进程）。
  // 开发/服务器模式仍用 standalone。
  output: process.env.DESKTOP_BUILD === "1" ? "export" : "standalone",
  // 静态导出按目录出 index.html，刷新 /schemes 这类路由才不会 404
  trailingSlash: process.env.DESKTOP_BUILD === "1",
  // Next 15's built-in ESLint runner still passes legacy `useEslintrc`/`extensions`
  // options that ESLint 9 rejects together with a flat config, so linting runs as
  // its own step (`pnpm lint` -> `eslint src`), which CI and the README both call.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
