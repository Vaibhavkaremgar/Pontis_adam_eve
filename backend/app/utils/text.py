from __future__ import annotations

import math


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return []

    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for index in range(0, len(normalized), step):
        chunk = normalized[index : index + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if index + chunk_size >= len(normalized):
            break
    return chunks


def average_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dims = len(vectors[0])
    totals = [0.0] * dims
    for vector in vectors:
        for idx, value in enumerate(vector):
            totals[idx] += float(value)
    return [value / len(vectors) for value in totals]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))

    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

