from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import List
from threading import Lock

from sentence_transformers import SentenceTransformer

from app.core.config import EMBEDDING_MODEL_NAME, VECTOR_SIZE
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
        return [float(value) for value in cached]

    try:
        embedding = _get_model().encode(safe_text or " ")
        vector = embedding.tolist()
        set_json("embeddings", cache_key, vector)
        return vector
    except Exception as exc:
        if _model_failed_until is None or datetime.now(timezone.utc) >= _model_failed_until:
            _disable_model(str(exc))
        logger.warning("embedding_fallback_used reason=%s", str(exc))
        vector = _fallback_embedding(safe_text or " ")
        set_json("embeddings", cache_key, vector)
        return vector


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
