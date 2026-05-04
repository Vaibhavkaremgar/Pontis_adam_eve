from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateExplanation(BaseModel):
    semanticScore: float
    skillOverlap: float
    finalScore: float
    pdlRelevance: float
    recencyScore: float
    penalties: dict[str, float]
    skillsMatched: list[str] = Field(default_factory=list)
    experienceMatch: str = ""
    candidateExperience: str = ""
    jobExperience: str = ""
    aiReasoning: str = ""


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
    role: str
    company: str
    email: str = ""
    isMockEmail: bool = False
    skills: list[str]
    summary: str
    fitScore: float
    decision: str
    explanation: CandidateExplanation
    strategy: str
    status: str = "new"
    debug: CandidateRankingDebug | None = None
    outreachStatus: str = "pending"
    exportStatus: str = "pending"
    ats_export_status: str = "not_sent"


class OutreachRequest(BaseModel):
    jobId: str
    selectedCandidates: list[str]
    customBody: str = ""


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
    action: Literal["accept", "reject"]


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


class CandidateSelectionBatchData(BaseModel):
    session: CandidateSelectionSessionData


class CandidateSelectionFinalData(BaseModel):
    session: CandidateSelectionSessionData
    topCandidates: list[CandidateResult] = Field(default_factory=list)
    analysis: CandidateSelectionAnalysis | None = None


class InterviewSessionRequest(BaseModel):
    jobId: str
    candidateId: str


class InterviewSessionData(BaseModel):
    id: str
    jobId: str
    candidateId: str
    email: str = ""
    token: str
    status: str
    expiresAt: str
    bookedAt: str | None = None
    bookingLink: str = ""
    bookingUrl: str = ""


class InterviewBookingRequest(BaseModel):
    token: str
    scheduledAt: str | None = None


class InterviewBookingData(BaseModel):
    token: str
    status: str
    jobId: str
    candidateId: str
    meetingLink: str = ""
