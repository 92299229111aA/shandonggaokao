"use client";

/**
 * 轻量 toast。六页的保存成功/失败、加入方案等反馈都走这里。
 *
 * 自研而非引库：只需要一条底部提示 + 自动消失，引一个依赖不划算。
 * 用 role="status" + aria-live 让屏幕阅读器能读到，这是文档 §2.4 的要求。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

type ToastTone = "ok" | "warn" | "error";

interface ToastState {
  message: string;
  tone: ToastTone;
  id: number;
}

interface ToastApi {
  show: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastApi>({ show: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [current, setCurrent] = useState<ToastState | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const counter = useRef(0);

  const show = useCallback((message: string, tone: ToastTone = "ok") => {
    counter.current += 1;
    setCurrent({ message, tone, id: counter.current });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCurrent(null), 3600);
  }, []);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className={`toast${current ? " show" : ""}${current ? ` ${current.tone}` : ""}`}
        role="status"
        aria-live="polite"
        hidden={!current}
      >
        {current?.message}
      </div>
    </ToastContext.Provider>
  );
}
