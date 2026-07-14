/**
 * What this file does:
 * Fetches recruiter results workspace data through Adam's backend proxy.
 *
 * What API it connects to:
 * GET /results?jobId=...
 * GET /results/{workflowToken}
 * GET /results/video/{workflowToken}
 *
 * How it fits in the pipeline:
 * The frontend only talks to Adam. Adam federates to Pontis for interview intelligence and media.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type ResultListItem = {
  candidateId: string;
  name: string;
  status: string;
  workflowToken: string;
  score: number;
  recommendation: string;
  completionState: string;
  videoAvailable: boolean;
  createdAt?: string | null;
  expiresAt?: string | null;
};

export type ResultListResponse = {
  jobId: string;
  recruiterId: string;
  candidates: ResultListItem[];
  counts: {
    completed: number;
    available: number;
  };
};

export type ResultWorkspaceResponse = {
  candidate: {
    id: string;
    name: string;
    role: string;
    company: string;
    headline: string;
    location: string;
    email: string;
    summary: string;
    skills: string[];
    source: string;
  };
  recording: {
    sessionToken: string;
    recordingPath: string;
    videoAvailable: boolean;
  };
  transcript: string;
  summary: string;
  scores: {
    overall: number;
    technical: number;
    communication: number;
    cultureFit: number;
  };
  decision: string;
  status: string;
  timeline: {
    events?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  recommendation: string;
  analysis: {
    strengths?: string[];
    weaknesses?: string[];
    riskAreas?: string[];
    communication?: string;
    technicalDepth?: string;
    [key: string]: unknown;
  };
  metadata: Record<string, unknown>;
  createdAt?: string | null;
  expiresAt?: string | null;
  operations?: {
    decisionState?: string;
    availableActions?: string[];
    followUpPrompt?: {
      show?: boolean;
      message?: string;
    };
  };
};

export type AdvanceResultPayload = {
  roundType: string;
  mode: string;
  meetUrl: string;
  officeAddress: string;
  interviewer: {
    name: string;
    email: string;
  };
  recruiterEmail: string;
  slots: string[];
  notes: string;
  timezone?: string;
  duration?: string;
  panelInterviewers?: string[];
};

export type ResultDecisionPayload = {
  decision: "pass" | "hold" | "reject";
};

export async function getResultsList(jobId: string): Promise<ApiResponse<ResultListResponse>> {
  return requestApi<ResultListResponse>({
    url: `${API_BASE_URL}/results?jobId=${encodeURIComponent(jobId)}`,
    method: "GET",
  });
}

export async function getResultWorkspace(workflowToken: string): Promise<ApiResponse<ResultWorkspaceResponse>> {
  return requestApi<ResultWorkspaceResponse>({
    url: `${API_BASE_URL}/results/${encodeURIComponent(workflowToken)}`,
    method: "GET",
  });
}

export function getResultVideoUrl(workflowToken: string): string {
  return `${API_BASE_URL}/results/video/${encodeURIComponent(workflowToken)}`;
}

export async function submitResultDecision(workflowToken: string, payload: ResultDecisionPayload): Promise<ApiResponse<{ workflowToken: string; decision: string; duplicate?: boolean }>> {
  return requestApi({
    url: `${API_BASE_URL}/results/${encodeURIComponent(workflowToken)}/decision`,
    method: "POST",
    payload,
  });
}

export async function advanceResultWorkflow(
  workflowToken: string,
  payload: AdvanceResultPayload,
): Promise<ApiResponse<{
  workflowToken: string;
  jobId: string;
  candidateId: string;
  candidateEmail: string;
  subject: string;
  messageId: string;
  status: string;
  decisionState: string;
  roundType: string;
  mode: string;
  sentAt: string;
  recipient: string;
  cc: string[];
  duplicate?: boolean;
}>> {
  return requestApi({
    url: `${API_BASE_URL}/results/${encodeURIComponent(workflowToken)}/advance`,
    method: "POST",
    payload,
  });
}
