from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateExplanation(BaseModel):
    semanticScore: float
    skillOverlap: float
    finalScore: float
    pdlRelevance: float
    recencyScore: float
    engineeringScore: float = 0.0
    penalties: dict[str, float]
    skillsMatched: list[str] = Field(default_factory=list)
    experienceMatch: str = ""
    candidateExperience: str = ""
    jobExperience: str = ""
    aiReasoning: str = ""
    retrievalAttribution: dict = Field(default_factory=dict)
    sourceBreakdown: dict = Field(default_factory=dict)
    recruiterPreferenceInfluence: float = 0.0
    voiceInterviewInfluence: float = 0.0
    lexicalRetrievalInfluence: float = 0.0
    vectorRetrievalInfluence: float = 0.0
    freshnessInfluence: float = 0.0
    selectionRoundInfluence: float = 0.0


class CandidateRankingDebug(BaseModel):
    existing_score: float
    recruiter_score_raw: float
    recruiter_score_adjusted: float
    session_signal: float
    weights: dict[str, float]
    final_score: float
    recruiter_capped: bool
    experience_bucket: str = ""
    experience_score: float = 0.0


class CandidateResult(BaseModel):
    id: str
    name: str
    role: str | None = None
    company: str | None = None
    email: str | None = None
    isMockEmail: bool = False
    headline: str | None = None
    location: str | None = None
    yearsExperience: float | None = None
    skills: list[str]
    summary: str | None = None
    education: list[str] | None = None
    projects: list[str] | None = None
    certifications: list[str] | None = None
    companiesHistory: list[str] | None = None
    domainExperience: list[str] | None = None
    resumeText: str | None = None
    profileData: dict = Field(default_factory=dict)
    fitScore: float
    decision: str
    explanation: CandidateExplanation
    strategy: str
    status: str = "new"
    debug: CandidateRankingDebug | None = None
    outreachStatus: str = "pending"
    enrichmentStatus: str = "pending"
    enrichmentSource: str = ""
    enrichmentConfidence: float = 0.0
    contactEmail: str = ""
    contactPhone: str = ""
    exportStatus: str = "pending"
    ats_export_status: str = "not_sent"
    sourceProvider: str = ""
    sourceQuery: str = ""
    sourceTimestamp: str = ""
    sourceType: str = ""
    source: str = ""
    source_url: str = ""
    linkedinUrl: str = ""
    githubUrl: str | None = None
    portfolioUrl: str | None = None
    currentCompany: str | None = None
    inferredExperience: str | None = None
    snippetQuality: Literal["rich", "partial", "thin"] = "partial"
    rawDiscovery: dict = Field(default_factory=dict)


class OutreachRequest(BaseModel):
    jobId: str
    selectedCandidates: list[str]
    customBody: str = ""
    recipientEmail: str = ""


class OutreachReplyRequest(BaseModel):
    providerMessageId: str = ""
    jobId: str = ""
    candidateId: str = ""
    rawEvent: dict = Field(default_factory=dict)


class OutreachData(BaseModel):
    message: str


class OutreachReplyData(BaseModel):
    jobId: str = ""
    candidateId: str = ""
    providerMessageId: str = ""
    status: str = "replied"
    intent: str = ""


class InterviewItem(BaseModel):
    candidateId: str
    name: str = ""
    status: str


class SwipeFeedbackRequest(BaseModel):
    jobId: str
    candidateId: str
    action: Literal["accept", "reject", "maybe", "not_now", "pass"]
    reactivateAt: str = ""


class SwipeFeedbackData(BaseModel):
    jobId: str
    candidateId: str
    action: Literal["accept", "reject"]
    message: str


class CandidateExportRequest(BaseModel):
    jobId: str
    candidateIds: list[str] = Field(default_factory=list)
    provider: str = "mock"


class CandidateExportData(BaseModel):
    provider: str
    status: str
    exportedCount: int
    reference: str


class CandidateSelectionRequest(BaseModel):
    jobId: str
    candidateId: str


class CandidateSelectionAnalysis(BaseModel):
    skillsOverlap: list[dict] = Field(default_factory=list)
    experienceTrends: dict = Field(default_factory=dict)
    companySimilarities: dict = Field(default_factory=dict)
    roleAlignment: dict = Field(default_factory=dict)
    preferenceSignals: dict = Field(default_factory=dict)
    summary: str = ""


