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

export type InterviewSession = {
  id: string;
  jobId: string;
  candidateId: string;
  email: string;
  token: string;
  status: string;
  sourceType?: string;
  workflowToken?: string;
  stageName?: string;
  stageIndex?: number;
  stageHistory?: Array<Record<string, unknown>>;
  expiresAt: string;
  bookedAt: string | null;
  scheduledAt?: string | null;
  timezone?: string;
  availableSlots?: string[];
  bookingLink?: string;
  bookingUrl?: string;
  meetingLink?: string;
};

export type InterviewBookingPayload = {
  token: string;
  scheduledAt?: string | null;
};

export type InterviewReschedulePayload = {
  token: string;
  scheduledAt: string;
  reason?: string;
};

export type InterviewDecisionPayload = {
  jobId: string;
  candidateId: string;
  action: string;
  targetStage?: string;
  interviewerId?: string;
  notes?: string;
  recommendation?: string;
  sourceType?: string;
};

export type InterviewEvaluationPayload = {
  jobId: string;
  candidateId: string;
  stageName?: string;
  interviewerId?: string;
  summary?: string;
  recommendation?: string;
  competencyScores?: Record<string, number>;
  notes?: string;
  metadata?: Record<string, unknown>;
};

export type InterviewInsights = {
  jobId: string;
  candidateId: string;
  currentStage: string;
  workflowToken?: string;
  workflowPayload?: Record<string, unknown>;
  progression?: Array<Record<string, unknown>>;
  evaluationCount?: number;
  evaluations?: Array<Record<string, unknown>>;
  intelligence?: Record<string, unknown>;
  currentSession?: Record<string, unknown> | null;
  stageHistory?: Array<Record<string, unknown>>;
};

/** This function calls backend API and returns structured response. */
export async function getInterviewStatuses(jobId: string): Promise<ApiResponse<InterviewStatus[]>> {
  return requestApi<InterviewStatus[]>({
    url: `${API_BASE_URL}/interviews?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function getSession(token: string): Promise<ApiResponse<InterviewSession>> {
  return requestApi<InterviewSession>({
    url: `${API_BASE_URL}/interview/session?token=${encodeURIComponent(token)}`,
    method: "GET"
  });
}

export async function bookSession(payload: InterviewBookingPayload): Promise<ApiResponse<InterviewSession>> {
  return requestApi<InterviewSession>({
    url: `${API_BASE_URL}/interview/book`,
    method: "POST",
    payload
  });
}

export async function rescheduleSession(payload: InterviewReschedulePayload): Promise<ApiResponse<InterviewSession>> {
  return requestApi<InterviewSession>({
    url: `${API_BASE_URL}/interview/reschedule`,
    method: "POST",
    payload
  });
}

export async function getInterviewInsights(jobId: string, candidateId: string): Promise<ApiResponse<InterviewInsights>> {
  const params = `?jobId=${encodeURIComponent(jobId)}&candidateId=${encodeURIComponent(candidateId)}`;
  return requestApi<InterviewInsights>({
    url: `${API_BASE_URL}/interview/insights${params}`,
    method: "GET"
  });
}

export async function submitInterviewDecision(payload: InterviewDecisionPayload): Promise<ApiResponse<Record<string, unknown>>> {
  return requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/interview/decision`,
    method: "POST",
    payload
  });
}

export async function recordInterviewEvaluation(payload: InterviewEvaluationPayload): Promise<ApiResponse<Record<string, unknown>>> {
  return requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/interview/evaluations`,
    method: "POST",
    payload
  });
}

export type FirstRoundInterviewRequest = {
  candidateId: string;
  jobId: string;
  availableSlots?: string[];
  timezone?: string;
};

export type FirstRoundInterviewResponse = InterviewSession & {
  interviewRound?: string;
  jobTitle?: string;
  companyName?: string;
};

export async function requestFirstRoundInterview(
  payload: FirstRoundInterviewRequest
): Promise<ApiResponse<FirstRoundInterviewResponse>> {
  return requestApi<FirstRoundInterviewResponse>({
    url: `${API_BASE_URL}/interviews/first-round/request`,
    method: "POST",
    payload,
  });
}
