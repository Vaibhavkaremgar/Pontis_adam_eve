/**
 * What this file does:
 * Provides helpers to read/write auth-visible UI state and pipeline state in browser storage.
 *
 * Storage strategy:
 * - user profile: localStorage (UI restoration only)
 * - Pipeline state (jobId, job, company, isRefined): sessionStorage (tab-scoped, cleared on new session)
 *
 * How it fits in the pipeline:
 * Keeps lightweight UI persistence centralized while auth now uses httpOnly cookies.
 */
import type { Candidate, Company, Job, User } from "@/types";

const USER_KEY = "pontis_user";
const JOB_ID_KEY = "pontis_job_id";
const JOB_KEY = "pontis_job";
const COMPANY_KEY = "pontis_company";
const IS_REFINED_KEY = "pontis_is_refined";
const SHORTLIST_JOB_ID_KEY = "pontis_shortlist_job_id";
const SHORTLIST_IDS_KEY = "pontis_shortlist_ids";
const SHORTLIST_CANDIDATES_KEY = "pontis_shortlist_candidates";

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function storeSession(user: User) {
  if (typeof window === "undefined") return;
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(USER_KEY);
  clearPipelineState();
}

export function storePipelineState(state: {
  jobId?: string;
  job?: Job;
  company?: Company;
  isRefined?: boolean;
}) {
  if (typeof window === "undefined") return;
  if (state.jobId !== undefined) sessionStorage.setItem(JOB_ID_KEY, state.jobId);
  if (state.job !== undefined) sessionStorage.setItem(JOB_KEY, JSON.stringify(state.job));
  if (state.company !== undefined) sessionStorage.setItem(COMPANY_KEY, JSON.stringify(state.company));
  if (state.isRefined !== undefined) sessionStorage.setItem(IS_REFINED_KEY, String(state.isRefined));
}

export function getStoredPipelineState(): {
  jobId: string;
  job: Job | null;
  company: Company | null;
  isRefined: boolean;
} {
  if (typeof window === "undefined") {
    return { jobId: "", job: null, company: null, isRefined: false };
  }
  const jobId = sessionStorage.getItem(JOB_ID_KEY) || "";
  const isRefined = sessionStorage.getItem(IS_REFINED_KEY) === "true";

  let job: Job | null = null;
  try {
    const raw = sessionStorage.getItem(JOB_KEY);
    if (raw) job = JSON.parse(raw) as Job;
  } catch {
    // ignore
  }

  let company: Company | null = null;
  try {
    const raw = sessionStorage.getItem(COMPANY_KEY);
    if (raw) company = JSON.parse(raw) as Company;
  } catch {
    // ignore
  }

  return { jobId, job, company, isRefined };
}

export function clearPipelineState() {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(JOB_ID_KEY);
  sessionStorage.removeItem(JOB_KEY);
  sessionStorage.removeItem(COMPANY_KEY);
  sessionStorage.removeItem(IS_REFINED_KEY);
}

export function storeShortlistedCandidateIds(jobId: string, candidateIds: string[]) {
  if (typeof window === "undefined") return;
  const normalizedIds = Array.from(new Set((candidateIds || []).map((id) => String(id).trim()).filter(Boolean)));
  sessionStorage.setItem(SHORTLIST_JOB_ID_KEY, jobId);
  sessionStorage.setItem(SHORTLIST_IDS_KEY, JSON.stringify(normalizedIds));
}

export function getStoredShortlistedCandidateIds(jobId: string): string[] {
  if (typeof window === "undefined") return [];
  const storedJobId = sessionStorage.getItem(SHORTLIST_JOB_ID_KEY) || "";
  if (!storedJobId || storedJobId !== jobId) return [];

  try {
    const raw = sessionStorage.getItem(SHORTLIST_IDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return Array.from(new Set(parsed.map((id) => String(id).trim()).filter(Boolean)));
  } catch {
    return [];
  }
}

export function clearShortlistedCandidateIds() {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(SHORTLIST_JOB_ID_KEY);
  sessionStorage.removeItem(SHORTLIST_IDS_KEY);
  sessionStorage.removeItem(SHORTLIST_CANDIDATES_KEY);
}

export function storeShortlistedCandidates(jobId: string, candidates: Candidate[]) {
  if (typeof window === "undefined") return;
  const normalizedCandidates = Array.from(
    new Map(
      (candidates || [])
        .filter((candidate): candidate is Candidate => Boolean(candidate?.id))
        .map((candidate) => [String(candidate.id), candidate] as const)
    ).values()
  );
  sessionStorage.setItem(SHORTLIST_JOB_ID_KEY, jobId);
  sessionStorage.setItem(SHORTLIST_CANDIDATES_KEY, JSON.stringify(normalizedCandidates));
}

export function getStoredShortlistedCandidates(jobId: string): Candidate[] {
  if (typeof window === "undefined") return [];
  const storedJobId = sessionStorage.getItem(SHORTLIST_JOB_ID_KEY) || "";
  if (!storedJobId || storedJobId !== jobId) return [];

  try {
    const raw = sessionStorage.getItem(SHORTLIST_CANDIDATES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((candidate): candidate is Candidate => Boolean(candidate && typeof candidate === "object" && "id" in candidate));
  } catch {
    return [];
  }
}
