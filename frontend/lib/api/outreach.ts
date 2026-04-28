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
import type { Candidate } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type OutreachPayload = {
  jobId: string;
  selectedCandidates: string[];
  customBody?: string;
};

type OutreachData = {
  success: boolean;
  processed: number;
  sent: number;
  skipped: number;
  details: { candidateId: string; status: string; reason: string; toEmail: string }[];
  skippedCandidates: { candidateId: string; reason: string }[];
  skipReasons: Record<string, number>;
  warnings?: string[];
};

type QueueOutreachData = {
  queued: boolean;
  job_id: string;
  selected_count: number;
};

export type OutreachStatusItem = {
  candidateId: string;
  status: Candidate["outreachStatus"];
  provider: string;
  toEmail: string;
  attemptCount: number;
  lastSentAt: string | null;
  nextFollowUpAt: string | null;
  lastError: string;
};

export type EmailPreview = {
  subject: string;
  body: string;
  toEmail: string;
};

/** This function calls backend API and returns structured response. */
export async function sendOutreach(payload: OutreachPayload): Promise<ApiResponse<OutreachData>> {
  return requestApi<OutreachData>({
    url: `${API_BASE_URL}/outreach`,
    method: "POST",
    payload
  });
}

export async function queueOutreach(payload: OutreachPayload): Promise<ApiResponse<QueueOutreachData>> {
  return requestApi<QueueOutreachData>({
    url: `${API_BASE_URL}/outreach/queue`,
    method: "POST",
    payload
  });
}

export async function getOutreachStatuses(jobId: string): Promise<ApiResponse<OutreachStatusItem[]>> {
  return requestApi<OutreachStatusItem[]>({
    url: `${API_BASE_URL}/outreach/status?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function getEmailPreview(jobId: string, candidateId: string): Promise<ApiResponse<EmailPreview>> {
  return requestApi<EmailPreview>({
    url: `${API_BASE_URL}/outreach/preview?jobId=${encodeURIComponent(jobId)}&candidateId=${encodeURIComponent(candidateId)}`,
    method: "GET"
  });
}
