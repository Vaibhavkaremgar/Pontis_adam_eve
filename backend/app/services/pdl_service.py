import json
import logging
import os

import requests
from dotenv import load_dotenv

from app.core.config import HTTP_TIMEOUT_SECONDS, PDL_SEARCH_SIZE, PDL_URL

load_dotenv()

API_KEY = os.getenv("PDL_API_KEY")
logger = logging.getLogger(__name__)


def _run_person_search(es_query: dict, size: int) -> dict:
    if not API_KEY:
        logger.warning("PDL_API_KEY is not configured; returning empty candidate set")
        return {"data": []}

    params = {
        "api_key": API_KEY,
        "query": json.dumps(es_query),
        "size": size,
    }

    logger.info("Calling PDL person search", extra={"size": size})
    response = requests.get(PDL_URL, params=params, timeout=HTTP_TIMEOUT_SECONDS)

    if response.status_code != 200:
        logger.error("PDL request failed", extra={"status_code": response.status_code, "response": response.text})
        raise Exception(f"PDL Error: {response.text}")

    return response.json()


def fetch_candidates(query, size):
    es_query = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"job_title": query}}
                ]
            }
        }
    }

    return _run_person_search(es_query=es_query, size=size)


def fetch_candidates_with_filters(filters: dict, size: int | None = None) -> dict:
    size = size or PDL_SEARCH_SIZE

    role = (filters.get("role") or "").strip()
    skills = [str(skill).strip().lower() for skill in (filters.get("skills") or []) if str(skill).strip()]
    location = (filters.get("location") or "").strip().lower()
    primary_skill = skills[0] if skills else ""

    query_variants: list[dict] = []

    if role:
        must_filters = [{"match": {"job_title": role}}]
        if primary_skill:
            must_filters.append({"match": {"skills": primary_skill}})
        if location:
            must_filters.append({"match": {"location_country": location}})
        query_variants.append(
            {
                "query": {
                    "bool": {
                        "must": must_filters,
                    }
                }
            }
        )

        if primary_skill:
            query_variants.append(
                {
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {"job_title": role}},
                                {"match": {"skills": primary_skill}},
                            ],
                        }
                    }
                }
            )

        query_variants.append(
            {
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"job_title": role}}
                        ],
                    }
                }
            }
        )

    if not query_variants:
        return {"data": []}

    logger.info("PDL filters", extra={"filters": {"role": role, "skill": primary_skill, "location": location}})

    for index, es_query in enumerate(query_variants, start=1):
        logger.info("PDL final ES query", extra={"attempt": index, "query": es_query})
        response = _run_person_search(es_query=es_query, size=size)
        logger.info("PDL response", extra={"attempt": index, "response": response})

        candidates = response.get("data", []) if isinstance(response, dict) else []
        logger.info("PDL candidate count", extra={"attempt": index, "count": len(candidates)})
        if candidates:
            return response

    return response if "response" in locals() else {"data": []}
