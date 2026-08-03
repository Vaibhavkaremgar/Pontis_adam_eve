from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdminPagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class AdminDashboardSummary(BaseModel):
    total_agencies: int = 0
    total_users: int = 0
    active_users: int = 0
    inactive_users: int = 0
    total_jobs: int = 0
    total_candidates: int = 0


class AgencySummary(BaseModel):
    id: str
    name: str
    slug: str = ""
    status: str = "Active"
    created_at: str | None = None
    updated_at: str | None = None
    total_users: int = 0
    total_jobs: int = 0
    total_candidates: int = 0
    linkedin_email: str | None = None
    linkedin_connected: bool = False
    linkedin_connected_at: str | None = None
    linkedin_last_verified_at: str | None = None
    linkedin_profile_path: str | None = None
    linkedin_connection_status: str = "pending"


class AgencyListResponse(BaseModel):
    items: list[AgencySummary] = Field(default_factory=list)
    pagination: AdminPagination


class AgencyCreateRequest(BaseModel):
    name: str


class AgencyUpdateRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class UserSummary(BaseModel):
    id: str
    name: str = ""
    email: str
    agency_id: str | None = None
    agency_name: str = ""
    role: str = "AGENCY_USER"
    status: str = "Active"
    created_at: str | None = None
    updated_at: str | None = None


class UserListResponse(BaseModel):
    items: list[UserSummary] = Field(default_factory=list)
    pagination: AdminPagination


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agency_id: str = Field(alias="agencyId")
    name: str = ""
    email: str
    role: str = "AGENCY_USER"
    is_active: bool = Field(default=True, alias="isActive")


class UserUpdateRequest(BaseModel):
    agency_id: str | None = None
    name: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
