"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";

/**
 * 客户端挂载门。
 *
 * 桌面版把前端导出成静态 HTML，而这套界面完全由客户端状态驱动
 * （档案、方案存在 localStorage 与后端里）。构建时渲染出的 HTML 与用户真机
 * 打开时的首帧必然不同 —— 静态导出下这会触发 React hydration 不匹配
 * （生产环境报 #418，整棵树被丢弃重渲染）。
 *
 * 先渲染骨架、挂载后再渲染真实界面，两边首帧就始终一致。
 * 代价只是启动多一帧骨架，而本来就要显示加载态。
 */
function MountGate({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <div className="boot-splash" aria-busy="true" aria-live="polite">
        <span className="boot-mark" aria-hidden="true" />
        <p>正在启动…</p>
      </div>
    );
  }
  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              // A missing backend or a version conflict will not fix itself by retrying.
              if (error instanceof ApiError && (error.status === 0 || error.status === 409)) return false;
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <MountGate>{children}</MountGate>
    </QueryClientProvider>
  );
}
