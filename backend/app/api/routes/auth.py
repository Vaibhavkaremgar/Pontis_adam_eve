from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.user import GoogleLoginRequest
from app.services.auth_service import login_with_google_token, request_otp, verify_otp
from app.utils.responses import success_response

router = APIRouter(tags=["auth"])


class OtpRequestPayload(BaseModel):
    email: str


class OtpVerifyPayload(BaseModel):
    email: str
    otp: str


@router.post("/auth/request-otp")
def request_otp_route(payload: OtpRequestPayload, db: Session = Depends(get_db)):
    result = request_otp(db=db, email=payload.email)
    return success_response(result)


@router.post("/auth/verify-otp")
def verify_otp_route(payload: OtpVerifyPayload, db: Session = Depends(get_db)):
    data = verify_otp(db=db, email=payload.email, otp=payload.otp)
    return success_response(data.model_dump())


@router.post("/auth/google")
def login_google(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    data = login_with_google_token(db=db, token=payload.token)
    return success_response(data.model_dump())
