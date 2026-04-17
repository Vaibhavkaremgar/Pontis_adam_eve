/**
 * What this file does:
 * Submits recruiter voice notes for refinement.
 *
 * What API it connects to:
 * POST /voice/refine
 *
 * How it fits in the pipeline:
 * Frontend sends notes and jobId; backend applies AI/refinement logic and updates search state.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type VoiceRefinePayload = {
  jobId: string;
  voiceNotes: string[];
};

type VoiceRefineData = {
  refined: boolean;
};

/** This function calls backend API and returns structured response. */
export async function refineWithVoice(payload: VoiceRefinePayload): Promise<ApiResponse<VoiceRefineData>> {
  return requestApi<VoiceRefineData>({
    url: `${API_BASE_URL}/voice/refine`,
    method: "POST",
    payload
  });
}
