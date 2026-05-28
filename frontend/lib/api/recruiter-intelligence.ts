/**
 * What this file does:
 * Fetches recruiter-intelligence orchestration state and persists voice transcripts.
 *
 * What API it connects to:
 * GET/POST /recruiters/{recruiterId}/intelligence/jobs/{jobId}
 *
 * How it fits in the pipeline:
 * Bridges the adaptive voice interview, intent summary, and comparison-round session state.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type RecruiterIntelligenceSession = {
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
    intent_profile?: Record<string, unknown>;
    telemetry?: Record<string, number>;
    current_question?: string;
    stage_summary?: string;
  };
  selection: {
    status?: string;
    stage?: string;
    current_calibration_set_id?: string;
    rounds?: Array<{
      round_index: number;
      calibration_set_id?: string;
      candidate_ids: string[];
      candidates: Array<Record<string, unknown>>;
      signal_quality: number;
      contrast_axes: string[];
      rationale: string;
      pair_explanation?: Record<string, unknown>;
    }>;
    current_pair?: Record<string, unknown>;
    intent_profile?: Record<string, unknown>;
    recommended_questions?: string[];
    telemetry?: Record<string, number>;
    voice_summary?: string;
  };
  calibration?: {
    status?: string;
    stage?: string;
    current_round_index?: number;
    current_calibration_set_id?: string;
    orchestration_session_id?: string;
    rounds?: Array<{
      round_index: number;
      calibration_set_id?: string;
      candidate_ids: string[];
      candidates: Array<Record<string, unknown>>;
      signal_quality: number;
      contrast_axes: string[];
      rationale: string;
      pair_explanation?: Record<string, unknown>;
    }>;
    current_pair?: Record<string, unknown>;
    current_profile_set?: Record<string, unknown>;
    intent_profile?: Record<string, unknown>;
    recommended_questions?: string[];
    telemetry?: Record<string, number>;
    voice_summary?: string;
    archetype_sets?: Array<Record<string, unknown>>;
    profile_sets?: Array<Record<string, unknown>>;
    candidate_profile_sets?: Array<Record<string, unknown>>;
  };
};

export type RecruiterIntelligenceUpdatePayload = {
  jobId: string;
  transcript: string;
  voiceSummary?: string;
  entities?: Record<string, unknown>;
};

export type RecruiterCalibrationChoicePayload = {
  jobId: string;
  candidateId: string;
  calibrationSetId?: string;
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

export async function chooseRecruiterCalibrationArchetype(
  recruiterId: string,
  jobId: string,
  payload: RecruiterCalibrationChoicePayload
): Promise<ApiResponse<RecruiterIntelligenceSession>> {
  return requestApi<RecruiterIntelligenceSession>({
    url: `${API_BASE_URL.replace(/\/$/, "")}/recruiters/${encodeURIComponent(recruiterId)}/intelligence/jobs/${encodeURIComponent(jobId)}/choice`,
    method: "POST",
    payload: {
      ...payload,
      jobId
    }
  });
}
