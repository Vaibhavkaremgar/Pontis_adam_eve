import logging
from collections.abc import Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app.core.config import COLLECTION_NAME, QDRANT_SEARCH_LIMIT, QDRANT_URL, VECTOR_SIZE

client = QdrantClient(url=QDRANT_URL)
logger = logging.getLogger(__name__)


def _validate_vector(vector: list[float]) -> None:
    if len(vector) != VECTOR_SIZE:
        raise ValueError(f"Expected vector size {VECTOR_SIZE}, received {len(vector)}")


def create_collection() -> None:
    if not COLLECTION_NAME:
        return

    if client.collection_exists(collection_name=COLLECTION_NAME):
        collection_info = client.get_collection(collection_name=COLLECTION_NAME)
        params = getattr(getattr(collection_info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        existing_size = getattr(vectors, "size", None)
        existing_distance = getattr(vectors, "distance", None)

        if existing_size != VECTOR_SIZE or existing_distance != Distance.COSINE:
            raise ValueError(
                "Qdrant collection config mismatch: "
                f"expected size={VECTOR_SIZE}, distance={Distance.COSINE}; "
                f"received size={existing_size}, distance={existing_distance}"
            )
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE,
        ),
    )


def insert_vector(id: str, vector: list[float], payload: dict) -> None:
    _validate_vector(vector)
    create_collection()
    if not COLLECTION_NAME:
        return
    print(f"Candidate embedding length: {len(vector)}")
    logger.info("Upserting vector into Qdrant", extra={"point_id": id, "embedding_size": len(vector)})
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            {
                "id": id,
                "vector": vector,
                "payload": payload,
            }
        ],
        wait=True,
    )


def _raw_qdrant_results(results: Any) -> Sequence[Any]:
    if isinstance(results, Sequence):
        return results

    for attr in ("points", "result"):
        value = getattr(results, attr, None)
        if isinstance(value, Sequence):
            return value

    return []


def search_vector(vector: list[float], limit: int = QDRANT_SEARCH_LIMIT) -> list[dict]:
    _validate_vector(vector)
    create_collection()
    if not COLLECTION_NAME:
        return []

    print(f"Job embedding length: {len(vector)}")
    logger.info("Searching Qdrant with vector", extra={"embedding_size": len(vector), "limit": limit})

    try:
        if hasattr(client, "query_points"):
            results = client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        else:
            results = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
    except Exception:
        logger.exception("Qdrant vector search failed")
        return []

    raw_results = _raw_qdrant_results(results)
    serialized_results = [
        {
            "id": getattr(point, "id", None),
            "score": getattr(point, "score", 0.0),
            "payload": getattr(point, "payload", {}) or {},
        }
        for point in raw_results
    ]
    print(f"Raw Qdrant results: {serialized_results}")
    logger.info("Raw Qdrant search results", extra={"results": serialized_results})

    return [
        {
            "id": getattr(r, "id", None),
            "score": getattr(r, "score", 0.0),
            "payload": getattr(r, "payload", {}) or {},
        }
        for r in raw_results
        if getattr(r, "payload", None)
    ]
