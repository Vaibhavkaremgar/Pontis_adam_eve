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
    bookingUrl: str = ""


class InterviewBookingRequest(BaseModel):
    token: str
    scheduledAt: str | None = None


class InterviewBookingData(BaseModel):
    token: str
    status: str
    jobId: str
    candidateId: str
