/**
 * Typed client for the FastAPI backend.
 *
 * Every error the API returns uses one envelope:
 *   { "error": { "code", "message", "details" } }
 * so the UI can always show a real message instead of "something went wrong".
 */

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isVersionConflict(): boolean {
    return this.status === 409;
  }
}

// 桌面版由同一个后端进程提供前端与 API，所以 base 传 "/"（去掉尾斜杠就是空串，
// 于是所有请求走相对路径）。不能用空字符串当哨兵 —— 构建时会被当成"未设置"而回落到 8000。
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

/** 给用户看的地址；相对路径模式下后端就是"本地服务"。 */
const API_LABEL = API_BASE_URL || "本地服务";

const API_PREFIX = "/api/v1";

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      "NETWORK_ERROR",
      `无法连接后端服务（${API_LABEL}）。请确认后端已启动：docker compose up -d api`,
      { cause: String(cause) },
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? safeParse(text) : undefined;

  if (!response.ok) {
    const envelope = (payload as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })
      ?.error;
    throw new ApiError(
      response.status,
      envelope?.code ?? `HTTP_${response.status}`,
      envelope?.message ?? `请求失败（HTTP ${response.status}）`,
      envelope?.details ?? {},
    );
  }

  return payload as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}
