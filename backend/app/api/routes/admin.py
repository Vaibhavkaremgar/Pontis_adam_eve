from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.security import require_role
from app.db.admin_repositories import AdminRepository
from app.db.session import get_db
from app.schemas.admin import (
    AgencyCreateRequest,
    AgencyListResponse,
    AgencySummary,
    AgencyUpdateRequest,
    AdminDashboardSummary,
    AdminPagination,
    UserCreateRequest,
    UserListResponse,
    UserSummary,
    UserUpdateRequest,
)
from app.services.admin_service import (
    admin_assert_actor,
    create_admin_agency,
    create_admin_user,
    get_admin_dashboard,
    list_admin_agencies,
    list_admin_users,
    set_admin_agency_status,
    update_admin_agency,
    update_admin_user,
)
from app.utils.responses import success_response

router = APIRouter(prefix="/admin", tags=["admin"])
super_admin_access = Depends(require_role("SUPER_ADMIN"))


def _actor(request: Request) -> dict[str, str]:
    return admin_assert_actor(getattr(request.state, "user", None) or {})


@router.get("/dashboard")
def dashboard(_: dict = super_admin_access, db: Session = Depends(get_db)):
    return success_response(AdminDashboardSummary(**get_admin_dashboard(db=db)).model_dump())


@router.get("/agencies")
def agencies(
    search: str = Query("", alias="search"),
    status: str = Query("", alias="status"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, alias="pageSize", ge=1, le=100),
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    result = list_admin_agencies(db=db, search=search, status=status, page=page, page_size=pageSize)
    return success_response(
        AgencyListResponse(
            items=[AgencySummary(**item) for item in result["items"]],
            pagination=AdminPagination(**result["pagination"]),
        ).model_dump()
    )


@router.post("/agencies")
def create_agency(
    payload: AgencyCreateRequest,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(create_admin_agency(db=db, actor_id=_actor(request)["id"], payload=payload))


@router.patch("/agencies/{agencyId}")
def edit_agency(
    agencyId: str,
    payload: AgencyUpdateRequest,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(update_admin_agency(db=db, actor_id=_actor(request)["id"], agency_id=agencyId, payload=payload))


@router.post("/agencies/{agencyId}/deactivate")
def deactivate_agency(
    agencyId: str,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(set_admin_agency_status(db=db, actor_id=_actor(request)["id"], agency_id=agencyId, is_active=False))


@router.post("/agencies/{agencyId}/reactivate")
def reactivate_agency(
    agencyId: str,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(set_admin_agency_status(db=db, actor_id=_actor(request)["id"], agency_id=agencyId, is_active=True))


@router.get("/users")
def users(
    search: str = Query("", alias="search"),
    agencyId: str = Query("", alias="agencyId"),
    role: str = Query("", alias="role"),
    status: str = Query("", alias="status"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, alias="pageSize", ge=1, le=100),
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    result = list_admin_users(db=db, search=search, agency_id=agencyId, role=role, status=status, page=page, page_size=pageSize)
    return success_response(
        UserListResponse(
            items=[UserSummary(**item) for item in result["items"]],
            pagination=AdminPagination(**result["pagination"]),
        ).model_dump()
    )


@router.post("/users")
def create_user(
    payload: UserCreateRequest,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(create_admin_user(db=db, actor_id=_actor(request)["id"], payload=payload))


@router.patch("/users/{userId}")
def edit_user(
    userId: str,
    payload: UserUpdateRequest,
    request: Request,
    _: dict = super_admin_access,
    db: Session = Depends(get_db),
):
    return success_response(update_admin_user(db=db, actor_id=_actor(request)["id"], user_id=userId, payload=payload))


@router.get("/agencies/all")
def all_agencies(_: dict = super_admin_access, db: Session = Depends(get_db)):
    result = AdminRepository(db).list_agencies(page=1, page_size=1000)
    return success_response(result["items"])
