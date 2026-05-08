/**
 * What this file does:
 * Provides thin wrappers for internal admin/ops APIs.
 *
 * How it fits in the pipeline:
 * Exposes diagnostics, queue replay, outreach analytics, and audit visibility to the admin UI.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type AdminDiagnostics = Record<string, unknown>;
export type DeadLetterItem = Record<string, unknown>;
export type OutreachAnalytics = Record<string, unknown>;
export type AuditLogItem = Record<string, unknown>;

export async function getAdminDiagnostics(): Promise<ApiResponse<AdminDiagnostics>> {
  return requestApi<AdminDiagnostics>({
    url: `${API_BASE_URL}/admin/diagnostics`,
    method: "GET"
  });
}

export async function getDeadLetters(queueType?: string): Promise<ApiResponse<DeadLetterItem[]>> {
  const params = queueType ? `?queueType=${encodeURIComponent(queueType)}` : "";
  return requestApi<DeadLetterItem[]>({
    url: `${API_BASE_URL}/admin/queue/deadletters${params}`,
    method: "GET"
  });
}

export async function replayDeadLetter(queueType: string, jobId: string): Promise<ApiResponse<Record<string, unknown>>> {
  return requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/queue/deadletters/replay?queueType=${encodeURIComponent(queueType)}&jobId=${encodeURIComponent(jobId)}`,
    method: "POST"
  });
}

export async function getOutreachAnalytics(jobId?: string): Promise<ApiResponse<OutreachAnalytics>> {
  const params = jobId ? `?jobId=${encodeURIComponent(jobId)}` : "";
  return requestApi<OutreachAnalytics>({
    url: `${API_BASE_URL}/admin/outreach/analytics${params}`,
    method: "GET"
  });
}

export async function getAuditLogs(limit = 50): Promise<ApiResponse<AuditLogItem[]>> {
  return requestApi<AuditLogItem[]>({
    url: `${API_BASE_URL}/admin/audit?limit=${encodeURIComponent(String(limit))}`,
    method: "GET"
  });
}
