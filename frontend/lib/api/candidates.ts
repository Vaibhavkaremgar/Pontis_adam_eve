/**
 * What this file does:
 * Retrieves ranked candidates for a job.
 *
 * What API it connects to:
 * GET /candidates?jobId=...&refined=true|false
 *
 * How it fits in the pipeline:
 * Frontend displays returned candidates; retrieval/ranking logic stays in backend.
 */
import { API_BASE_URL } from "@/lib/config";
import type { Candidate } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type CandidateQuery = {
  jobId: string;
  refined?: boolean;
  debug?: boolean;
  mode?: "volume" | "elite";
  refresh?: boolean;
};

type SwipePayload = {
  jobId: string;
  candidateId: string;
  action: "accept" | "reject";
};

type SwipeData = {
  jobId: string;
  candidateId: string;
  action: "accept" | "reject";
  previousState: string;
  newState: string;
  message: string;
  ats_export_status?: string;
};

type ExportPayload = {
  jobId: string;
  candidateIds: string[];
  provider?: string;
};

type ExportData = {
  provider: string;
  status: string;
  exportedCount: number;
  reference: string;
  results?: Array<{
    candidateId: string;
    status: string;
    error?: string;
    existing?: boolean;
  }>;
};

type SelectionPayload = {
  jobId: string;
  candidateId: string;
};

type SelectionFinalData = {
  sessionId: string;
  jobId: string;
  status: string;
  currentBatchIndex: number;
  totalBatches: number;
  batchSize: number;
  selectedCandidateIds: string[];
  rejectedCandidateIds: string[];
  currentBatch: Candidate[];
  analysis?: Record<string, unknown> | null;
  completed: boolean;
  finalCandidates: Candidate[];
  topCandidates?: Candidate[];
  stage?: string;
  recommendedQuestions?: string[];
  gapAnalysis?: Record<string, unknown>;
  intentProfile?: Record<string, unknown>;
  currentPair?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
  voiceSummary?: string;
  pairExplanation?: Record<string, unknown>;
};

type SelectionUpdateData = {
  status?: string;
  enrichmentStatus?: string;
  outreachStatus?: string;
  replyStatus?: string;
  candidateStatus?: string;
  contactEmail?: string;
  contactPhone?: string;
  warning?: string;
  interviewReuse?: {
    reused: boolean;
    workflowToken: string;
  };
};

type CandidateRequestState = {
  request_id?: string;
  candidate_id: string;
  job_id: string;
  status?: string;
  recruiter_action?: "NONE" | "INTERESTED" | "NOT_INTERESTED" | string;
  created_at?: string | null;
  updated_at?: string | null;
  responded_at?: string | null;
  eve_delivery_status?: "queued" | "delivered" | "failed" | string;
};

/** This function calls backend API and returns structured response. */
export async function getCandidates({ jobId, refined, debug }: CandidateQuery): Promise<ApiResponse<Candidate[]>> {
  const params = new URLSearchParams({ jobId });
  if (refined) params.set("refined", "true");
  if (refined) params.set("refresh", "true");
  // Always pass debug=true so the backend includes sourcingState / noResultsReason
  params.set("debug", "true");

  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates?${params.toString()}`,
    method: "GET",
  });
}

export async function getCandidatesWithMode({
  jobId,
  mode = "volume",
  refresh = false,
}: CandidateQuery): Promise<ApiResponse<Candidate[]>> {
  const params = new URLSearchParams({ jobId, mode });
  if (refresh) params.set("refresh", "true");
  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates?${params.toString()}`,
    method: "GET",
  });
}

export async function createCandidateInterest(payload: { jobId: string; candidateId: string }): Promise<ApiResponse<CandidateRequestState>> {
  return requestApi<CandidateRequestState>({
    url: `${API_BASE_URL}/candidates/${encodeURIComponent(payload.candidateId)}/interest?jobId=${encodeURIComponent(payload.jobId)}`,
    method: "POST",
  });
}

export async function createCandidateNotInterested(payload: { jobId: string; candidateId: string }): Promise<ApiResponse<CandidateRequestState>> {
  return requestApi<CandidateRequestState>({
    url: `${API_BASE_URL}/candidates/${encodeURIComponent(payload.candidateId)}/not-interested?jobId=${encodeURIComponent(payload.jobId)}`,
    method: "POST",
  });
}

