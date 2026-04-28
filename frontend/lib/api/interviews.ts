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
  expiresAt: string;
  bookedAt: string | null;
  bookingUrl: string;
};

export type InterviewBookingPayload = {
  token: string;
  scheduledAt?: string | null;
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
