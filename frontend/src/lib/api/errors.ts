/**
 * Typed wrappers over the backend error envelope so pages can react to the
 * interesting cases (409 version conflict, network down) without parsing strings.
 */

import { ApiError } from "./client";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return `${error.message}。你看到的是旧版本，请点击“刷新方案”后重试。`;
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "发生未知错误";
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

export function isNetworkError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}

export { ApiError };
