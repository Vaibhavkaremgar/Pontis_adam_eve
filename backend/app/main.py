import logging
import secrets
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.api.routes.slack import router as slack_router
from app.core.auth_middleware import auth_middleware
from app.core.config import APP_ENV, APOLLO_ENRICHMENT_ENABLED, CORS_ALLOW_ORIGINS, INTERNAL_API_KEY, SOURCE_PROVIDER, XRAY_ENABLED, config_diagnostics, missing_secret_warnings, validate_runtime_config
from app.core.rate_limit_middleware import rate_limit_middleware
from app.core.security import verify_access_token
from app.db.session import db_health_snapshot, init_db
from app.services.candidate_service import warm_candidate_retrieval
from app.services.embedding_service import embedding_health_snapshot
from app.services.embedding_registry_service import ensure_embedding_version_registry
from app.services.email_service import email_health_snapshot
from app.services.job_queue_service import queue_health_snapshot, start_job_queue_workers, stop_job_queue_workers
from app.services.metrics_service import get_metrics_snapshot
from app.services.llm_service import llm_health
from app.services.qdrant_service import ensure_qdrant_indexes, qdrant_health_snapshot
from app.services.qdrant_service import close_qdrant_client
from app.services.pdl_service import pdl_health_snapshot
from app.services.serpapi_sourcing_service import serpapi_health_snapshot
from app.services.apollo_enrichment_service import apollo_health_snapshot
from app.services.redis_service import close_redis_client, get_redis
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


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", "").strip() or uuid4().hex
    request.state.request_id = request_id
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{(perf_counter() - started) * 1000:.2f}"
    return response


@app.get("/")
def home():
    return success_response({"message": "Backend is running"})


@app.get("/health/live")
def live_health():
    return {"status": "ok"}


@app.get("/health/ready")
def ready_health():
    db_status = db_health_snapshot()
    redis_status = {"status": "ok" if get_redis() is not None else "degraded"}
    qdrant_status = qdrant_health_snapshot()
    llm_status = llm_health()
    email_status = email_health_snapshot()
    queue_status = queue_health_snapshot()

    overall = "ok"
    if any(
        value.get("status") in {"down", "degraded", "unconfigured", "error"}
        for value in [db_status, redis_status, qdrant_status, llm_status, email_status, queue_status]
    ):
        overall = "degraded"
    if db_status.get("status") == "down":
        overall = "down"

    return success_response(
        {
            "status": overall,
            "services": {
                "db": db_status,
                "redis": redis_status,
                "qdrant": qdrant_status,
                "llm": llm_status,
                "email": email_status,
                "queue": queue_status,
            },
        }
    )


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


@app.get("/api/config/diagnostics")
def config_api(request: Request):
    _authorize_internal_request(request)
    return success_response(config_diagnostics())


