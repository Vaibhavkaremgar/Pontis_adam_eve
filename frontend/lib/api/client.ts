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
    const response = await fetch(url, {
      method,
      headers: buildApiHeaders({
        ...(payload ? { "Content-Type": "application/json" } : {})
      }),
      ...(payload ? { body: JSON.stringify(payload) } : {})
    });

    let parsed: Partial<ApiResponse<T>> | null = null;

    try {
      parsed = (await response.json()) as Partial<ApiResponse<T>>;
    } catch {
      parsed = null;
    }

    if (response.status === 401) {
      dispatchUnauthorizedEvent();

      const result: ApiResponse<T> = {
        success: false,
        data: null,
        error: "Session expired. Please log in again."
      };

      logRequest({ url, method, payload, response: result });
      return result;
    }

    if (!response.ok) {
      const result: ApiResponse<T> = {
        success: false,
        data: null,
        error:
          response.status >= 500
            ? "Server error. Please try again in a moment."
            : parsed?.error || "Request failed"
      };

      logRequest({ url, method, payload, response: result });
      return result;
    }

    const result: ApiResponse<T> = {
      success: Boolean(parsed?.success),
      data: (parsed?.data as T | null) ?? null,
      error: parsed?.error
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
