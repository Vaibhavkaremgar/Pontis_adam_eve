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
  role?: "recruiter" | "internal_ops" | "admin" | string;
  provider?: "email" | "google";
  name?: string;
  picture?: string;
};

/** Company context captured in step 1 and sent with hiring create payload. */
export type Company = {
  name: string;
  website: string;
  description: string;
  industry?: string;
  atsProvider?: string;
  atsConnected?: boolean;
};

/** Job brief captured in step 2 and used to trigger backend embedding pipeline. */
export type Job = {
  title: string;
  description: string;
  location: string;
  compensation: string;
  workAuthorization: "required" | "preferred" | "not-required";
  remotePolicy?: string;
  experienceRequired?: string;
  vettingMode?: "volume" | "elite";
  autoExportToAts?: boolean;
};

/** Candidate record returned by candidate search endpoint. */
export type Candidate = {
  id: string;
  name: string;
  role: string;
  company: string;
  email?: string;
  isMockEmail?: boolean;
  headline?: string;
  location?: string;
  yearsExperience?: number;
  skills: string[];
  summary: string;
  education?: string[];
  projects?: string[];
  certifications?: string[];
  companiesHistory?: string[];
  domainExperience?: string[];
  resumeText?: string;
  profileData?: Record<string, unknown>;
  fitScore: number;
  decision: "strong_match" | "potential" | "weak";
  explanation?: {
    semantic?: number;
    semanticScore?: number;
    skills_match?: string[];
    skillsMatched?: string[];
    experienceMatch?: string;
    candidateExperience?: string;
    jobExperience?: string;
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
    sourceBreakdown?: {
      vector?: number;
      lexical?: number;
      structured?: number;
      recruiterPreference?: number;
      freshness?: number;
      selectionRound?: number;
      voiceInterview?: number;
    };
    recruiterPreferenceInfluence?: number;
    voiceInterviewInfluence?: number;
    lexicalRetrievalInfluence?: number;
    vectorRetrievalInfluence?: number;
    freshnessInfluence?: number;
    selectionRoundInfluence?: number;
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
  status:
    | "new"
    | "sourced"
    | "reviewed"
    | "selected"
    | "enriching"
    | "enriched"
    | "enrichment_failed"
    | "outreach_pending"
    | "outreach_sent"
    | "replied_interested"
    | "replied_not_interested"
    | "interview_requested"
    | "interview_scheduled"
    | "interview_no_show"
    | "no_show"
    | "interview_completed"
    | "advanced"
    | "second_round_requested"
    | "second_round_scheduled"
    | "second_round_reschedule_requested"
    | "final_round"
    | "offer_stage"
    | "archived"
    | "rejected"
    | "offer_sent"
    | "placed"
    | "search_closed"
    | "placed"
    | "hired";
  ats_status?: string;
  ats_status_source?: string;
  ats_status_reason?: string;
  ats_status_updated_at?: string;
  outreachStatus?: "pending" | "dry_run" | "sent" | "failed" | string;
  enrichmentStatus?: "pending" | "resolving" | "enriched" | "partial" | "failed" | "no_contact_found" | string;
  enrichmentSource?: string;
  enrichmentConfidence?: number;
  contactEmail?: string;
  contactPhone?: string;
  exportStatus?: "pending" | "queued" | "exported" | "failed" | string;
  ats_export_status?: "sent" | "failed" | "not_sent" | string;
  sourceProvider?: string;
  sourceQuery?: string;
  sourceTimestamp?: string;
  sourceType?: string;
  linkedinUrl?: string;
  currentCompany?: string;
  inferredExperience?: string;
};

/** Interview stage record shown in final ready step. */
export type InterviewStatus = {
  candidateId: string;
  name: string;
  status:
    | "selected"
    | "outreach_pending"
    | "outreach_sent"
    | "interview_requested"
    | "interview_scheduled"
    | "interview_in_progress"
    | "rejected"
    | "advanced"
    | "second_round_requested"
    | "second_round_scheduled"
    | "second_round_reschedule_requested"
    | "final_round"
    | "offer_stage"
    | "offer_sent"
    | "placed"
    | "hired"
    | "search_closed"
    | "archived"
    | "interview_completed"
    | "interview_no_show"
    | "new"
    | "reviewed"
    | "evaluation_processing"
    | "results_ready";
};

export type CandidateSelectionAnalysis = {
  skillsOverlap: Array<{ skill: string; count: number }>;
  experienceTrends: {
    averageYears: number;
    minimumYears: number;
    maximumYears: number;
    sampleSize: number;
  };
  companySimilarities: {
    topCompanies: Array<{ company: string; count: number }>;
  };
  roleAlignment: {
    topRoles: Array<{ role: string; count: number }>;
  };
  preferenceSignals: {
    sharedSkills: string[];
    sharedRoles: string[];
    sharedCompanies: string[];
  };
  summary: string;
};

export type CandidateSelectionSession = {
  sessionId: string;
  jobId: string;
  status: string;
  currentBatchIndex: number;
  totalBatches: number;
  batchSize: number;
  selectedCandidateIds: string[];
  rejectedCandidateIds: string[];
  currentBatch: Candidate[];
  analysis?: CandidateSelectionAnalysis | null;
  completed: boolean;
  finalCandidates: Candidate[];
  topCandidates?: Candidate[];
  stage?: string;
  recommendedQuestions?: string[];
  gapAnalysis?: {
    missing_fields?: string[];
    ambiguous_fields?: string[];
    confidence_scores?: Record<string, number>;
    missing_preferences?: string[];
    recommended_questions?: string[];
  };
  intentProfile?: {
    required_skills?: string[];
    preferred_skills?: string[];
    seniority_weight?: number;
    startup_weight?: number;
    domain_weight?: number;
    leadership_weight?: number;
    infra_weight?: number;
    culture_preferences?: string[];
    hiring_biases?: Record<string, unknown>;
    recruiter_preference_embedding?: number[];
    preference_text?: string;
    voice_summary?: string;
    selection_round_count?: number;
    profile_hash?: string;
  };
  currentPair?: {
    round_index?: number;
    candidate_ids?: string[];
    candidates?: Candidate[];
    signal_quality?: number;
    contrast_axes?: string[];
    rationale?: string;
    pair_explanation?: Record<string, unknown>;
  };
  telemetry?: {
    preference_learning_gain?: number;
    rerank_precision_gain?: number;
    pair_signal_quality?: number;
    recruiter_preference_confidence?: number;
  };
  voiceSummary?: string;
  pairExplanation?: Record<string, unknown>;
  warning?: string;
};
