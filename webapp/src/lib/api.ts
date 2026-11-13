/**
 * Typed API client for the MEIKO AGENT control plane.
 *
 * The browser only ever calls same-origin `/api/backend/*`, which Next.js
 * rewrites to the FastAPI service. Credentials live in an httpOnly-free session
 * token issued by the backend; nothing here trusts client-side identity.
 */

const BASE = "/api/backend";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, message: string, code = "error", detail?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

const TOKEN_KEY = "meiko.session";
const USER_KEY = "meiko.user";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): { id: number; first_name: string; username: string | null; is_owner: boolean; role: string } | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setSession(token: string, user: unknown) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event("meiko:session"));
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event("meiko:session"));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("content-type") && init.body) headers.set("content-type", "application/json");
  const token = getToken();
  if (token) headers.set("authorization", `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers, cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail = (data as { detail?: { error?: string; message?: string } } | null)?.detail;
    const message =
      (detail && typeof detail === "object" && detail.message) ||
      (typeof detail === "string" ? detail : null) ||
      response.statusText ||
      "Request failed";
    const code = (detail && typeof detail === "object" && (detail.error as string)) || `http_${response.status}`;
    throw new ApiError(response.status, String(message), String(code), detail);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),

  // ---------------------------------------------------------------- auth
  loginDev: (telegramId: number, username?: string) =>
    request<{ token: string; expires_in: number; user: unknown }>("/auth/dev", {
      method: "POST",
      body: JSON.stringify({ telegram_id: telegramId, username, first_name: "Operator" }),
    }),
  loginTelegram: (payload: Record<string, unknown>) =>
    request<{ token: string; expires_in: number; user: unknown }>("/auth/telegram", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  loginWebApp: (initData: string) =>
    request<{ token: string; expires_in: number; user: unknown }>("/auth/webapp", {
      method: "POST",
      body: JSON.stringify({ init_data: initData }),
    }),
  me: () => request<import("./types").User>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};

export function wsStreamUrl(): string {
  return `${BASE}/activity/stream`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (Number.isNaN(seconds)) return "never";
  if (seconds < 60) return `${Math.max(seconds, 0)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatBytes(bytes: number | undefined | null): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

export function formatDuration(seconds: number | undefined | null): string {
  if (!seconds) return "0s";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${Math.floor(seconds)}s`;
}
