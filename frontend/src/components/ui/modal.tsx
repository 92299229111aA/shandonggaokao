import * as React from "react";

import { cn } from "@/lib/utils";

/** Minimal modal used for ACS / preference explanations. No extra dependency. */
export function Modal({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div
        className={cn("max-h-[80vh] w-full max-w-2xl overflow-auto rounded-lg border bg-card p-5 shadow-lg", className)}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 text-sm text-muted-foreground hover:bg-accent"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function KeyValueList({ entries }: { entries: Array<[string, React.ReactNode]> }) {
  return (
    <dl className="space-y-1.5 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-start justify-between gap-4">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="text-right font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
