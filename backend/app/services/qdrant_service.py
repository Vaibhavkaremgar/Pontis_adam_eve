from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.config import CANDIDATE_COLLECTION_NAME, JOB_COLLECTION_NAME, QDRANT_API_KEY, QDRANT_URL, VECTOR_SIZE
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None
_client_disabled = False
_last_search_error_at: datetime | None = None
_last_search_error_message: str = ""
QDRANT_ERROR_COOLDOWN_SECONDS = 180


def _get_client() -> QdrantClient | None:
    global _client, _client_disabled

    if _client_disabled:
        return None
    if _client is not None:
        return _client

    try:
        _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
        _client.get_collections()
        return _client
    except Exception as exc:
        logger.warning("Qdrant unavailable; vector operations are running in no-op mode", exc_info=exc)
        log_metric("error", source="qdrant", kind="connection_unavailable")
        _client_disabled = True
        return None


def ensure_collection(name: str) -> None:
    client = _get_client()
    if not client:
        return
    try:
        if client.collection_exists(name):
            return
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
    except Exception as exc:
        logger.warning("Failed to ensure Qdrant collection '%s'", name, exc_info=exc)


def ensure_all_collections() -> None:
    ensure_collection(JOB_COLLECTION_NAME)
    ensure_collection(CANDIDATE_COLLECTION_NAME)


def delete_job_vectors(job_id: str) -> None:
    client = _get_client()
    if not client:
        return
    try:
        ensure_collection(JOB_COLLECTION_NAME)
        client.delete(
            collection_name=JOB_COLLECTION_NAME,
            points_selector=Filter(must=[FieldCondition(key="jobId", match=MatchValue(value=job_id))]),
        )
    except Exception as exc:
        logger.warning("Failed to delete job vectors for jobId=%s", job_id, exc_info=exc)


def upsert_job_chunks(job_id: str, vectors: list[list[float]], chunks: list[str]) -> None:
    client = _get_client()
    if not client:
        return
    ensure_collection(JOB_COLLECTION_NAME)
    points: list[PointStruct] = []
    for idx, (vector, chunk) in enumerate(zip(vectors, chunks)):
        points.append(
            PointStruct(
                id=_stable_point_id(f"job:{job_id}:{idx}"),
                vector=vector,
                payload={"jobId": job_id, "chunkIndex": idx, "text": chunk},
            )
        )
    if points:
        try:
            client.upsert(collection_name=JOB_COLLECTION_NAME, points=points, wait=True)
        except Exception as exc:
            logger.warning("Failed to upsert job vectors for jobId=%s", job_id, exc_info=exc)


def delete_candidate_vectors(job_id: str) -> None:
    client = _get_client()
    if not client:
        return
    try:
        ensure_collection(CANDIDATE_COLLECTION_NAME)
        client.delete(
            collection_name=CANDIDATE_COLLECTION_NAME,
            points_selector=Filter(must=[FieldCondition(key="jobId", match=MatchValue(value=job_id))]),
        )
    except Exception as exc:
        logger.warning("Failed to delete candidate vectors for jobId=%s", job_id, exc_info=exc)


def upsert_candidate_chunks(job_id: str, candidate_id: str, vectors: list[list[float]], chunks: list[str], payload: dict[str, Any]) -> None:
    client = _get_client()
    if not client:
        return
    ensure_collection(CANDIDATE_COLLECTION_NAME)
    points: list[PointStruct] = []
    for idx, (vector, chunk) in enumerate(zip(vectors, chunks)):
        point_payload = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "chunkIndex": idx,
            "text": chunk,
            **payload,
        }
        points.append(
            PointStruct(
                id=_stable_point_id(f"cand:{job_id}:{candidate_id}:{idx}"),
                vector=vector,
                payload=point_payload,
            )
        )
    if points:
        try:
            client.upsert(collection_name=CANDIDATE_COLLECTION_NAME, points=points, wait=True)
        except Exception as exc:
            logger.warning(
                "Failed to upsert candidate vectors for jobId=%s candidateId=%s",
                job_id,
                candidate_id,
                exc_info=exc,
            )


