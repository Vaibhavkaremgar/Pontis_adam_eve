from pydantic import BaseModel


class Company(BaseModel):
    name: str
    website: str
    description: str


class Job(BaseModel):
    id: str
    title: str
    description: str
    location: str
    compensation: str
    workAuthorization: str


class JobInput(BaseModel):
    title: str
    description: str
    location: str
    compensation: str
    workAuthorization: str


class JobCreatePayload(BaseModel):
    company: Company
    job: JobInput


class JobCreateResponse(BaseModel):
    jobId: str


class VoiceRefineRequest(BaseModel):
    voiceNotes: list[str]
    jobId: str

