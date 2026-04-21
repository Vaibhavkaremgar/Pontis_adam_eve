import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.auth_middleware import auth_middleware
from app.core.config import CORS_ALLOW_ORIGINS, missing_secret_warnings
from app.db.session import db_health_snapshot, init_db
from app.services.candidate_service import warm_candidate_retrieval
from app.services.metrics_service import get_metrics_snapshot
from app.services.openai_service import openai_health_snapshot
from app.services.pdl_service import pdl_health_snapshot, run_startup_connectivity_check
from app.services.refresh_scheduler import scheduler_status, start_scheduler, stop_scheduler
from app.utils.exceptions import APIError
from app.utils.responses import error_response, success_response

logger = logging.getLogger(__name__)
app = FastAPI()
app.middleware("http")(auth_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return success_response({"message": "Backend is running"})


@app.get("/health")
def health():
    db_status = db_health_snapshot()
    pdl_status = pdl_health_snapshot()
    openai_status = openai_health_snapshot()
    scheduler = scheduler_status()

    overall = "ok"
    if any(
        value.get("status") in {"down", "degraded", "unconfigured"}
        for value in [db_status, pdl_status, openai_status]
    ):
        overall = "degraded"
    if db_status.get("status") == "down":
        overall = "down"

    return success_response(
        {
            "status": overall,
            "services": {
                "db": db_status,
                "pdl": pdl_status,
                "openai": openai_status,
                "scheduler": scheduler,
            },
        }
    )


@app.get("/metrics")
def metrics():
    return success_response(get_metrics_snapshot())


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    for warning in missing_secret_warnings():
        logger.warning("configuration_warning %s", warning)
    run_startup_connectivity_check()
    warm_candidate_retrieval()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()


@app.exception_handler(APIError)
def api_error_handler(_: Request, exc: APIError):
    return JSONResponse(status_code=exc.status_code, content=error_response(exc.message))


@app.exception_handler(RequestValidationError)
def validation_error_handler(_: Request, exc: RequestValidationError):
    first_error = exc.errors()[0] if exc.errors() else {}
    message = str(first_error.get("msg") or "Invalid request")
    return JSONResponse(status_code=400, content=error_response(message))


@app.exception_handler(HTTPException)
def http_error_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=error_response(str(exc.detail)))


@app.exception_handler(Exception)
def unhandled_error_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content=error_response("Internal server error"))


app.include_router(api_router)
