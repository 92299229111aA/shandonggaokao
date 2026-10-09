import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Next 15's built-in ESLint runner still passes legacy `useEslintrc`/`extensions`
  // options that ESLint 9 rejects together with a flat config, so linting runs as
  // its own step (`pnpm lint` -> `eslint src`), which CI and the README both call.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
