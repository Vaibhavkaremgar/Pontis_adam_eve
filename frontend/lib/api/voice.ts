/**
 * What this file does:
 * Submits full voice conversation transcript for job refinement.
 *
 * What API it connects to:
 * POST /voice/refine
 *
 * How it fits in the pipeline:
 * Frontend sends the full structured conversation (both Maya and recruiter turns)
 * so backend has complete context for LLM extraction and job re-embedding.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type VoiceRefinePayload = {
  jobId: string;
  voiceNotes: string[];
  transcript: string; // full "Maya: ...\nRecruiter: ..." conversation
};

type VoiceRefineData = {
  refined: boolean;
  usedFallback?: boolean;
  job?: {
    title: string;
    description: string;
    location: string;
    compensation: string;
    skills_required: string[];
    responsibilities: string[];
    experience_level: string;
  };
  extraction?: {
    success: boolean;
    confidence: number;
    fields: string[];
  };
};

export async function refineWithVoice(payload: VoiceRefinePayload): Promise<ApiResponse<VoiceRefineData>> {
  return requestApi<VoiceRefineData>({
    url: `${API_BASE_URL}/voice/refine`,
    method: "POST",
    payload
  });
}
