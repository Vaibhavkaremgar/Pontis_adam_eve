/**
 * What this file does:
 * Fetches recruiter-intelligence orchestration state and persists voice transcripts.
 *
 * What API it connects to:
 * GET/POST /recruiters/{recruiterId}/intelligence/jobs/{jobId}
 *
 * How it fits in the pipeline:
 * Bridges the adaptive voice interview and intent summary state.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type RecruiterIntelligenceSession = {
  voice_intake_summary?: string;
  voice_intake_transcript?: string;
  interview: {
    job_id?: string;
    recruiter_id?: string;
    stage?: string;
    status?: string;
    gap_analysis?: {
      missing_fields?: string[];
      ambiguous_fields?: string[];
      confidence_scores?: Record<string, number>;
      missing_preferences?: string[];
      recommended_questions?: string[];
    };
    recommended_questions?: string[];
    voice_summary?: string;
    transcript?: string;
    voice_transcript?: string;
    intent_profile?: Record<string, unknown>;
    telemetry?: Record<string, number>;
    current_question?: string;
    stage_summary?: string;
  };
};

export type RecruiterIntelligenceUpdatePayload = {
  jobId: string;
  transcript: string;
  voiceSummary?: string;
  entities?: Record<string, unknown>;
};

export async function getRecruiterIntelligence(
  recruiterId: string,
  jobId: string
): Promise<ApiResponse<RecruiterIntelligenceSession>> {
  return requestApi<RecruiterIntelligenceSession>({
    url: `${API_BASE_URL.replace(/\/$/, "")}/recruiters/${encodeURIComponent(recruiterId)}/intelligence/jobs/${encodeURIComponent(jobId)}`,
    method: "GET"
  });
}

export async function updateRecruiterIntelligence(
  recruiterId: string,
  jobId: string,
  payload: RecruiterIntelligenceUpdatePayload
): Promise<ApiResponse<RecruiterIntelligenceSession>> {
  return requestApi<RecruiterIntelligenceSession>({
    url: `${API_BASE_URL.replace(/\/$/, "")}/recruiters/${encodeURIComponent(recruiterId)}/intelligence/jobs/${encodeURIComponent(jobId)}`,
    method: "POST",
    payload: {
      ...payload,
      jobId
    }
  });
}
