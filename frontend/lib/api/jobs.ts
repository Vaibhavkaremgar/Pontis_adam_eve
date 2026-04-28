/**
 * What this file does:
 * Handles recruiter job parsing requests from a URL and returns structured job hints.
 *
 * What API it connects to:
 * POST /api/jobs/parse
 *
 * How it fits in the pipeline:
 * Lets the job creation page prefill structured fields before the recruiter submits the full brief.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type JobParsePayload = {
  url: string;
};

export type JobParseData = {
  title: string;
  description: string;
  location: string;
  compensation: string;
  workAuthorization: "required" | "preferred" | "not-required";
  remotePolicy: string;
  experienceRequired: string;
};

export async function parseJobPosting(payload: JobParsePayload): Promise<ApiResponse<JobParseData>> {
  return requestApi<JobParseData>({
    url: `${API_BASE_URL}/jobs/parse`,
    method: "POST",
    payload
  });
}
