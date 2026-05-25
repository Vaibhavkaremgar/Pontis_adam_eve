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
  details: {
    candidateId: string;
    status: string;
    reason?: string;
    toEmail?: string;
    providerId?: string;
    originalEmail?: string;
  }[];
  skippedCandidates: { candidateId: string; reason: string }[];
  skipReasons: Record<string, number>;
  warnings?: string[];
  debug?: {
    provider?: string;
    fromEmail?: string;
    providerConfigured?: boolean;
    dryRun?: boolean;
  };
};

export type OutreachStatusItem = {
  candidateId: string;
  status: Candidate["outreachStatus"];
  provider: string;
  toEmail: string;
  attemptCount: number;
  followUpCount?: number;
  lastSentAt: string | null;
  lastContactedAt?: string | null;
  lastOpenedAt?: string | null;
  lastRepliedAt?: string | null;
  nextFollowUpAt: string | null;
  lastError: string;
  replyState?: string;
  archiveReason?: string;
  openCount?: number;
  replyCount?: number;
  engagementScore?: number;
  replyLikelihoodScore?: number;
  responsivenessScore?: number;
};

export type EmailPreview = {
  subject: string;
  body: string;
  toEmail: string;
  candidateEmail?: string;
  manualRequired?: boolean;
  fallbackReason?: string;
};

/** This function calls backend API and returns structured response. */
export async function sendOutreach(payload: OutreachPayload): Promise<ApiResponse<OutreachData>> {
  return requestApi<OutreachData>({
    url: `${API_BASE_URL}/outreach`,
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
