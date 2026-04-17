/**
 * What this file does:
 * Loads interview-ready candidates for final stage display.
 *
 * What API it connects to:
 * GET /interviews?jobId=...
 *
 * How it fits in the pipeline:
 * Frontend shows interview statuses returned by backend workflow systems.
 */
import { API_BASE_URL } from "@/lib/config";
import type { InterviewStatus } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

/** This function calls backend API and returns structured response. */
export async function getInterviewStatuses(jobId: string): Promise<ApiResponse<InterviewStatus[]>> {
  return requestApi<InterviewStatus[]>({
    url: `${API_BASE_URL}/interviews?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}
