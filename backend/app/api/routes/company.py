from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.repositories import CompanyRepository
from app.db.session import get_db
from app.schemas.company import CompanySaveRequest
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(tags=["company"])


def _company_status_payload(company):
    return {
        "id": getattr(company, "id", ""),
        "name": getattr(company, "name", "") or "",
        "website": getattr(company, "website", "") or "",
        "description": getattr(company, "description", "") or "",
        "industry": getattr(company, "industry", "") or "",
        "ats_connected": bool(getattr(company, "ats_connected", False)),
        "ats_provider": getattr(company, "ats_provider", "") or "",
        "atsConnected": bool(getattr(company, "ats_connected", False)),
        "atsProvider": getattr(company, "ats_provider", "") or "",
    }


@router.get("/company")
def get_company_status(
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = request.state.user["id"]
    company = CompanyRepository(db).get_latest_for_user(user_id=user_id)
    if not company:
        raise APIError("Company not found", status_code=404)

    return success_response(_company_status_payload(company))


@router.post("/company/save")
def save_company(
    payload: CompanySaveRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = request.state.user["id"]
    company = CompanyRepository(db).upsert_for_user(
        user_id=user_id,
        name=payload.name,
        website=payload.website,
        description=payload.description,
        industry=payload.industry,
    )
    db.commit()
    return success_response(
        {
            "id": company.id,
            "name": company.name,
            "website": company.website,
            "description": company.description,
            "industry": company.industry,
            **_company_status_payload(company),
        }
    )
