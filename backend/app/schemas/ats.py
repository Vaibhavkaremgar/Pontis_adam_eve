from __future__ import annotations

from pydantic import BaseModel

from app.core.config import DEFAULT_ATS_PROVIDER


class ATSExportRequest(BaseModel):
    candidate_id: str
    job_id: str
    provider: str = DEFAULT_ATS_PROVIDER


class ATSConnectRequest(BaseModel):
    provider: str = DEFAULT_ATS_PROVIDER


class ATSExportResponse(BaseModel):
    exportId: str
    candidateId: str
    jobId: str
    provider: str
    status: str
    externalReference: str = ""
    error: str = ""
    createdAt: str = ""
    existing: bool = False
