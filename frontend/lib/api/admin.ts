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
export type PipelineBoard = Record<string, unknown>;
export type PipelineAnalytics = Record<string, unknown>;
export type NotificationItem = Record<string, unknown>;
export type AutomationJobItem = Record<string, unknown>;
export type TaskItem = Record<string, unknown>;
export type NoteItem = Record<string, unknown>;
export type OperationalIntelligence = Record<string, unknown>;

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

export async function getPipelineBoard(jobId?: string): Promise<ApiResponse<PipelineBoard>> {
  const params = jobId ? `?jobId=${encodeURIComponent(jobId)}` : "";
  return requestApi<PipelineBoard>({
    url: `${API_BASE_URL}/admin/pipeline/board${params}`,
    method: "GET"
  });
}

export async function getPipelineAnalytics(jobId?: string): Promise<ApiResponse<PipelineAnalytics>> {
  const params = jobId ? `?jobId=${encodeURIComponent(jobId)}` : "";
  return requestApi<PipelineAnalytics>({
    url: `${API_BASE_URL}/admin/pipeline/analytics${params}`,
    method: "GET"
  });
}

export async function getNotificationCenter(jobId?: string): Promise<ApiResponse<NotificationItem[]>> {
  const params = jobId ? `?jobId=${encodeURIComponent(jobId)}` : "";
  return requestApi<NotificationItem[]>({
    url: `${API_BASE_URL}/admin/notifications${params}`,
    method: "GET"
  });
}

export async function markNotificationRead(notificationKey: string): Promise<ApiResponse<Record<string, unknown>>> {
  return requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/notifications/read?notificationKey=${encodeURIComponent(notificationKey)}`,
    method: "POST"
  });
}

export async function getAutomationJobs(): Promise<ApiResponse<AutomationJobItem[]>> {
  return requestApi<AutomationJobItem[]>({
    url: `${API_BASE_URL}/admin/automation/jobs`,
    method: "GET"
  });
}

export async function getRecruiterTasks(jobId?: string): Promise<ApiResponse<TaskItem[]>> {
  const params = jobId ? `?jobId=${encodeURIComponent(jobId)}` : "";
  return requestApi<TaskItem[]>({
    url: `${API_BASE_URL}/admin/tasks${params}`,
    method: "GET"
  });
}

export async function getRecruiterNotes(jobId: string, candidateId?: string): Promise<ApiResponse<NoteItem[]>> {
  const params = `?jobId=${encodeURIComponent(jobId)}${candidateId ? `&candidateId=${encodeURIComponent(candidateId)}` : ""}`;
  return requestApi<NoteItem[]>({
    url: `${API_BASE_URL}/admin/notes${params}`,
    method: "GET"
  });
}

export async function getOperationalIntelligence(jobId?: string, candidateId?: string): Promise<ApiResponse<OperationalIntelligence>> {
  const params = `${jobId ? `?jobId=${encodeURIComponent(jobId)}` : ""}${candidateId ? `${jobId ? "&" : "?"}candidateId=${encodeURIComponent(candidateId)}` : ""}`;
  return requestApi<OperationalIntelligence>({
    url: `${API_BASE_URL}/admin/intelligence${params}`,
    method: "GET"
  });
}
