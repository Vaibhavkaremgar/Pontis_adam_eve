/**
 * What this file does:
 * Logs request/response metadata for API interactions.
 *
 * What API it connects to:
 * Used by all /lib/api client functions.
 *
 * How it fits in the pipeline:
 * This logs all API interactions for debugging backend integration.
 */

type LogRequestInput = {
  url: string;
  method: string;
  payload?: unknown;
  response?: unknown;
};

type LogEventInput = {
  event: string;
  payload?: Record<string, unknown>;
};

export function logRequest({ url, method, payload, response }: LogRequestInput) {
  console.log("[API LOG]", {
    timestamp: new Date().toISOString(),
    method,
    url,
    payload,
    response
  });
}

export function logEvent({ event, payload }: LogEventInput) {
  console.log("[APP LOG]", {
    timestamp: new Date().toISOString(),
    event,
    payload: payload || {}
  });
}