class CandidateSelectionSessionData(BaseModel):
    sessionId: str
    jobId: str
    status: str
    currentBatchIndex: int
    totalBatches: int
    batchSize: int
    selectedCandidateIds: list[str] = Field(default_factory=list)
    rejectedCandidateIds: list[str] = Field(default_factory=list)
    currentBatch: list[CandidateResult] = Field(default_factory=list)
    analysis: CandidateSelectionAnalysis | None = None
    completed: bool = False
    finalCandidates: list[CandidateResult] = Field(default_factory=list)
    stage: str = ""
    recommendedQuestions: list[str] = Field(default_factory=list)
    gapAnalysis: dict = Field(default_factory=dict)
    intentProfile: dict = Field(default_factory=dict)
    currentPair: dict = Field(default_factory=dict)
    telemetry: dict = Field(default_factory=dict)
    voiceSummary: str = ""
    pairExplanation: dict = Field(default_factory=dict)


class CandidateSelectionBatchData(BaseModel):
    session: CandidateSelectionSessionData


class CandidateSelectionFinalData(BaseModel):
    session: CandidateSelectionSessionData
    topCandidates: list[CandidateResult] = Field(default_factory=list)
    analysis: CandidateSelectionAnalysis | None = None


class InterviewSessionRequest(BaseModel):
    jobId: str
    candidateId: str
    availableSlots: list[str] = Field(default_factory=list)
    timezone: str = "UTC"


class InterviewSessionData(BaseModel):
    id: str
    jobId: str
    candidateId: str
    companyId: str | None = None
    outreachEventId: str | None = None
    sourceType: str = "adam"
    workflowToken: str = ""
    stageName: str = "recruiter_screen"
    stageIndex: int = 0
    bookingStatus: str = "pending"
    email: str = ""
    token: str
    status: str
    expiresAt: str
    bookedAt: str | None = None
    scheduledAt: str | None = None
    timezone: str = "UTC"
    availableSlots: list[str] = Field(default_factory=list)
    interviewer: dict = Field(default_factory=dict)
    candidateName: str = ""
    jobTitle: str = ""
    companyName: str = ""
    stageHistory: list[dict] = Field(default_factory=list)
    bookingLink: str = ""
    bookingUrl: str = ""


class InterviewBookingRequest(BaseModel):
    token: str
    scheduledAt: str | None = None


class InterviewRescheduleRequest(BaseModel):
    token: str
    scheduledAt: str
    reason: str = ""


class InterviewBookingData(BaseModel):
    token: str
    status: str
    jobId: str
    candidateId: str
    sourceType: str = "adam"
    workflowToken: str = ""
    stageName: str = "recruiter_screen"
    scheduledAt: str | None = None
    meetingLink: str = ""
    bookingStatus: str = "confirmed"
    timezone: str = "UTC"
    availableSlots: list[str] = Field(default_factory=list)
    interviewer: dict = Field(default_factory=dict)


class InterviewRescheduleData(BaseModel):
    token: str
    status: str
    jobId: str
    candidateId: str
    sourceType: str = "adam"
    workflowToken: str = ""
    stageName: str = "recruiter_screen"
    scheduledAt: str | None = None
    meetingLink: str = ""
    bookingStatus: str = "confirmed"
    timezone: str = "UTC"
    availableSlots: list[str] = Field(default_factory=list)
    interviewer: dict = Field(default_factory=dict)


class InterviewDecisionRequest(BaseModel):
    jobId: str
    candidateId: str
    action: str
    targetStage: str = ""
    interviewerId: str = ""
    notes: str = ""
    recommendation: str = ""
    sourceType: str = "adam"


class InterviewDecisionData(BaseModel):
    jobId: str
    candidateId: str
    action: str
    currentStage: str = ""
    nextStage: str = ""
    atsStatus: str = ""
    workflowToken: str = ""
    currentSession: dict = Field(default_factory=dict)
    nextSession: dict = Field(default_factory=dict)
    progression: list[dict] = Field(default_factory=list)
    intelligence: dict = Field(default_factory=dict)
    evaluations: list[dict] = Field(default_factory=list)
    decision: dict = Field(default_factory=dict)
    duplicate: bool = False


class InterviewInsightsData(BaseModel):
    jobId: str
    candidateId: str
    currentStage: str = ""
    workflowToken: str = ""
    workflowPayload: dict = Field(default_factory=dict)
    progression: list[dict] = Field(default_factory=list)
    evaluationCount: int = 0
    evaluations: list[dict] = Field(default_factory=list)
    intelligence: dict = Field(default_factory=dict)
    currentSession: dict | None = None
    stageHistory: list[dict] = Field(default_factory=list)
