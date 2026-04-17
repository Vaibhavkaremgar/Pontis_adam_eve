from pydantic import BaseModel
from typing import Optional, List, Any


class Candidate(BaseModel):
    id: int
    name: Optional[str] = None
    role: Optional[str] = None
    skills: Optional[List[str]] = None
    raw_data: Optional[Any] = None   


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    id: int
    score: float
    payload: Candidate