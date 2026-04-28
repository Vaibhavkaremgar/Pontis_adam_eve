/**
 * What this file does:
 * Retrieves operational metrics for the hiring workflow.
 *
 * What API it connects to:
 * GET /metrics
 *
 * How it fits in the pipeline:
 * Surfaces product health and conversion signals in the UI.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type MetricsSnapshot = {
  events: number;
  retrieval_requests: number;
  local_hits: number;
  pdl_fallbacks: number;
  fallbacks: number;
  errors: number;
  emails_sent: number;
  emails_failed: number;
  replies_received: number;
  interviews_booked: number;
  followups_sent: number;
  local_hit_rate: number;
  pdl_fallback_rate: number;
  fallback_rate: number;
  error_rate: number;
  reply_rate: number;
  followup_rate: number;
  conversion_rate: number;
  avg_similarity: number;
  evaluation?: Record<string, unknown>;
};

export async function getMetrics(): Promise<ApiResponse<MetricsSnapshot>> {
  return requestApi<MetricsSnapshot>({
    url: `${API_BASE_URL}/metrics`,
    method: "GET"
  });
}
