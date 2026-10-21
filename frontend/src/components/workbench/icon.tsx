/**
 * 工作台图标集。
 *
 * 路径取自已确认的界面设计参考稿，保持同一套线性图标（24×24、currentColor 描边），
 * 不引入图标库 —— 25 个图标不值得增加一个依赖，也不该为此改变视觉风格。
 */

import type { CSSProperties, ReactNode } from "react";

export type IconName =
  | "home"
  | "profile"
  | "prefs"
  | "search"
  | "edit"
  | "shield"
  | "arrow"
  | "chevron"
  | "check"
  | "download"
  | "info"
  | "clock"
  | "pin"
  | "school"
  | "plus"
  | "close"
  | "up"
  | "down"
  | "lock"
  | "unlock"
  | "refresh"
  | "settings"
  | "warn"
  | "chart"
  | "file";

const PATHS: Record<IconName, ReactNode> = {
  home: (
    <>
      <path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" />
      <path d="M9 21v-8h6v8" />
    </>
  ),
  profile: (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4V2h6v2M9 10h6M9 14h6M9 18h3" />
    </>
  ),
  prefs: (
    <>
      <path d="M4 6h16M4 12h16M4 18h16" />
      <circle cx="8" cy="6" r="2" fill="white" />
      <circle cx="16" cy="12" r="2" fill="white" />
      <circle cx="10" cy="18" r="2" fill="white" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  edit: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </>
  ),
  shield: (
    <>
      <path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6Z" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
  arrow: <path d="M4 12h15m-5-5 5 5-5 5" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  check: <path d="m5 12 4 4L19 6" />,
  download: <path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7h.01" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l4 2" />
    </>
  ),
  pin: (
    <>
      <path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  school: <path d="m3 9 9-6 9 6M5 10v10h14V10M9 20v-6h6v6M9 9h.01M15 9h.01" />,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  up: <path d="m6 14 6-6 6 6" />,
  down: <path d="m6 10 6 6 6-6" />,
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8-1M12 14v3" />
    </>
  ),
  refresh: <path d="M20 7a9 9 0 1 0 1 8M20 2v6h-6" />,
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="3" fill="white" />
      <circle cx="15" cy="17" r="3" fill="white" />
    </>
  ),
  warn: (
    <>
      <path d="m12 3 10 18H2Z" />
      <path d="M12 9v5M12 17h.01" />
    </>
  ),
  chart: <path d="M4 3v18h17M8 17v-5M13 17V8M18 17V5" />,
  file: <path d="M14 2H5v20h14V7Zm0 0v5h5M8 12h8M8 16h6" />,
};

export function Icon({
  name,
  size,
  className,
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      className={`ico ${className ?? ""}`.trim()}
      viewBox="0 0 24 24"
      aria-hidden="true"
      style={size ? { width: size, height: size, ...style } : style}
    >
      {PATHS[name]}
    </svg>
  );
}
