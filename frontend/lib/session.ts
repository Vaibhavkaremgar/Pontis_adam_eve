/**
 * What this file does:
 * Provides helpers to read/write auth session data in browser storage.
 *
 * What API it connects to:
 * Used by /lib/api clients to attach token to backend requests.
 *
 * How it fits in the pipeline:
 * Keeps token/session persistence centralized so app can restore authenticated recruiter state on reload.
 */
import type { User } from "@/types";

const TOKEN_KEY = "pontis_token";
const USER_KEY = "pontis_user";

export function getStoredToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;

  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;

  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function storeSession(token: string, user: User) {
  if (typeof window === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
