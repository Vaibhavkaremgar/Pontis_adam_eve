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
import type { Candidate, CandidateSelectionSession } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type CandidateQuery = {
  jobId: string;
  refined?: boolean;
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

type SelectionResponse = CandidateSelectionSession;

/** This function calls backend API and returns structured response. */
export async function getCandidates({ jobId, refined }: CandidateQuery): Promise<ApiResponse<Candidate[]>> {
  const params = new URLSearchParams({ jobId });
  if (refined) params.set("refined", "true");
  if (refined) params.set("refresh", "true");

  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates?${params.toString()}`,
    method: "GET"
  });
}

export async function getCandidatesWithMode({
  jobId,
  mode = "volume",
  refresh = false
}: CandidateQuery): Promise<ApiResponse<Candidate[]>> {
  const params = new URLSearchParams({ jobId, mode });
  if (refresh) params.set("refresh", "true");
  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates?${params.toString()}`,
    method: "GET"
  });
}

export async function getShortlistedCandidates(jobId: string): Promise<ApiResponse<Candidate[]>> {
  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates/shortlisted?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function swipeCandidate(payload: SwipePayload): Promise<ApiResponse<SwipeData>> {
  return requestApi<SwipeData>({
    url: `${API_BASE_URL}/candidates/swipe`,
    method: "POST",
    payload
  });
}

export async function exportCandidates(payload: ExportPayload): Promise<ApiResponse<ExportData>> {
  return requestApi<ExportData>({
    url: `${API_BASE_URL}/candidates/export`,
    method: "POST",
    payload: payload.provider ? payload : { jobId: payload.jobId, candidateIds: payload.candidateIds }
  });
}

export async function getFirstSelectionBatch(jobId: string): Promise<ApiResponse<SelectionResponse>> {
  return requestApi<SelectionResponse>({
    url: `${API_BASE_URL}/candidates/selection/first?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function getNextSelectionBatch(jobId: string): Promise<ApiResponse<SelectionResponse>> {
  return requestApi<SelectionResponse>({
    url: `${API_BASE_URL}/candidates/selection/next?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function submitSelectionChoice(payload: SelectionPayload): Promise<ApiResponse<SelectionResponse>> {
  return requestApi<SelectionResponse>({
    url: `${API_BASE_URL}/candidates/selection`,
    method: "POST",
    payload
  });
}

export async function getFinalSelectionResults(jobId: string): Promise<ApiResponse<SelectionResponse>> {
  return requestApi<SelectionResponse>({
    url: `${API_BASE_URL}/candidates/selection/final?jobId=${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}