def _normalize_filter_value(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(part for part in value.strip().lower().split() if part)


def _metadata_filter(metadata_filters: dict[str, Any] | None) -> Filter | None:
    if not metadata_filters:
        return None

    must: list[FieldCondition] = []
    role = _normalize_filter_value(metadata_filters.get("role"))
    company = _normalize_filter_value(metadata_filters.get("company"))
    location = _normalize_filter_value(metadata_filters.get("location"))
    preferred_skills = [
        _normalize_filter_value(str(item))
        for item in (metadata_filters.get("preferredSkills") or [])
        if _normalize_filter_value(str(item))
    ]
    preferred_roles = [
        _normalize_filter_value(str(item))
        for item in (metadata_filters.get("preferredRoles") or [])
        if _normalize_filter_value(str(item))
    ]
    should: list[FieldCondition] = []

    if role:
        must.append(FieldCondition(key="roleNorm", match=MatchValue(value=role)))
    if company:
        must.append(FieldCondition(key="companyNorm", match=MatchValue(value=company)))
    if location:
        must.append(FieldCondition(key="locationNorm", match=MatchValue(value=location)))
    for skill in preferred_skills[:4]:
        should.append(FieldCondition(key="skillTokens", match=MatchValue(value=skill)))
    for preferred_role in preferred_roles[:2]:
        should.append(FieldCondition(key="rolePattern", match=MatchValue(value=preferred_role)))

    if not must and not should:
        return None
    return Filter(must=must, should=should)


def _mark_search_error(message: str) -> None:
    global _last_search_error_at, _last_search_error_message
    _last_search_error_at = datetime.now(timezone.utc)
    _last_search_error_message = message


def _clear_search_error() -> None:
    global _last_search_error_at, _last_search_error_message
    _last_search_error_at = None
    _last_search_error_message = ""


def is_qdrant_search_error_active() -> bool:
    if _last_search_error_at is None:
        return False
    expires_at = _last_search_error_at + timedelta(seconds=QDRANT_ERROR_COOLDOWN_SECONDS)
    return datetime.now(timezone.utc) <= expires_at


def last_qdrant_search_error() -> str:
    return _last_search_error_message


def _normalize_points(response: Any) -> list[Any]:
    points = getattr(response, "points", None)
    if isinstance(points, list):
        return points
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        dict_points = response.get("points")
        if isinstance(dict_points, list):
            return dict_points
    return []


def search_candidate_chunks(
    *,
    query_vector: list[float],
    limit: int = 60,
    metadata_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    client = _get_client()
    if not client:
        return []

    ensure_collection(CANDIDATE_COLLECTION_NAME)
    resolved_limit = max(1, limit)
    query_filter = _metadata_filter(metadata_filters)

    logger.debug(
        "Qdrant search called collection=%s vector_length=%s limit=%s filter_enabled=%s",
        CANDIDATE_COLLECTION_NAME,
        len(query_vector),
        resolved_limit,
        bool(query_filter),
    )

    results: list[Any] = []
    try:
        try:
            response = client.query_points(
                collection_name=CANDIDATE_COLLECTION_NAME,
                query=query_vector,
                limit=resolved_limit,
                with_payload=True,
                with_vectors=False,
                query_filter=query_filter,
            )
        except TypeError:
            response = client.query_points(
                collection_name=CANDIDATE_COLLECTION_NAME,
                query=query_vector,
                limit=resolved_limit,
                with_payload=True,
                with_vectors=False,
            )
        results = _normalize_points(response)
    except AttributeError:
        # Optional compatibility fallback for clients that only expose search().
        try:
            results = list(
                client.search(
                    collection_name=CANDIDATE_COLLECTION_NAME,
                    query_vector=query_vector,
                    limit=resolved_limit,
                    with_payload=True,
                    with_vectors=False,
                    query_filter=query_filter,
                )
            )
        except Exception as exc:
            log_metric("error", source="qdrant", kind="search_failed_fallback_path")
            _mark_search_error(str(exc))
            logger.warning("Qdrant search failed (fallback path)", exc_info=exc)
            return []
    except Exception as exc:
        log_metric("error", source="qdrant", kind="search_failed")
        _mark_search_error(str(exc))
        logger.warning("Qdrant search failed", exc_info=exc)
        return []

    if not results and query_filter is not None:
        try:
            try:
                response = client.query_points(
                    collection_name=CANDIDATE_COLLECTION_NAME,
                    query=query_vector,
                    limit=resolved_limit,
                    with_payload=True,
                    with_vectors=False,
                )
                results = _normalize_points(response)
            except AttributeError:
                results = list(
                    client.search(
                        collection_name=CANDIDATE_COLLECTION_NAME,
                        query_vector=query_vector,
                        limit=resolved_limit,
                        with_payload=True,
                        with_vectors=False,
                    )
                )
        except Exception as exc:
            log_metric("error", source="qdrant", kind="search_failed_without_filters")
            _mark_search_error(str(exc))
            logger.warning("Qdrant search failed without metadata filters", exc_info=exc)
            return []

    logger.debug("Qdrant search returned results_count=%s", len(results))
    _clear_search_error()

    rows: list[dict[str, Any]] = []
    for item in results:
        payload = getattr(item, "payload", None) or {}
        rows.append(
            {
                "id": str(getattr(item, "id", "") or ""),
                "score": float(getattr(item, "score", 0.0) or 0.0),
                "candidateId": str(payload.get("candidateId") or ""),
                "jobId": str(payload.get("jobId") or ""),
                "payload": payload,
            }
        )
    return rows


def _stable_point_id(seed: str) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)