@app.on_event("startup")
def on_startup() -> None:
    validation = validate_runtime_config(production_mode=APP_ENV in {"production", "prod"})
    critical_issues = [item for item in validation["issues"] if item["severity"] == "critical"]
    if critical_issues:
        raise RuntimeError(f"Invalid runtime config: {critical_issues}")
    logger.info(
        "startup_sourcing_provider source_provider=%s xray_enabled=%s apollo_enrichment_enabled=%s embedding_model=%s",
        SOURCE_PROVIDER,
        XRAY_ENABLED,
        APOLLO_ENRICHMENT_ENABLED,
        embedding_health_snapshot().get("model") or "",
    )

    try:
        init_db()
    except Exception as exc:
        logger.exception("database_initialization_failed error=%s", str(exc))
        raise

    try:
        if SOURCE_PROVIDER != "xray_apollo":
            try:
                ensure_qdrant_indexes()
            except Exception as exc:
                logger.warning("qdrant_index_initialization_failed error=%s", str(exc))

        for warning in missing_secret_warnings():
            logger.warning("configuration_warning %s", warning)
        for warning in [item for item in validation["issues"] if item["severity"] == "warning"]:
            logger.warning("configuration_warning %s", warning["message"])
        if SOURCE_PROVIDER != "xray_apollo":
            try:
                pdl_snapshot = pdl_health_snapshot()
                logger.info("startup_pdl_status status=%s last_error=%s", pdl_snapshot.get("status", ""), pdl_snapshot.get("last_error", ""))
            except Exception as exc:
                logger.warning("pdl_startup_health_check_failed error=%s", str(exc))
        try:
            ensure_embedding_version_registry()
        except Exception as exc:
            logger.warning("embedding_registry_initialization_failed error=%s", str(exc))
        if SOURCE_PROVIDER == "xray_apollo":
            logger.info("candidate_warmup_skipped reason=xray_apollo")
        else:
            try:
                warm_candidate_retrieval()
            except Exception as exc:
                logger.warning("candidate_warmup_failed error=%s", str(exc))

        if APP_ENV in {"production", "prod"}:
            redis_client = get_redis()
            if redis_client is None:
                raise RuntimeError("Redis unavailable at startup")
            serpapi_status = serpapi_health_snapshot()
            if serpapi_status.get("status") != "ok":
                raise RuntimeError(f"SerpAPI unavailable at startup: {serpapi_status}")
            apollo_status = apollo_health_snapshot()
            if apollo_status.get("status") != "ok":
                raise RuntimeError(f"Apollo unavailable at startup: {apollo_status}")
            embedding_status = embedding_health_snapshot()
            if embedding_status.get("status") != "ok":
                raise RuntimeError(f"Embedding model unavailable at startup: {embedding_status}")
            qdrant_status = qdrant_health_snapshot()
            if qdrant_status.get("status") != "ok":
                raise RuntimeError(f"Qdrant unavailable at startup: {qdrant_status}")
            logger.info(
                "startup_runtime_dependencies_ok redis=ok serpapi=%s apollo=%s embedding=%s qdrant=%s",
                serpapi_status.get("status", ""),
                apollo_status.get("status", ""),
                embedding_status.get("status", ""),
                qdrant_status.get("status", ""),
            )
    finally:
        start_job_queue_workers()
        start_scheduler()
        logger.info("startup_scheduler_started")


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_job_queue_workers()
    stop_scheduler()
    close_redis_client()
    close_qdrant_client()
    try:
        from app.db.session import engine

        engine.dispose()
    except Exception:
        logger.exception("database_shutdown_failed")


@app.exception_handler(APIError)
def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            exc.message,
            code=exc.code,
            category=exc.category,
            retryable=exc.retryable,
            details=exc.details,
            request_id=str(getattr(request.state, "request_id", "") or ""),
        ),
    )


@app.exception_handler(RequestValidationError)
def validation_error_handler(request: Request, exc: RequestValidationError):
    first_error = exc.errors()[0] if exc.errors() else {}
    message = str(first_error.get("msg") or "Invalid request")
    return JSONResponse(
        status_code=400,
        content=error_response(
            message,
            code="validation_error",
            category="validation",
            request_id=str(getattr(request.state, "request_id", "") or ""),
        ),
    )


@app.exception_handler(HTTPException)
def http_error_handler(request: Request, exc: HTTPException):
    status_code = exc.status_code
    return JSONResponse(
        status_code=status_code,
        content=error_response(
            str(exc.detail),
            code=f"http_{status_code}",
            category="http",
            retryable=status_code >= 500,
            request_id=str(getattr(request.state, "request_id", "") or ""),
        ),
    )


@app.exception_handler(Exception)
def unhandled_error_handler(request: Request, __: Exception):
    return JSONResponse(
        status_code=500,
        content=error_response(
            "Internal server error",
            code="internal_error",
            category="system",
            retryable=True,
            request_id=str(getattr(request.state, "request_id", "") or ""),
        ),
    )


app.include_router(api_router)
app.include_router(slack_router)
# Slack can be reached either directly at /slack/* or behind an API prefix at /api/slack/*.
# Keep both mounts so Slack app settings and existing docs/deployments remain compatible.
app.include_router(slack_router, prefix="/api", include_in_schema=False)
