/**
 * What this file does:
 * Builds shared API headers for backend requests.
 *
 * What API it connects to:
 * Applied across all /lib/api calls.
 *
 * How it fits in the pipeline:
 * Keeps request header handling centralized while auth now rides on httpOnly cookies.
 */
export function buildApiHeaders(extraHeaders?: Record<string, string>) {
  return {
    ...(extraHeaders || {})
  };
}
