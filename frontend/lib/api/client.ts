/**
 * What this file does:
 * Centralizes API request execution, parsing, and error handling.
 *
 * What API it connects to:
 * Used by all /lib/api endpoint wrappers.
 *
 * How it fits in the pipeline:
 * Provides production-grade handling for auth expiry (401), server failures (500), and normalized ApiResponse output.
 */
import { logRequest } from "@/lib/logger";

import { buildApiHeaders } from "./headers";
import type { ApiResponse } from "./types";

type RequestApiInput = {
  url: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  payload?: unknown;
};

let csrfToken: string | null = null;
let csrfTokenPromise: Promise<string> | null = null;

function requiresCsrf(method: RequestApiInput["method"], url: string): boolean {
  if (method === "GET") return false;
  const safeAuthPaths = ["/auth/request-otp", "/auth/verify-otp", "/auth/google", "/auth/csrf"];
  return !safeAuthPaths.some((path) => url.includes(path));
}

function suppressUnauthorizedDispatch(url: string): boolean {
  return ["/auth/me", "/auth/logout", "/auth/request-otp", "/auth/verify-otp", "/auth/google"].some((path) =>
    url.includes(path)
  );
}

async function fetchCsrfToken(url: string): Promise<string> {
  if (csrfToken) return csrfToken;
  if (csrfTokenPromise) return csrfTokenPromise;

  const csrfUrl = new URL("/api/backend/auth/csrf", window.location.origin).toString();
  csrfTokenPromise = fetch(csrfUrl, {
    method: "GET",
    credentials: "include",
    headers: buildApiHeaders()
  })
    .then(async (response) => {
      const parsed = (await response.json().catch(() => null)) as Partial<ApiResponse<{ token?: string }>> | null;
      if (!response.ok || !parsed?.data?.token) {
        throw new Error(parsed?.error || "Failed to load CSRF token");
      }
      csrfToken = parsed.data.token;
      return csrfToken;
    })
    .finally(() => {
      csrfTokenPromise = null;
    });

  return csrfTokenPromise;
}

export function clearCachedCsrfToken() {
  csrfToken = null;
}

function dispatchUnauthorizedEvent() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event("auth:unauthorized"));
}

/**
 * This function calls backend API and returns structured response.
 * It also handles global API errors:
 * - 401 -> trigger global logout flow
 * - 500 -> return friendly server error message
 */
export async function requestApi<T>({ url, method, payload }: RequestApiInput): Promise<ApiResponse<T>> {
  logRequest({ url, method, payload, response: "request_started" });

  try {
    const headers: Record<string, string> = {
      ...buildApiHeaders({
        ...(payload ? { "Content-Type": "application/json" } : {})
      })
    };

    if (typeof window !== "undefined" && requiresCsrf(method, url)) {
      headers["X-CSRF-Token"] = await fetchCsrfToken(url);
    }

    const response = await fetch(url, {
      method,
      credentials: "include",
      headers,
      ...(payload ? { body: JSON.stringify(payload) } : {})
    });

    const responseText = await response.text();
    let parsed: any = null;

    try {
      parsed = responseText ? JSON.parse(responseText) : null;
    } catch {
      parsed = null;
    }

    if (response.status === 401) {
      if (!suppressUnauthorizedDispatch(url)) {
        dispatchUnauthorizedEvent();
      }
      clearCachedCsrfToken();

      const result: ApiResponse<T> = {
        success: false,
        data: null,
        error: "Session expired. Please log in again."
      };

      logRequest({ url, method, payload, response: result });
      return result;
    }

    if (response.status === 403 && requiresCsrf(method, url)) {
      clearCachedCsrfToken();
    }

    if (!response.ok) {
      const detailParts = [
        parsed?.error,
        parsed?.detail,
        parsed?.message,
        typeof parsed?.detail === "string" ? parsed.detail : "",
        typeof parsed?.error === "string" ? parsed.error : "",
      ].filter((value): value is string => Boolean(value && value.trim()));
      const rawDetail = detailParts[0] || responseText.trim();
      const result: ApiResponse<T> = {
        success: false,
        data: null,
        error:
          response.status >= 500
            ? `Server error (${response.status}): ${rawDetail || response.statusText || "Please try again in a moment."}`
            : rawDetail || response.statusText || "Request failed"
      };

      logRequest({ url, method, payload, response: result });
      return result;
    }

    const result: ApiResponse<T> = {
      success: Boolean(parsed?.success),
      data: (parsed?.data as T | null) ?? null,
      error: parsed?.error || null,
      debug: parsed?.debug ?? null
    };

    logRequest({ url, method, payload, response: result });
    return result;
  } catch {
    const result: ApiResponse<T> = {
      success: false,
      data: null,
      error: "Network error while calling backend API"
    };

    logRequest({ url, method, payload, response: result });
    return result;
  }
}
