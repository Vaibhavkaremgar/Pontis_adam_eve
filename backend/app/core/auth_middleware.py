from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.security import verify_access_token
from app.utils.exceptions import APIError
from app.utils.responses import error_response


EXEMPT_PATHS = {
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/auth/login",
    "/api/auth/google",
    "/api/auth/google/callback",
}


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if not path.startswith("/api") or path in EXEMPT_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content=error_response("Missing Authorization header"))

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        claims = verify_access_token(token)
    except APIError as exc:
        return JSONResponse(status_code=exc.status_code, content=error_response(exc.message))

    request.state.user = {"id": claims["sub"], "email": claims.get("email")}
    return await call_next(request)
