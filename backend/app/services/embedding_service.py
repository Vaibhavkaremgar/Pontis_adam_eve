from __future__ import annotations

from threading import Lock

from sentence_transformers import SentenceTransformer
from app.core.config import EMBEDDING_MODEL_NAME

_model: SentenceTransformer | None = None
_model_lock = Lock()


def _get_model() -> SentenceTransformer:
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def get_embedding(text: str) -> list[float]:
    embedding = _get_model().encode(text)
    return embedding.tolist()
