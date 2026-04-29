from pydantic import BaseModel


class Company(BaseModel):
    name: str
    website: str
    description: str
    industry: str = ""


class Job(BaseModel):
    title: str
    description: str
    location: str
    compensation: str
    workAuthorization: str
    remotePolicy: str = ""
    experienceRequired: str = ""
    atsJobId: str = ""
    vettingMode: str = "volume"
    autoExportToAts: bool = False


class JobInput(BaseModel):
    title: str
    description: str
    location: str
    compensation: str
    workAuthorization: str
    remotePolicy: str = ""
    experienceRequired: str = ""
    atsJobId: str = ""
    vettingMode: str = "volume"
    autoExportToAts: bool = False


class JobCreatePayload(BaseModel):
    company: Company
    job: JobInput


class JobCreateResponse(BaseModel):
    jobId: str


class JobParseRequest(BaseModel):
    url: str


class JobParseData(BaseModel):
    title: str = ""
    description: str = ""
    location: str = ""
    compensation: str = ""
    workAuthorization: str = "required"
    remotePolicy: str = "hybrid"
    experienceRequired: str = ""


class JobModeRequest(BaseModel):
    mode: str = "volume"


class JobModeData(BaseModel):
    jobId: str
    mode: str
    strategy: str


class VoiceRefineRequest(BaseModel):
    voiceNotes: list[str]
    jobId: str
    transcript: str = ""  # full conversation string: "Maya: ...\nRecruiter: ..."


class VoiceRefineData(BaseModel):
    refined: bool

