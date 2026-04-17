/**
 * What this file does:
 * Builds API headers with auth token for backend requests.
 *
 * What API it connects to:
 * Applied across all /lib/api calls.
 *
 * How it fits in the pipeline:
 * Ensures every backend call carries recruiter session token after login.
 */
import { getStoredToken } from "@/lib/session";

export function buildApiHeaders(extraHeaders?: Record<string, string>) {
  const token = getStoredToken();

  return {
    ...(extraHeaders || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
}
