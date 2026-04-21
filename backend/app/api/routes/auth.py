from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.user import GoogleLoginRequest, LoginRequest
from app.services.auth_service import login_user, login_with_google_token
from app.utils.responses import success_response

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    data = login_user(db=db, email=payload.email, provider=payload.provider)
    return success_response(data.model_dump())


@router.post("/auth/google")
def login_google(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    data = login_with_google_token(db=db, token=payload.token)
    return success_response(data.model_dump())
