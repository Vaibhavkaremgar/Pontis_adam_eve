from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResultRecordingData(BaseModel):
    sessionToken: str = ""
    recordingPath: str = ""
    videoAvailable: bool = False


class ResultScoresData(BaseModel):
    overall: float = 0.0
    technical: float = 0.0
    communication: float = 0.0
    cultureFit: float = 0.0


class ResultCandidateData(BaseModel):
    id: str = ""
    name: str = ""
    role: str = ""
    company: str = ""
    headline: str = ""
    location: str = ""
    email: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    source: str = ""


class ResultWorkspaceData(BaseModel):
    candidate: ResultCandidateData = Field(default_factory=ResultCandidateData)
    recording: ResultRecordingData = Field(default_factory=ResultRecordingData)
    transcript: str = ""
    summary: str = ""
    scores: ResultScoresData = Field(default_factory=ResultScoresData)
    decision: str = ""
    status: str = ""
    timeline: dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    analysis: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    engagement: dict[str, Any] = Field(default_factory=dict)


class ResultListItem(BaseModel):
    candidateId: str = ""
    name: str = ""
    status: str = ""
    workflowToken: str = ""
    score: float = 0.0
    recommendation: str = ""
    completionState: str = ""
    videoAvailable: bool = False
    currentStage: str = ""
    connectionStatus: str = ""
    invitationStatus: str = ""
    currentProgress: str = ""
    sourceCategory: str = ""


class ResultListData(BaseModel):
    jobId: str = ""
    recruiterId: str = ""
    candidates: list[ResultListItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
