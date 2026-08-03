from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    provider: str = "email"


class GoogleLoginRequest(BaseModel):
    token: str


class UserProfile(BaseModel):
    id: str
    email: str
    name: str = ""
    picture: str = ""
    provider: str = "email"
    role: str = "AGENCY_USER"
    agency_id: str | None = None


class LoginData(BaseModel):
    user: UserProfile
    token: str
    access_token: str
