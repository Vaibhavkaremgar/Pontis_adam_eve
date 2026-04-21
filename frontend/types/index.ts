/**
 * What this file does:
 * Defines shared frontend data contracts used across pages, context, and API clients.
 *
 * What API it connects to:
 * These types map to payloads/responses for /api/auth/login, /api/hiring/create,
 * /api/candidates, /api/voice/refine, /api/outreach, and /api/interviews.
 *
 * How it fits in the pipeline:
 * Frontend orchestrates recruiter input and API calls with these shapes while backend handles
 * embeddings, vector DB writes, sourcing APIs, and AI ranking.
 */

/** Logged-in recruiter profile from auth service. Auth data lives in a standard DB, not a vector DB. */
export type User = {
  id: string;
  email: string;
  provider?: "email" | "google";
  name?: string;
  picture?: string;
};

/** Company context captured in step 1 and sent with hiring create payload. */
export type Company = {
  name: string;
  website: string;
  description: string;
};

/** Job brief captured in step 2 and used to trigger backend embedding pipeline. */
export type Job = {
  title: string;
  description: string;
  location: string;
  compensation: string;
  workAuthorization: "required" | "preferred" | "not-required";
};

/** Candidate record returned by candidate search endpoint. */
export type Candidate = {
  id: string;
  name: string;
  role: string;
  company: string;
  skills: string[];
  summary: string;
  fitScore: number;
  decision: "strong_match" | "potential" | "weak";
  explanation?: {
    semantic?: number;
    semanticScore?: number;
    skills_match?: string[];
    feedback_boost?: number;
    diversity_bonus?: number;
    exploration_bonus?: number;
    rejection_penalty?: number;
    summary?: string[];
    skillOverlap?: number;
    finalScore?: number;
    pdlRelevance?: number;
    recencyScore?: number;
    aiReasoning?: string;
    penalties?: {
      semanticPenalty?: number;
      missingSkillsPenalty?: number;
      feedbackBonus?: number;
      feedbackBias?: number;
      diversityBonus?: number;
      explorationBonus?: number;
      rejectionPenalty?: number;
      [key: string]: number | undefined;
    };
  };
  strategy: "HIGH" | "MEDIUM" | "LOW";
  status: "new" | "contacted" | "shortlisted" | "interview_scheduled" | "rejected" | "exported";
  outreachStatus?: "pending" | "dry_run" | "sent" | "failed" | string;
  exportStatus?: "pending" | "queued" | "exported" | "failed" | string;
};

/** Interview stage record shown in final ready step. */
export type InterviewStatus = {
  candidateId: string;
  status: "shortlisted" | "contacted" | "interview_scheduled" | "rejected" | "exported" | "new";
};