export async function getCandidateRequestStatus(payload: { jobId: string; candidateId: string }): Promise<ApiResponse<CandidateRequestState>> {
  return requestApi<CandidateRequestState>({
    url: `${API_BASE_URL}/candidates/${encodeURIComponent(payload.candidateId)}/request-status?jobId=${encodeURIComponent(payload.jobId)}`,
    method: "GET",
  });
}

export type CandidateFullProfile = {
  candidate_id: string;
  name: string;
  role?: string;
  company?: string;
  location?: string;
  years_experience?: number;
  skills?: string[];
  summary?: string;
  email?: string;
  phone?: string;
  linkedin_url?: string;
  github_url?: string;
  resume_text?: string;
  work_experience?: unknown[];
  education?: unknown[];
  certifications?: string[];
  projects?: string[];
  profile_access?: "LIMITED" | "FULL" | "INTERNAL";
  request_status?: string | null;
  recruiter_action?: string;
  request_id?: string;
  responded_at?: string | null;
  agency_name?: string;
  raw_profile_available?: boolean;
};

export async function getCandidateFullProfile(payload: { jobId: string; candidateId: string }): Promise<ApiResponse<CandidateFullProfile>> {
  return requestApi<CandidateFullProfile>({
    url: `${API_BASE_URL}/candidates/${encodeURIComponent(payload.candidateId)}/profile?jobId=${encodeURIComponent(payload.jobId)}`,
    method: "GET",
  });
}

export async function getInternalCandidateProfile(payload: { jobId: string; candidateId: string }): Promise<ApiResponse<CandidateFullProfile>> {
  return requestApi<CandidateFullProfile>({
    url: `${API_BASE_URL}/candidates/${encodeURIComponent(payload.candidateId)}/internal-profile?jobId=${encodeURIComponent(payload.jobId)}`,
    method: "GET",
  });
}

export async function getAcceptedCandidates(jobId: string): Promise<ApiResponse<CandidateFullProfile[]>> {
  return requestApi<CandidateFullProfile[]>({
    url: `${API_BASE_URL}/candidates/accepted?jobId=${encodeURIComponent(jobId)}`,
    method: "GET",
  });
}

export async function getPendingAcceptanceCandidates(jobId: string): Promise<ApiResponse<CandidateFullProfile[]>> {
  return requestApi<CandidateFullProfile[]>({
    url: `${API_BASE_URL}/candidates/pending-acceptance?jobId=${encodeURIComponent(jobId)}`,
    method: "GET",
  });
}

export async function getShortlistedCandidates(jobId: string): Promise<ApiResponse<Candidate[]>> {
  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates/shortlisted?jobId=${encodeURIComponent(jobId)}`,
    method: "GET",
  });
}

export async function getFinalSelectionResults(jobId: string): Promise<ApiResponse<SelectionFinalData>> {
  return requestApi<SelectionFinalData>({
    url: `${API_BASE_URL}/candidates/selection/final?jobId=${encodeURIComponent(jobId)}`,
    method: "GET",
  });
}

export async function swipeCandidate(payload: SwipePayload): Promise<ApiResponse<SwipeData>> {
  return requestApi<SwipeData>({
    url: `${API_BASE_URL}/candidates/swipe`,
    method: "POST",
    payload,
  });
}

export async function exportCandidates(payload: ExportPayload): Promise<ApiResponse<ExportData>> {
  return requestApi<ExportData>({
    url: `${API_BASE_URL}/candidates/export`,
    method: "POST",
    payload: payload.provider ? payload : { jobId: payload.jobId, candidateIds: payload.candidateIds },
  });
}

type EnrichmentStateData = {
  enrichment_state: string;
  enrichment_requested_at?: string;
  enrichment_completed_at?: string;
  enrichment_providers_used?: string[];
  enrichment_failed_reason?: string;
};

export async function getCandidateEnrichmentState(
  jobId: string,
  candidateId: string,
): Promise<ApiResponse<EnrichmentStateData>> {
  const params = new URLSearchParams({ jobId, candidateId });
  return requestApi<EnrichmentStateData>({
    url: `${API_BASE_URL}/candidates/enrichment?${params.toString()}`,
    method: "GET",
  });
}

export async function selectCandidateForEnrichment(payload: SelectionPayload): Promise<ApiResponse<SelectionUpdateData>> {
  return requestApi<SelectionUpdateData>({
    url: `${API_BASE_URL}/candidates/select`,
    method: "POST",
    payload,
  });
}
