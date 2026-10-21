"use client";

/**
 * 六页共用的流程状态（档案 / 偏好 / 推荐 / 方案 的 id）。
 *
 * 为什么不是普通 useState：外壳（侧边栏、顶栏）只挂载一次，不会随导航重建；
 * 如果每个组件各持一份 state，外壳就永远看不到页面里刚写入的 profileId，
 * 侧栏的完成标记、顶栏的保存状态都会停在旧值 —— 六页也就不是"同一份状态"了。
 *
 * 所以这里做成模块级外部存储：localStorage 持久化 + 内存快照 + 订阅通知，
 * 用 useSyncExternalStore 让所有组件共享同一个快照。
 */

import { useCallback, useEffect, useSyncExternalStore } from "react";

const KEY = "sdgk.flow.v1";

export interface FlowState {
  cycleId: number | null;
  profileId: number | null;
  preferenceId: number | null;
  sessionId: number | null;
  schemeId: number | null;
  /** 最近一次生成推荐时关于输入的口径提示（例如位次超出当年一段范围）。 */
  inputWarnings: string[];
}

const EMPTY: FlowState = {
  cycleId: null,
  profileId: null,
  preferenceId: null,
  sessionId: null,
  schemeId: null,
  inputWarnings: [],
};

interface Snapshot {
  state: FlowState;
  loaded: boolean;
}

let snapshot: Snapshot = { state: EMPTY, loaded: false };
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Snapshot {
  return snapshot;
}

/** SSR 阶段没有 localStorage：先给初始快照，客户端挂载后会立刻读一次。 */
function getServerSnapshot(): Snapshot {
  return snapshot;
}

let loadStarted = false;

/** 首次挂载时把 localStorage 读进内存快照；失败就保持空状态，不阻断流程。 */
function ensureLoaded(): void {
  if (loadStarted) return;
  loadStarted = true;
  if (typeof window === "undefined") return;
  let next: FlowState = EMPTY;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<FlowState>;
      if (parsed && typeof parsed === "object") {
        next = {
          ...EMPTY,
          ...parsed,
          inputWarnings: Array.isArray(parsed.inputWarnings) ? parsed.inputWarnings : [],
        };
      }
    }
  } catch {
    // 存档损坏就丢弃，不能让坏数据阻断填写
    next = EMPTY;
  }
  snapshot = { state: next, loaded: true };
  emit();
}

function write(next: FlowState): void {
  snapshot = { state: next, loaded: true };
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 隐私模式下可能写不了；内存里仍然是新值，流程照常
  }
  emit();
}

export function useFlowState() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // 必须在 effect 里读存储，不能在渲染期间读：
  // 渲染期改快照会让服务端与客户端首帧不一致，直接触发 hydration 失败。
  useEffect(() => {
    ensureLoaded();
  }, []);

  const update = useCallback((patch: Partial<FlowState>) => {
    write({ ...snapshot.state, ...patch });
  }, []);

  const reset = useCallback(() => {
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      // ignore
    }
    write(EMPTY);
  }, []);

  return { state: current.state, update, reset, loaded: current.loaded };
}
