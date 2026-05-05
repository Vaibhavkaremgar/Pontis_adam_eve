import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.api.routes.slack import router as slack_router
from app.core.auth_middleware import auth_middleware
from app.core.config import CORS_ALLOW_ORIGINS, INTERNAL_API_KEY, missing_secret_warnings
from app.core.rate_limit_middleware import rate_limit_middleware
from app.core.security import verify_access_token
from app.db.session import db_health_snapshot, init_db
from app.services.candidate_service import warm_candidate_retrieval
from app.services.metrics_service import get_metrics_snapshot
from app.services.llm_service import llm_health
from app.services.qdrant_service import ensure_qdrant_indexes, qdrant_health_snapshot
from app.services.pdl_service import pdl_health_snapshot, run_startup_connectivity_check
from app.services.refresh_scheduler import scheduler_status, start_scheduler, stop_scheduler
from app.utils.exceptions import APIError
from app.utils.responses import error_response, success_response

logger = logging.getLogger(__name__)
app = FastAPI()
app.middleware("http")(auth_middleware)
app.middleware("http")(rate_limit_middleware)
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


def _authorize_internal_request(request: Request) -> None:
    internal_key = request.headers.get("X-Internal-API-Key", "").strip()
    if INTERNAL_API_KEY and internal_key and secrets.compare_digest(internal_key, INTERNAL_API_KEY):
        return

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        verify_access_token(token)
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def public_health():
    return {"status": "ok"}


@app.get("/api/health")
def health(request: Request):
    _authorize_internal_request(request)
    db_status = db_health_snapshot()
    pdl_status = pdl_health_snapshot()
    qdrant_status = qdrant_health_snapshot()
    llm_status = llm_health()
    scheduler = scheduler_status()

    overall = "ok"
    if any(
        value.get("status") in {"down", "degraded", "unconfigured", "error"}
        for value in [db_status, pdl_status, qdrant_status, llm_status]
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
                "qdrant": qdrant_status,
                "llm": llm_status,
                "openai": llm_status,
                "scheduler": scheduler,
            },
        }
    )


@app.get("/metrics")
def metrics(request: Request):
    _authorize_internal_request(request)
    return success_response(get_metrics_snapshot())


@app.get("/api/metrics")
def metrics_api(request: Request):
    _authorize_internal_request(request)
    return success_response(get_metrics_snapshot())


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_db()
    except Exception as exc:
        logger.exception("database_initialization_failed error=%s", str(exc))
        raise

    try:
        ensure_qdrant_indexes()
    except Exception as exc:
        logger.warning("qdrant_index_initialization_failed error=%s", str(exc), exc_info=exc)

    for warning in missing_secret_warnings():
        logger.warning("configuration_warning %s", warning)
    try:
        run_startup_connectivity_check()
    except Exception as exc:
        logger.warning("pdl_startup_connectivity_check_failed error=%s", str(exc), exc_info=exc)
    try:
        warm_candidate_retrieval()
    except Exception as exc:
        logger.warning("candidate_warmup_failed error=%s", str(exc), exc_info=exc)
    finally:
        start_scheduler()
        logger.info("startup_scheduler_started")


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
app.include_router(slack_router)
