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
