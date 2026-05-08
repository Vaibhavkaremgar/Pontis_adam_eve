from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import List
from threading import Lock

from sentence_transformers import SentenceTransformer

from app.core.config import EMBEDDING_MODEL_NAME, VECTOR_SIZE
from app.services.metrics_service import log_metric
from app.services.persistent_cache_service import get_json, set_json

_model: SentenceTransformer | None = None
_model_failed_until: datetime | None = None
_model_failure_reason = ""
_model_lock = Lock()
logger = logging.getLogger(__name__)
EMBEDDING_MODEL_RETRY_COOLDOWN_SECONDS = 1800
_SAMPLE_EMBEDDING_TEXTS = [
    "Senior backend engineer with Python, FastAPI, PostgreSQL, and AWS experience.",
    "Machine learning engineer focused on recommendation systems and retrieval ranking.",
    "Full stack developer skilled in React, TypeScript, Node.js, and microservices.",
    "Data engineer with Spark, Airflow, and large-scale ETL pipeline expertise.",
    "Product designer and frontend engineer with accessibility and performance focus.",
]


def _get_model() -> SentenceTransformer:
    global _model

    if _model_failed_until and datetime.now(timezone.utc) < _model_failed_until:
        raise RuntimeError(_model_failure_reason or "embedding model unavailable")

    if _model is None:
        with _model_lock:
            if _model is None:
                _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _disable_model(reason: str) -> None:
    global _model_failed_until, _model_failure_reason
    _model_failed_until = datetime.now(timezone.utc) + timedelta(seconds=EMBEDDING_MODEL_RETRY_COOLDOWN_SECONDS)
    _model_failure_reason = reason
    logger.warning("embedding_model_disabled reason=%s retry_at=%s", reason, _model_failed_until.isoformat())


def embed(text: str) -> List[float]:
    safe_text = text.strip() if text else ""
    cache_key = hashlib.sha256((safe_text or " ").encode("utf-8")).hexdigest()
    cached = get_json("embeddings", cache_key)
    if isinstance(cached, list) and cached:
        log_metric("embedding_usage", cache_hit=True, vector_size=len(cached), model=EMBEDDING_MODEL_NAME)
        return [float(value) for value in cached]

    try:
        embedding = _get_model().encode(safe_text or " ")
        vector = embedding.tolist()
        set_json("embeddings", cache_key, vector)
        log_metric("embedding_usage", cache_hit=False, vector_size=len(vector), model=EMBEDDING_MODEL_NAME)
        return vector
    except Exception as exc:
        if _model_failed_until is None or datetime.now(timezone.utc) >= _model_failed_until:
            _disable_model(str(exc))
        logger.warning("embedding_fallback_used reason=%s", str(exc))
        vector = _fallback_embedding(safe_text or " ")
        set_json("embeddings", cache_key, vector)
        log_metric("embedding_usage", cache_hit=False, vector_size=len(vector), model="fallback")
        return vector


def embed_many(texts: list[str]) -> list[list[float]]:
    cleaned = [text.strip() if text else "" for text in texts]
    if not cleaned:
        return []

    results: list[list[float] | None] = [None] * len(cleaned)
    missing_indices: list[int] = []
    missing_texts: list[str] = []
    for index, text in enumerate(cleaned):
        cache_key = hashlib.sha256((text or " ").encode("utf-8")).hexdigest()
        cached = get_json("embeddings", cache_key)
        if isinstance(cached, list) and cached:
            results[index] = [float(value) for value in cached]
        else:
            missing_indices.append(index)
            missing_texts.append(text)

    if missing_texts:
        try:
            embeddings = _get_model().encode([text or " " for text in missing_texts])
            if getattr(embeddings, "ndim", 1) == 1:
                embeddings = [embeddings]
            for index, embedding in zip(missing_indices, embeddings):
                vector = embedding.tolist()
                results[index] = vector
                cache_key = hashlib.sha256((cleaned[index] or " ").encode("utf-8")).hexdigest()
                set_json("embeddings", cache_key, vector)
                log_metric("embedding_usage", cache_hit=False, vector_size=len(vector), model=EMBEDDING_MODEL_NAME)
        except Exception as exc:
            logger.warning("embedding_batch_fallback_used reason=%s", str(exc))
            for index in missing_indices:
                vector = _fallback_embedding(cleaned[index] or " ")
                results[index] = vector
                cache_key = hashlib.sha256((cleaned[index] or " ").encode("utf-8")).hexdigest()
                set_json("embeddings", cache_key, vector)
                log_metric("embedding_usage", cache_hit=False, vector_size=len(vector), model="fallback")

    return [vector if vector is not None else _fallback_embedding(cleaned[index] or " ") for index, vector in enumerate(results)]


def get_embedding(text: str) -> list[float]:
    return embed(text)


def _fallback_embedding(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    vector = [rng.uniform(-1.0, 1.0) for _ in range(VECTOR_SIZE)]
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0:
        return [0.0] * VECTOR_SIZE
    return [value / norm for value in vector]


def preload_sample_candidate_embeddings() -> int:
    preloaded = 0
    for text in _SAMPLE_EMBEDDING_TEXTS:
        try:
            embed(text)
            preloaded += 1
        except Exception as exc:
            logger.warning("embedding_preload_failed reason=%s", str(exc))
    logger.info("Preloaded sample candidate embeddings count=%s", preloaded)
    return preloaded
