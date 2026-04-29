from __future__ import annotations

from pydantic import BaseModel


class CompanySaveRequest(BaseModel):
    name: str
    website: str
    description: str = ""
    industry: str = ""


class CompanySaveResponse(BaseModel):
    id: str
    name: str
    website: str
    description: str
    industry: str
    ats_provider: str = ""
    ats_connected: bool = False
