/**
 * What this file does:
 * Connects ATS providers for the current company.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type AtsConnectPayload = {
  provider: string;
};

type AtsConnectData = {
  connected: boolean;
  provider: string;
};

type AtsDisconnectData = {
  connected: boolean;
};

export async function connectAts(payload: AtsConnectPayload): Promise<ApiResponse<AtsConnectData>> {
  return requestApi<AtsConnectData>({
    url: `${API_BASE_URL}/ats/connect`,
    method: "POST",
    payload
  });
}

export async function disconnectAts(): Promise<ApiResponse<AtsDisconnectData>> {
  return requestApi<AtsDisconnectData>({
    url: `${API_BASE_URL}/ats/disconnect`,
    method: "POST"
  });
}

export type AtsTimelineEvent = {
  type: string;
  jobId: string;
  candidateId: string;
  createdAt: string;
  fromStatus?: string | null;
  toStatus?: string | null;
  status?: string | null;
  source?: string | null;
  channel?: string | null;
  recipientType?: string | null;
  recipient?: string | null;
  notificationType?: string | null;
  title?: string | null;
  provider?: string | null;
  providerMessageId?: string | null;
  bookingUrl?: string | null;
  token?: string | null;
  scheduledAt?: string | null;
  lastError?: string | null;
  metadata?: Record<string, unknown>;
};

export type AtsNotification = {
  id: string;
  candidateId: string | null;
  recipientType: string;
  recipient: string;
  channel: string;
  title: string;
  body: string;
  status: string;
  notificationType: string;
  notificationKey: string;
  deliveryReference: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export async function getCandidateAtsTimeline(jobId: string, candidateId: string): Promise<ApiResponse<AtsTimelineEvent[]>> {
  const params = `?jobId=${encodeURIComponent(jobId)}&candidateId=${encodeURIComponent(candidateId)}`;
  return requestApi<AtsTimelineEvent[]>({
    url: `${API_BASE_URL}/ats/timeline${params}`,
    method: "GET"
  });
}

export async function getJobAtsNotifications(jobId: string): Promise<ApiResponse<AtsNotification[]>> {
  const params = `?jobId=${encodeURIComponent(jobId)}`;
  return requestApi<AtsNotification[]>({
    url: `${API_BASE_URL}/ats/notifications${params}`,
    method: "GET"
  });
}
