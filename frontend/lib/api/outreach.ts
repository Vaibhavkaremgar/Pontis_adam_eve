/**
 * What this file does:
 * Sends selected candidates to outreach workflow.
 *
 * What API it connects to:
 * POST /outreach
 *
 * How it fits in the pipeline:
 * Frontend submits selected IDs; backend handles Slack notifications and outreach orchestration.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type OutreachPayload = {
  jobId: string;
  selectedCandidates: string[];
};

type OutreachData = {
  message: string;
};

/** This function calls backend API and returns structured response. */
export async function sendOutreach(payload: OutreachPayload): Promise<ApiResponse<OutreachData>> {
  return requestApi<OutreachData>({
    url: `${API_BASE_URL}/outreach`,
    method: "POST",
    payload
  });
}
