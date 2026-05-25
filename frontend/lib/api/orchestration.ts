/**
 * Slack orchestration API client.
 *
 * This is additive to the existing hiring/voice flow. It only powers the
 * Slack handoff path opened via /voice?token=...
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type OrchestrationSession = {
  id: string;
  sessionToken: string;
  source: string;
  sourceType?: string;
  currentStage: string;
  slackTeamId: string;
  slackChannelId: string;
  slackThreadTs: string;
  slackUserId: string;
  intakeMode: string;
  selectedPath: string;
  currentQuestion: string;
  currentQuestionKey: string;
  structuredContext: Record<string, unknown>;
  rawConversation: Array<Record<string, unknown>>;
  normalizedIntake: Record<string, unknown>;
  voiceContext: Record<string, unknown>;
  slackContext: Record<string, unknown>;
  voiceHandoffToken: string;
  voiceHandoffExpiresAt: string | null;
  voiceHandoffConsumedAt: string | null;
  voiceTokenUsed: boolean;
  expiresAt: string | null;
  completedAt: string | null;
  companyId: string | null;
  jobId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export type OrchestrationVoiceStartData = {
  token: string;
  tokenExpiresAt: string;
  session: OrchestrationSession;
  firstMessage: string;
  variableValues: Record<string, string>;
  currentQuestion: string;
  currentQuestionKey: string;
  confidence: number;
};

export type OrchestrationVoiceCompleteData = {
  completed: boolean;
  session: OrchestrationSession;
  nextQuestion?: string;
  nextQuestionKey?: string;
  questionConfidence?: number;
  finalization?: {
    jobId: string;
    companyId: string;
    candidatesDelivered: Array<{ candidateId: string; posted: boolean }>;
  };
};

export async function getOrchestrationSession(token: string): Promise<ApiResponse<OrchestrationSession>> {
  return requestApi<OrchestrationSession>({
    url: `${API_BASE_URL}/slack/orchestration/sessions/${encodeURIComponent(token)}`,
    method: "GET",
  });
}

export async function startOrchestrationVoice(token: string): Promise<ApiResponse<OrchestrationVoiceStartData>> {
  return requestApi<OrchestrationVoiceStartData>({
    url: `${API_BASE_URL}/slack/orchestration/voice/start/${encodeURIComponent(token)}`,
    method: "POST",
  });
}

export async function completeOrchestrationVoice(
  token: string,
  payload: { transcript: string; voiceNotes: string[] }
): Promise<ApiResponse<OrchestrationVoiceCompleteData>> {
  return requestApi<OrchestrationVoiceCompleteData>({
    url: `${API_BASE_URL}/slack/orchestration/voice/complete/${encodeURIComponent(token)}`,
    method: "POST",
    payload,
  });
}
