from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import COOKIE_SAMESITE, COOKIE_SECURE, CSRF_COOKIE_NAME, CSRF_TOKEN_TTL_SECONDS, JWT_EXPIRY_DAYS
from app.core.security import clear_auth_cookies, create_csrf_token, set_auth_cookies
from app.db.session import get_db
from app.schemas.user import GoogleLoginRequest
from app.services.audit_service import record_audit_event
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
def verify_otp_route(payload: OtpVerifyPayload, request: Request, db: Session = Depends(get_db)):
    data = verify_otp(db=db, email=payload.email, otp=payload.otp)
    csrf_token = create_csrf_token(user_id=data.user.id)
    resp = JSONResponse(content=success_response(data.model_dump()))
    set_auth_cookies(resp, token=data.access_token or data.token, csrf_token=csrf_token, max_age_seconds=JWT_EXPIRY_DAYS * 24 * 60 * 60)
    record_audit_event(
        db=db,
        actor_id=data.user.id,
        action="login_otp",
        entity_type="auth",
        entity_id=data.user.id,
        metadata={"email": payload.email, "method": "otp"},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return resp


@router.post("/auth/google")
def login_google(payload: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    data = login_with_google_token(db=db, token=payload.token)
    csrf_token = create_csrf_token(user_id=data.user.id)
    resp = JSONResponse(content=success_response(data.model_dump()))
    set_auth_cookies(resp, token=data.access_token or data.token, csrf_token=csrf_token, max_age_seconds=JWT_EXPIRY_DAYS * 24 * 60 * 60)
    record_audit_event(
        db=db,
        actor_id=data.user.id,
        action="login_google",
        entity_type="auth",
        entity_id=data.user.id,
        metadata={"method": "google"},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return resp


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None) or {}
    actor_id = str(user.get("id") or "")
    if actor_id:
        record_audit_event(
            db=db,
            actor_id=actor_id,
            action="logout",
            entity_type="auth",
            entity_id=actor_id,
            metadata={},
            request_id=str(getattr(request.state, "request_id", "") or ""),
        )
        db.commit()
    response = JSONResponse(content=success_response({"loggedOut": True}))
    clear_auth_cookies(response)
    return response


@router.get("/auth/csrf")
def csrf_token(request: Request):
    user_id = ""
    user = getattr(request.state, "user", None) or {}
    user_id = str(user.get("id") or "")
    token = create_csrf_token(user_id=user_id or None)
    resp = JSONResponse(content=success_response({"token": token}))
    resp.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=CSRF_TOKEN_TTL_SECONDS,
    )
    return resp


@router.get("/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "data": None, "error": "Unauthorized"})
    return success_response({"user": user})
