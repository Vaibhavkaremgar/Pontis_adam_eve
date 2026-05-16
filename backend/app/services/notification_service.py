from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.db.repositories import NotificationWorkflowTokenRepository

BOOKING_BASE_URL = "https://interview.pontis.one/booking.html"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_source(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    source: dict[str, Any] = {}
    raw_data = getattr(candidate, "raw_data", None)
    if isinstance(raw_data, dict):
        source.update(raw_data)
    for attr in (
        "name",
        "full_name",
        "fullName",
        "email",
        "phone",
        "linkedin_url",
        "linkedinUrl",
        "github_url",
        "githubUrl",
        "current_company",
        "currentCompany",
        "current_title",
        "currentTitle",
        "company",
        "role",
        "title",
        "skills",
        "total_experience_years",
        "years_experience",
        "parsed_resume_text",
        "parsedResumeText",
        "parsed_resume_json",
        "parsedResumeJson",
    ):
        value = getattr(candidate, attr, None)
        if value is not None:
            source[attr] = value
    return source


def _normalize_email(value: Any) -> str:
    email = _normalize_text(value).lower()
    if not email or "@" not in email or ".." in email:
        return ""
    local, _, domain = email.rpartition("@")
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return email


def _extract_email_value(node: Any) -> str:
    if isinstance(node, str):
        return _normalize_email(node)
    if isinstance(node, dict):
        for key in ("email", "work_email", "personal_email", "address", "value"):
            email = _normalize_email(node.get(key))
            if email:
                return email
        for value in node.values():
            email = _extract_email_value(value)
            if email:
                return email
    elif isinstance(node, list):
        for item in node:
            email = _extract_email_value(item)
            if email:
                return email
    return ""


def _candidate_email(candidate: Any) -> str:
    source = _candidate_source(candidate)
    for key in ("email", "work_email", "personal_email", "emails_primary"):
        email = _extract_email_value(source.get(key))
        if email:
            return email
    for key in ("emails", "work_emails", "personal_emails", "contact_emails", "parsed_resume_json", "parsedResumeJson", "raw_data"):
        email = _extract_email_value(source.get(key))
        if email:
            return email
    return ""


def _collect_list_values(value: Any) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for item in node.values():
                visit(item)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        text = _normalize_text(node)
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        collected.append(text)

    visit(value)
    return collected


def _candidate_value(candidate: Any, *names: str) -> Any:
    source = _candidate_source(candidate)
    for name in names:
        if name in source:
            value = source.get(name)
        else:
            value = getattr(candidate, name, None)
        if isinstance(value, str):
            text = _normalize_text(value)
            if text:
                return text
        elif value not in (None, "", [], {}):
            return value
    return ""


def _string_field(item: Any, *names: str) -> str:
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _build_booking_link(token: str) -> str:
    query = urlencode({"token": token}) if token else ""
    return f"{BOOKING_BASE_URL}?{query}" if query else BOOKING_BASE_URL


def build_slot_booking_payload(*, candidate: Any, job: Any) -> dict[str, Any]:
    source = _candidate_source(candidate)
    parsed_resume_json = _candidate_value(candidate, "parsed_resume_json", "parsedResumeJson")
    if not isinstance(parsed_resume_json, dict):
        parsed_resume_json = _candidate_value(source, "parsed_data", "parsedData")
        if not isinstance(parsed_resume_json, dict):
            parsed_resume_json = {}

    resume_text = _normalize_text(
        _candidate_value(candidate, "parsed_resume_text", "parsedResumeText", "resume_text", "resumeText", "raw_resume_text")
        or parsed_resume_json.get("raw_resume_text")
        or parsed_resume_json.get("resume_text")
        or parsed_resume_json.get("rawResumeText")
    )
    name = _candidate_value(candidate, "name", "full_name", "fullName") or parsed_resume_json.get("full_name") or parsed_resume_json.get("fullName")
    headline = _candidate_value(candidate, "current_title", "currentTitle", "role", "title") or parsed_resume_json.get("headline") or parsed_resume_json.get("title")
    skills = _candidate_value(candidate, "skills") or parsed_resume_json.get("skills") or []
    companies = _candidate_value(candidate, "companies") or parsed_resume_json.get("companies") or []
    education = _candidate_value(candidate, "education") or parsed_resume_json.get("education") or []
    projects = _candidate_value(candidate, "projects") or parsed_resume_json.get("projects") or []
    certifications = _candidate_value(candidate, "certifications") or parsed_resume_json.get("certifications") or []
    domain_experience = _candidate_value(candidate, "domain_experience", "domainExperience") or parsed_resume_json.get("domain_experience") or parsed_resume_json.get("domainExperience") or []
    location = _candidate_value(candidate, "location") or parsed_resume_json.get("location") or ""
    summary = _candidate_value(candidate, "summary") or parsed_resume_json.get("summary") or ""
    total_experience_years = _candidate_value(candidate, "total_experience_years", "years_experience", "yearsExperience")
    if total_experience_years in ("", None):
        total_experience_years = parsed_resume_json.get("years_experience", parsed_resume_json.get("yearsExperience", 0.0))
    try:
        total_experience_years_value = float(total_experience_years or 0.0)
    except (TypeError, ValueError):
        total_experience_years_value = 0.0

    current_company = _candidate_value(candidate, "current_company", "currentCompany", "company") or (companies[0] if isinstance(companies, list) and companies else "")
    email = _candidate_email(candidate)
    phone = _candidate_value(candidate, "phone") or parsed_resume_json.get("phone") or ""
    linkedin_url = _candidate_value(candidate, "linkedin_url", "linkedinUrl") or parsed_resume_json.get("linkedin_url") or parsed_resume_json.get("linkedinUrl") or ""
    github_url = _candidate_value(candidate, "github_url", "githubUrl") or parsed_resume_json.get("github_url") or parsed_resume_json.get("githubUrl") or ""
    skill_list = _collect_list_values(skills)
    company_list = _collect_list_values(companies)
    education_list = _collect_list_values(education)
    project_list = _collect_list_values(projects)
    certification_list = _collect_list_values(certifications)
    domain_experience_list = _collect_list_values(domain_experience)
    resume_metadata = {
        "full_name": _normalize_text(name),
        "headline": _normalize_text(headline),
        "years_experience": total_experience_years_value,
        "skills": skill_list,
        "companies": company_list,
        "education": education_list,
        "projects": project_list,
        "certifications": certification_list,
        "location": _normalize_text(location),
        "summary": _normalize_text(summary),
        "domain_experience": domain_experience_list,
        "contact": {
            "email": email,
            "phone": _normalize_text(phone),
            "linkedin_url": _normalize_text(linkedin_url),
            "github_url": _normalize_text(github_url),
        },
    }
    return {
        "name": _normalize_text(name),
        "email": email,
        "phone": _normalize_text(phone),
        "linkedin_url": _normalize_text(linkedin_url),
        "github_url": _normalize_text(github_url),
        "current_company": _normalize_text(current_company),
        "current_title": _normalize_text(_candidate_value(candidate, "current_title", "currentTitle", "role", "title") or headline),
        "total_experience_years": total_experience_years_value,
        "skills": skill_list,
        "resume_text": resume_text,
        "parsed_resume_text": resume_text,
        "parsed_resume_json": parsed_resume_json,
        "resume_metadata": resume_metadata,
        "resume": {
            "text": resume_text,
            "metadata": resume_metadata,
            "structured": parsed_resume_json,
        },
        "fit_score": float(_candidate_value(candidate, "fit_score") or 0.0),
        "job_title": _string_field(job, "title", "job_title", "jobTitle"),
        "company_name": _string_field(job, "company_name", "company", "companyName"),
    }


def create_notification_workflow_token(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_name: str,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    token_type: str = "",
    is_active: bool = True,
    source_app: str = "dashboard",
) -> dict[str, Any]:
    token_value = token or secrets.token_urlsafe(32)
    row = NotificationWorkflowTokenRepository(db).create(
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_name=workflow_name,
        token=token_value,
        payload=payload,
        expires_at=expires_at,
        token_type=token_type,
        is_active=is_active,
        source_app=source_app,
    )
    booking_link = _build_booking_link(row.token)
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "tokenType": row.token_type,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "isActive": row.is_active,
        "sourceApp": row.source_app,
        "payload": row.payload,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }


def consume_notification_workflow_token(*, db: Session, token: str, source_app: str = "dashboard") -> dict[str, Any] | None:
    row = NotificationWorkflowTokenRepository(db).mark_consumed(token, source_app=source_app)
    if not row:
        return None
    booking_link = _build_booking_link(row.token)
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "tokenType": row.token_type,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "isActive": row.is_active,
        "sourceApp": row.source_app,
        "payload": row.payload,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }
