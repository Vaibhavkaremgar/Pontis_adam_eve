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

from app.core.config import ENABLE_PLAYWRIGHT_JOB_PARSER, HTTP_TIMEOUT_SECONDS, GROQ_API_KEY, OPEN_ROUTER_API
from app.services.llm_service import generate
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

_ALLOWED_REMOTE_POLICIES = {"remote", "hybrid", "onsite"}
_ALLOWED_WORK_AUTH = {"required", "preferred", "not-required"}


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


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = unescape(data).strip()
        if text:
            self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def get_text(self) -> str:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(self.parts))
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _strip_html_markup(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if "<" not in text and ">" not in text:
        return re.sub(r"\s+", " ", unescape(text)).strip()
    parser = _TextOnlyHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        fallback = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
        fallback = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", fallback)
        fallback = re.sub(r"(?s)<[^>]+>", " ", fallback)
        fallback = unescape(fallback)
        return re.sub(r"\s+", " ", fallback).strip()
    cleaned = parser.get_text()
    return re.sub(r"\s+", " ", cleaned).strip()


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


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _strip_html_markup(value)
    return _normalize_text(str(value))


def _truncate_text(value: str, limit: int) -> str:
    normalized = _normalize_text(value)
    if not normalized or limit <= 0:
        return ""
    return normalized[:limit].strip()


def _extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        return ""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""

    pages: list[str] = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                try:
                    text = page.get_text("text", sort=True)
                except Exception:
                    continue
                text = _normalize_text(text)
                if text:
                    pages.append(text)
    except Exception as exc:
        logger.warning("job_parse_pdf_extraction_failed error=%s", str(exc))
        return ""

    return "\n\n".join(pages).strip()


def _looks_binary(text: str) -> bool:
    if not text:
        return False
    sample = text[:4000]
    nul_ratio = sample.count("\x00") / max(1, len(sample))
    control_chars = sum(1 for char in sample if ord(char) < 32 and char not in {"\n", "\r", "\t"})
    return nul_ratio > 0.001 or control_chars > max(40, len(sample) // 8)


def _decode_response_body(response: requests.Response, url: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type", "")).lower()
    raw_content = getattr(response, "content", b"")

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        pdf_text = _extract_pdf_text_from_bytes(raw_content if isinstance(raw_content, (bytes, bytearray)) else b"")
        if pdf_text:
            return pdf_text

    raw_text = getattr(response, "text", "") or ""
    if raw_text and not _looks_binary(raw_text):
        return raw_text

    if isinstance(raw_content, (bytes, bytearray)) and raw_content:
        encoding = getattr(response, "apparent_encoding", None) or "utf-8"
        for candidate_encoding in (encoding, "utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                decoded = bytes(raw_content).decode(candidate_encoding, errors="ignore")
            except Exception:
                continue
            if decoded and not _looks_binary(decoded):
                return decoded
        return bytes(raw_content).decode("utf-8", errors="ignore")

    return raw_text


def _serialize_json_block(block: dict[str, Any], limit: int = 4000) -> str:
    try:
        return json.dumps(block, ensure_ascii=False, indent=2)[:limit]
    except TypeError:
        return json.dumps(str(block), ensure_ascii=False)[:limit]


def _collect_json_source_blocks(parser: _JobPageParser, limit: int = 3) -> list[str]:
    ld_blocks = _extract_json_ld(parser)
    json_blocks = _extract_json_blocks(parser)
    selected: list[str] = []

    job_schema = _select_job_schema(ld_blocks)
    if job_schema:
        selected.append(_serialize_json_block(job_schema))

    nested_job_schema = _select_job_schema_from_json(json_blocks)
    if nested_job_schema and nested_job_schema is not job_schema:
        selected.append(_serialize_json_block(nested_job_schema))

    if len(selected) < limit:
        for block in ld_blocks:
            if block is job_schema:
                continue
            selected.append(_serialize_json_block(block))
            if len(selected) >= limit:
                break

    if len(selected) < limit:
        for block in json_blocks:
            if block is nested_job_schema:
                continue
            selected.append(_serialize_json_block(block))
            if len(selected) >= limit:
                break

    return selected[:limit]


def _extract_structured_hints(parser: _JobPageParser, url: str) -> dict[str, str]:
    ld_blocks = _extract_json_ld(parser)
    json_blocks = _extract_json_blocks(parser)
    job_schema = _select_job_schema(ld_blocks)
    nested_job_schema = _select_job_schema_from_json(json_blocks)
    if nested_job_schema:
        job_schema = nested_job_schema if not job_schema else {**nested_job_schema, **job_schema}

    hints = {
        "title": _strip_html_markup(job_schema.get("title") or parser.title or _best_meta(parser.meta, "og:title", "twitter:title", "title")) or _slug_title_from_url(url),
        "description": _strip_html_markup(
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


def _normalize_work_authorization(value: Any, fallback: str = "required") -> str:
    normalized = _normalize_text(str(value or "")).lower().replace("_", "-")
    if normalized in _ALLOWED_WORK_AUTH:
        return normalized
    if "preferred" in normalized:
        return "preferred"
    if "not required" in normalized or "not-required" in normalized or "no sponsorship" in normalized:
        return "not-required"
    if "required" in normalized:
        return "required"
    return fallback


def _normalize_remote_policy(value: Any, fallback: str = "hybrid") -> str:
    normalized = _normalize_text(str(value or "")).lower().replace("_", "-")
    if normalized in _ALLOWED_REMOTE_POLICIES:
        return normalized
    if "remote" in normalized:
        return "remote"
    if "onsite" in normalized or "on-site" in normalized or "in office" in normalized:
        return "onsite"
    if "hybrid" in normalized:
        return "hybrid"
    return fallback


def _normalize_job_parse_result(parsed: dict[str, Any], parser: _JobPageParser, url: str, hints: dict[str, str]) -> dict[str, str]:
    text = parser.get_text()
    title = _coerce_text(parsed.get("title")) or hints["title"] or _slug_title_from_url(url)
    description = _coerce_text(parsed.get("description")) or hints["description"] or text[:1200]
    location = _coerce_text(parsed.get("location")) or hints["location"]
    compensation = _coerce_text(parsed.get("compensation")) or hints["compensation"]
    work_authorization = _normalize_work_authorization(parsed.get("workAuthorization"), "required")
    remote_policy = _normalize_remote_policy(parsed.get("remotePolicy"), "hybrid")
    experience_required = _coerce_text(parsed.get("experienceRequired")) or hints["experience"]

    if not title:
        title = "Job Posting"
    if not description:
        description = f"Imported from {urlparse(url).netloc or 'job posting'}."

    # Keep the description as a real sentence/paragraph rather than a fragmentary outline.
    description = re.sub(r"\s+\n", "\n", description).strip()

    return {
        "title": title,
        "description": description,
        "location": location,
        "compensation": compensation,
        "workAuthorization": work_authorization,
        "remotePolicy": remote_policy,
        "experienceRequired": experience_required,
    }


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
    json_sources = "\n\n".join(_collect_json_source_blocks(parser))
    text = parser.get_text()
    clipped_text = _truncate_text(text, 14000)

    return (
        "You are a precise job-posting extraction engine.\n"
        "Return only valid JSON and nothing else.\n"
        "Do not invent or infer details that are not explicitly supported by the page.\n"
        "Prefer exact values from structured data (JSON-LD / embedded JSON) first, then metadata, then visible page text.\n"
        "If a field is unknown, return an empty string except for the two enum fields below.\n"
        "Normalize the enums exactly as follows:\n"
        '- workAuthorization: one of "required", "preferred", "not-required"\n'
        '- remotePolicy: one of "remote", "hybrid", "onsite"\n'
        "Keep title as the actual job title from the posting.\n"
        "Keep description as the main job description paragraph(s), preserving the substance of the posting.\n"
        "Return only JSON with this schema:\n"
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
        f"Structured JSON sources:\n{json_sources or '- none'}\n\n"
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

    # SSRF protection: block private/internal URLs before fetching
    from app.utils.ssrf import validate_public_url
    raw_url = validate_public_url(raw_url)

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

    body_text = _decode_response_body(response, raw_url)
    parser = _JobPageParser()
    parser.feed(body_text)

    hints = _extract_structured_hints(parser, raw_url)
    llm_prompt = _build_llm_prompt(raw_url, parser, hints)

    if GROQ_API_KEY or OPEN_ROUTER_API:
        try:
            parsed = generate(llm_prompt, expect_json=True)
            if isinstance(parsed, dict):
                return _normalize_job_parse_result(parsed, parser, raw_url, hints)
        except Exception as exc:
            logger.warning("job_parse_llm_failed url=%s error=%s", raw_url, str(exc))

    logger.info("job_parse_fallback_used url=%s", raw_url)
    return _fallback_parse(raw_url, parser)
