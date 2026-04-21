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
    aiReasoning: str = ""


class CandidateResult(BaseModel):
    id: str
    name: str
    role: str
    company: str
    skills: list[str]
    summary: str
    fitScore: float
    decision: str
    explanation: CandidateExplanation
    strategy: str
    status: str = "new"
    outreachStatus: str = "pending"
    exportStatus: str = "pending"


class OutreachRequest(BaseModel):
    jobId: str
    selectedCandidates: list[str]


class OutreachData(BaseModel):
    message: str


class InterviewItem(BaseModel):
    candidateId: str
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
    provider: Literal["merge"] = "merge"


class CandidateExportData(BaseModel):
    provider: str
    status: str
    exportedCount: int
    reference: str
