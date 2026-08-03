/**
 * What this file does:
 * Provides client wrappers for the super-admin portal APIs.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

export type AdminDashboard = {
  totalAgencies: number;
  totalUsers: number;
  activeUsers: number;
  inactiveUsers: number;
  totalJobs: number;
  totalCandidates: number;
};

export type AgencyRecord = {
  id: string;
  name: string;
  slug: string;
  status: string;
  createdAt: string | null;
  updatedAt: string | null;
  totalUsers: number;
  totalJobs: number;
  totalCandidates: number;
};

export type UserRecord = {
  id: string;
  name: string;
  email: string;
  agencyId: string;
  agencyName: string;
  role: "SUPER_ADMIN" | "AGENCY_USER" | string;
  status: string;
  createdAt: string | null;
  updatedAt: string | null;
};

export type Pagination = {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
};

export type AgencyListResponse = {
  items: AgencyRecord[];
  pagination: Pagination;
};

export type UserListResponse = {
  items: UserRecord[];
  pagination: Pagination;
};

export type AgencyUpsertPayload = {
  name: string;
};

export type AgencyUpdatePayload = {
  name?: string;
  isActive?: boolean;
};

export type UserUpsertPayload = {
  agencyId: string;
  name: string;
  email: string;
  role?: "SUPER_ADMIN" | "AGENCY_USER" | string;
  isActive?: boolean;
};

export type UserUpdatePayload = {
  agencyId?: string;
  name?: string;
  email?: string;
  role?: "SUPER_ADMIN" | "AGENCY_USER" | string;
  isActive?: boolean;
};

function toAgencyRecord(item: Record<string, unknown>): AgencyRecord {
  return {
    id: String(item.id || ""),
    name: String(item.name || ""),
    slug: String(item.slug || ""),
    status: String(item.status || ""),
    createdAt: (item.created_at as string | null) ?? (item.createdAt as string | null) ?? null,
    updatedAt: (item.updated_at as string | null) ?? (item.updatedAt as string | null) ?? null,
    totalUsers: Number(item.total_users ?? item.totalUsers ?? 0),
    totalJobs: Number(item.total_jobs ?? item.totalJobs ?? 0),
    totalCandidates: Number(item.total_candidates ?? item.totalCandidates ?? 0),
  };
}

function toUserRecord(item: Record<string, unknown>): UserRecord {
  return {
    id: String(item.id || ""),
    name: String(item.name || ""),
    email: String(item.email || ""),
    agencyId: String(item.agency_id || item.agencyId || ""),
    agencyName: String(item.agency_name || item.agencyName || ""),
    role: String(item.role || "AGENCY_USER"),
    status: String(item.status || ""),
    createdAt: (item.created_at as string | null) ?? (item.createdAt as string | null) ?? null,
    updatedAt: (item.updated_at as string | null) ?? (item.updatedAt as string | null) ?? null,
  };
}

function toAdminDashboard(item: Record<string, unknown>): AdminDashboard {
  return {
    totalAgencies: Number(item.total_agencies ?? item.totalAgencies ?? 0),
    totalUsers: Number(item.total_users ?? item.totalUsers ?? 0),
    activeUsers: Number(item.active_users ?? item.activeUsers ?? 0),
    inactiveUsers: Number(item.inactive_users ?? item.inactiveUsers ?? 0),
    totalJobs: Number(item.total_jobs ?? item.totalJobs ?? 0),
    totalCandidates: Number(item.total_candidates ?? item.totalCandidates ?? 0),
  };
}

function toPagination(item: Record<string, unknown>): Pagination {
  return {
    page: Number(item.page ?? 1),
    pageSize: Number(item.page_size ?? item.pageSize ?? 20),
    total: Number(item.total ?? 0),
    totalPages: Number(item.total_pages ?? item.totalPages ?? 0),
  };
}

export async function getAdminDashboard(): Promise<ApiResponse<AdminDashboard>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/dashboard`,
    method: "GET",
  });
  return { ...response, data: response.data ? toAdminDashboard(response.data) : null };
}

export async function getAdminAgencies(params: {
  search?: string;
  status?: string;
  page?: number;
  pageSize?: number;
} = {}): Promise<ApiResponse<AgencyListResponse>> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  if (params.status) query.set("status", params.status);
  if (params.page) query.set("page", String(params.page));
  if (params.pageSize) query.set("pageSize", String(params.pageSize));
  const response = await requestApi<{ items: Record<string, unknown>[]; pagination: Record<string, unknown> }>({
    url: `${API_BASE_URL}/admin/agencies${query.toString() ? `?${query.toString()}` : ""}`,
    method: "GET",
  });
  return {
    ...response,
    data: response.data
      ? {
          items: (response.data.items || []).map(toAgencyRecord),
          pagination: toPagination(response.data.pagination || {}),
        }
      : null,
  };
}

export async function getAllAgencies(): Promise<ApiResponse<AgencyRecord[]>> {
  const response = await requestApi<Record<string, unknown>[]>({
    url: `${API_BASE_URL}/admin/agencies/all`,
    method: "GET",
  });
  return {
    ...response,
    data: response.data ? response.data.map(toAgencyRecord) : null,
  };
}

export async function createAgency(payload: AgencyUpsertPayload): Promise<ApiResponse<AgencyRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/agencies`,
    method: "POST",
    payload,
  });
  return { ...response, data: response.data ? toAgencyRecord(response.data) : null };
}

export async function updateAgency(agencyId: string, payload: AgencyUpdatePayload): Promise<ApiResponse<AgencyRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/agencies/${encodeURIComponent(agencyId)}`,
    method: "PATCH",
    payload,
  });
  return { ...response, data: response.data ? toAgencyRecord(response.data) : null };
}

export async function deactivateAgency(agencyId: string): Promise<ApiResponse<AgencyRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/agencies/${encodeURIComponent(agencyId)}/deactivate`,
    method: "POST",
  });
  return { ...response, data: response.data ? toAgencyRecord(response.data) : null };
}

export async function reactivateAgency(agencyId: string): Promise<ApiResponse<AgencyRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/agencies/${encodeURIComponent(agencyId)}/reactivate`,
    method: "POST",
  });
  return { ...response, data: response.data ? toAgencyRecord(response.data) : null };
}

export async function getAdminUsers(params: {
  search?: string;
  agencyId?: string;
  role?: string;
  status?: string;
  page?: number;
  pageSize?: number;
} = {}): Promise<ApiResponse<UserListResponse>> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  if (params.agencyId) query.set("agencyId", params.agencyId);
  if (params.role) query.set("role", params.role);
  if (params.status) query.set("status", params.status);
  if (params.page) query.set("page", String(params.page));
  if (params.pageSize) query.set("pageSize", String(params.pageSize));
  const response = await requestApi<{ items: Record<string, unknown>[]; pagination: Record<string, unknown> }>({
    url: `${API_BASE_URL}/admin/users${query.toString() ? `?${query.toString()}` : ""}`,
    method: "GET",
  });
  return {
    ...response,
    data: response.data
      ? {
          items: (response.data.items || []).map(toUserRecord),
          pagination: toPagination(response.data.pagination || {}),
        }
      : null,
  };
}

export async function createUser(payload: UserUpsertPayload): Promise<ApiResponse<UserRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/users`,
    method: "POST",
    payload,
  });
  return { ...response, data: response.data ? toUserRecord(response.data) : null };
}

export async function updateUser(userId: string, payload: UserUpdatePayload): Promise<ApiResponse<UserRecord>> {
  const response = await requestApi<Record<string, unknown>>({
    url: `${API_BASE_URL}/admin/users/${encodeURIComponent(userId)}`,
    method: "PATCH",
    payload,
  });
  return { ...response, data: response.data ? toUserRecord(response.data) : null };
}
