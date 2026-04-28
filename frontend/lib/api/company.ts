/**
 * What this file does:
 * Persists company details to the backend so ATS settings can be connected at company scope.
 */
import { API_BASE_URL } from "@/lib/config";
import type { Company } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type CompanySavePayload = Company;

type CompanySaveData = Company & {
  id: string;
  ats_provider?: string;
  ats_connected?: boolean;
  atsProvider?: string;
  atsConnected?: boolean;
};

type CompanyStatusData = {
  id?: string;
  name?: string;
  website?: string;
  description?: string;
  industry?: string;
  ats_provider?: string;
  ats_connected?: boolean;
  atsProvider?: string;
  atsConnected?: boolean;
};

export async function saveCompany(payload: CompanySavePayload): Promise<ApiResponse<CompanySaveData>> {
  return requestApi<CompanySaveData>({
    url: `${API_BASE_URL}/company/save`,
    method: "POST",
    payload
  });
}

export async function getCompany(): Promise<ApiResponse<CompanyStatusData>> {
  return requestApi<CompanyStatusData>({
    url: `${API_BASE_URL}/company`,
    method: "GET"
  });
}
