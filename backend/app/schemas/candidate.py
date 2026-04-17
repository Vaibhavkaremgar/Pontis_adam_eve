from pydantic import BaseModel


class Candidate(BaseModel):
    name: str
    job_title: str
    company: str
    skills: list[str]
    score: float
    status: str


class OutreachRequest(BaseModel):
    jobId: str
    candidates: list[Candidate]


class OutreachResponse(BaseModel):
    success: bool


class InterviewRequest(BaseModel):
    jobId: str
    candidate: Candidate


class InterviewResponse(BaseModel):
    scheduled: bool
