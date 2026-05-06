from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import ENABLE_PLAYWRIGHT_JOB_PARSER, HTTP_TIMEOUT_SECONDS, GROQ_API_KEY
from app.services.llm_service import generate
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


class _JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self._capture_text = True
        self._capture_script = False
        self._capture_title = False
        self._script_type = ""
        self._script_id = ""
        self._script_buffer: list[str] = []
        self.ld_json_blocks: list[str] = []
        self.json_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        tag_name = tag.lower()
        if tag_name == "meta":
            key = (attr_map.get("name") or attr_map.get("property") or "").strip().lower()
            content = (attr_map.get("content") or "").strip()
            if key and content:
                self.meta[key] = content
        elif tag_name == "script":
            self._capture_script = True
            self._script_type = (attr_map.get("type") or "").strip().lower()
            self._script_id = (attr_map.get("id") or "").strip().lower()
            self._script_buffer = []
        elif tag_name == "title":
            self._capture_title = True
        elif tag_name in {"style", "noscript"}:
            self._capture_text = False
        elif tag_name in {"br", "p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "script":
            script_text = "".join(self._script_buffer).strip()
            if self._capture_script and self._script_type == "application/ld+json" and script_text:
                self.ld_json_blocks.append(script_text)
            if self._capture_script and (
                self._script_type in {"application/json", "text/json"}
                or self._script_id in {"__next_data__", "__nuxt", "__apollo_state__"}
            ) and script_text:
                self.json_blocks.append(script_text)
            self._capture_script = False
            self._script_type = ""
            self._script_id = ""
            self._script_buffer = []
        elif tag_name == "title":
            self._capture_title = False
        elif tag_name in {"style", "noscript"}:
            self._capture_text = True
        elif tag_name in {"p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._capture_script:
            self._script_buffer.append(data)
            return
        if self._capture_title:
            text = unescape(data).strip()
            if text:
                self.title = f"{self.title} {text}".strip()
            return
        if not self._capture_text:
            return
        text = unescape(data).strip()
        if text:
            self.text_parts.append(text)

    def get_text(self) -> str:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(self.text_parts))
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _slug_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return slug.title() if slug else ""


def _source_from_url(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.netloc or "unknown").lower()


@lru_cache(maxsize=1)
def _http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=2,
        connect=2,
        read=1,
        status=2,
        backoff_factor=0.5,
        status_forcelist=(403, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _best_meta(meta: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = _normalize_text(meta.get(key, ""))
        if value:
            return value
    return ""


def _extract_json_ld(parser: _JobPageParser) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in parser.ld_json_blocks:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict):
            blocks.append(payload)
        elif isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
    return blocks


def _extract_json_blocks(parser: _JobPageParser) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in parser.json_blocks:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict):
            blocks.append(payload)
        elif isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
    return blocks


def _find_in_structure(value: Any, *keys: str) -> str:
    wanted = {key.lower() for key in keys}

    def walk(node: Any) -> str:
        if isinstance(node, dict):
            for key, item in node.items():
                if str(key).strip().lower() in wanted:
                    normalized = _normalize_text(item if isinstance(item, str) else str(item))
                    if normalized:
                        return normalized
                found = walk(item)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(value)


def _select_job_schema(ld_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    for block in ld_blocks:
        raw_type = block.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        normalized = {str(item).strip().lower() for item in types if item}
        if normalized.intersection({"jobposting", "job posting", "occupation"}):
            return block
    return {}


def _select_job_schema_from_json(json_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    for block in json_blocks:
        if not isinstance(block, dict):
            continue
        for key in ("job", "jobPosting", "job_posting", "posting", "vacancy"):
            candidate = block.get(key)
            if isinstance(candidate, dict):
                return candidate
    return {}


def _extract_structured_hints(parser: _JobPageParser, url: str) -> dict[str, str]:
    ld_blocks = _extract_json_ld(parser)
    json_blocks = _extract_json_blocks(parser)
    job_schema = _select_job_schema(ld_blocks)
    nested_job_schema = _select_job_schema_from_json(json_blocks)
    if nested_job_schema:
        job_schema = nested_job_schema if not job_schema else {**nested_job_schema, **job_schema}

    hints = {
        "title": _normalize_text(job_schema.get("title") or parser.title or _best_meta(parser.meta, "og:title", "twitter:title", "title")) or _slug_title_from_url(url),
        "description": _normalize_text(
            job_schema.get("description")
            or _best_meta(parser.meta, "og:description", "twitter:description", "description")
        ),
        "location": "",
        "compensation": "",
        "experience": "",
    }

    if not hints["title"]:
        hints["title"] = _find_in_structure(json_blocks, "title", "jobTitle", "position", "role") or _slug_title_from_url(url)
    if not hints["description"]:
        hints["description"] = _find_in_structure(json_blocks, "description", "summary", "content") or ""
    if not hints["location"]:
        hints["location"] = _find_in_structure(json_blocks, "location", "addressLocality", "addressRegion", "city", "remote")
    if not hints["compensation"]:
        hints["compensation"] = _find_in_structure(json_blocks, "salary", "compensation", "pay", "range", "minValue", "maxValue")
    if not hints["experience"]:
        hints["experience"] = _find_in_structure(json_blocks, "experience", "experienceRequired", "years", "seniority")

    job_location = job_schema.get("jobLocation")
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, dict):
            hints["location"] = _normalize_text(
                address.get("addressLocality")
                or address.get("addressRegion")
                or address.get("addressCountry")
                or ""
            )
    elif isinstance(job_location, list):
        for item in job_location:
            if not isinstance(item, dict):
                continue
            address = item.get("address")
            if isinstance(address, dict):
                hints["location"] = _normalize_text(
                    address.get("addressLocality")
                    or address.get("addressRegion")
                    or address.get("addressCountry")
                    or ""
                )
                if hints["location"]:
                    break

    base_salary = job_schema.get("baseSalary")
    if isinstance(base_salary, dict):
        value = base_salary.get("value")
        if isinstance(value, dict):
            hints["compensation"] = _normalize_text(
                value.get("currency")
                or value.get("minValue")
                or value.get("maxValue")
                or value.get("value")
                or ""
            )

    text = parser.get_text()
    if not hints["location"]:
        location_match = re.search(
            r"\b(remote|hybrid|onsite|on-site)\b(?:\s*[-|,]\s*([A-Za-z .,-]{2,60}))?",
            text,
            flags=re.IGNORECASE,
        )
        if location_match:
            hints["location"] = location_match.group(0)

    compensation_match = re.search(
        r"(\$ ?\d{2,3}(?:[kK])?(?:\s*[-–to]+\s*\$? ?\d{2,3}(?:[kK])?)?(?:\s*\+?\s*equity)?|\bcompetitive\b|\bmarket[- ]rate\b)",
        text,
        flags=re.IGNORECASE,
    )
    if compensation_match and not hints["compensation"]:
        hints["compensation"] = compensation_match.group(0)

    experience_match = re.search(r"\b\d+\s*[\+\-–]\s*\d+\s+years\b|\b\d+\+?\s+years\b", text, flags=re.IGNORECASE)
    if experience_match:
        hints["experience"] = experience_match.group(0)

    return hints


def _build_llm_prompt(url: str, parser: _JobPageParser, hints: dict[str, str]) -> str:
    meta_lines = "\n".join(
        f"- {key}: {value}"
        for key, value in {
            "title": parser.title,
            "description": _best_meta(parser.meta, "og:description", "twitter:description", "description"),
            "site_name": _best_meta(parser.meta, "og:site_name", "application-name"),
            "url": url,
        }.items()
        if value
    )
    structured_hints = "\n".join(f"- {key}: {value}" for key, value in hints.items() if value)
    text = parser.get_text()
    clipped_text = text[:12000]

    return (
        "You are extracting structured job posting details from a job page.\n"
        "Use only the information present in the page content and metadata.\n"
        "If a value is unknown, use an empty string.\n"
        "Return only valid JSON with this schema:\n"
        "{\n"
        '  "title": "",\n'
        '  "description": "",\n'
        '  "location": "",\n'
        '  "compensation": "",\n'
        '  "workAuthorization": "required",\n'
        '  "remotePolicy": "hybrid",\n'
        '  "experienceRequired": ""\n'
        "}\n\n"
        f"URL: {url}\n\n"
        f"Metadata:\n{meta_lines or '- none'}\n\n"
        f"Structured hints:\n{structured_hints or '- none'}\n\n"
        f"Page text:\n{clipped_text}\n"
    )


def _fallback_parse(url: str, parser: _JobPageParser) -> dict[str, str]:
    text = parser.get_text()
    hints = _extract_structured_hints(parser, url)
    title = hints["title"] or _slug_title_from_url(url) or "Job Posting"
    description = hints["description"] or text[:1200] or f"Imported from {urlparse(url).netloc or 'job posting'}."

    remote_policy = "hybrid"
    location = hints["location"] or "Remote"
    lowered = f"{title}\n{description}\n{text}".lower()
    if "remote" in lowered:
        remote_policy = "remote"
        if not hints["location"]:
            location = "Remote"
    elif "onsite" in lowered or "on-site" in lowered:
        remote_policy = "onsite"
        if not hints["location"]:
            location = "On-site"

    return {
        "title": title,
        "description": description,
        "location": location,
        "compensation": hints["compensation"],
        "workAuthorization": "required",
        "remotePolicy": remote_policy,
        "experienceRequired": hints["experience"],
    }


def parse_job_posting_url(*, url: str) -> dict[str, str]:
    raw_url = (url or "").strip()
    if not raw_url:
        raise ValueError("url is required")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Referer": raw_url,
    }

    try:
        response = _http_session().get(raw_url, timeout=HTTP_TIMEOUT_SECONDS, headers=headers)
    except requests.RequestException as exc:
        source = _source_from_url(raw_url)
        logger.warning("job_parse_http_failed source=%s error=%s", source, str(exc))
        raise APIError(f"job_parse_failed source={source} reason=request_failed", status_code=400) from None

    if response.status_code >= 400:
        source = _source_from_url(raw_url)
        if response.status_code == 403:
            logger.warning("job_parse_blocked source=%s status=%s", source, response.status_code)
        else:
            logger.warning("job_parse_http_status source=%s status=%s", source, response.status_code)
        if ENABLE_PLAYWRIGHT_JOB_PARSER:
            logger.info("job_parse_playwright_enabled source=%s status=%s", source, response.status_code)
        raise APIError(
            f"job_parse_failed source={source} status={response.status_code} reason=http_error",
            status_code=400,
        )

    parser = _JobPageParser()
    parser.feed(response.text)

    hints = _extract_structured_hints(parser, raw_url)
    llm_prompt = _build_llm_prompt(raw_url, parser, hints)

    if GROQ_API_KEY:
        try:
            parsed = generate(llm_prompt, expect_json=True)
            if isinstance(parsed, dict):
                result = {
                    "title": _normalize_text(str(parsed.get("title") or "")) or hints["title"],
                    "description": _normalize_text(str(parsed.get("description") or "")) or hints["description"],
                    "location": _normalize_text(str(parsed.get("location") or "")) or hints["location"],
                    "compensation": _normalize_text(str(parsed.get("compensation") or "")) or hints["compensation"],
                    "workAuthorization": _normalize_text(str(parsed.get("workAuthorization") or "")) or "required",
                    "remotePolicy": _normalize_text(str(parsed.get("remotePolicy") or "")) or "hybrid",
                    "experienceRequired": _normalize_text(str(parsed.get("experienceRequired") or "")) or hints["experience"],
                }
                if not result["title"]:
                    result["title"] = hints["title"]
                if not result["description"]:
                    result["description"] = hints["description"] or parser.get_text()[:1200]
                return result
        except Exception as exc:
            logger.warning("job_parse_llm_failed url=%s error=%s", raw_url, str(exc))

    logger.info("job_parse_fallback_used url=%s", raw_url)
    return _fallback_parse(raw_url, parser)
