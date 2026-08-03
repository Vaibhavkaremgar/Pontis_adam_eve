from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class CommunicationRequest:
    recruiter_name: str = ""
    company: str = ""
    job_title: str = ""
    candidate_name: str = ""
    eve_link: str = ""
    template: str = ""


class CommunicationService:
    """Render LinkedIn engagement copy using only business-side inputs."""

    DEFAULT_TEMPLATE = (
        "Hi {candidate_name},\n\n"
        "{recruiter_name} from {company} thinks you could be a strong fit for {job_title}.\n"
        "If you're open to a quick conversation, you can use this link: {eve_link}\n\n"
        "Best,\n"
        "{recruiter_name}"
    )

    def render_message(
        self,
        *,
        recruiter_name: str,
        company: str,
        job_title: str,
        candidate_name: str,
        eve_link: str,
        template: str = "",
    ) -> str:
        values = {
            "recruiter_name": _normalize_text(recruiter_name) or "Recruiter",
            "company": _normalize_text(company) or "our company",
            "job_title": _normalize_text(job_title) or "the role",
            "candidate_name": _normalize_text(candidate_name) or "there",
            "eve_link": _normalize_text(eve_link),
        }
        base_template = _normalize_text(template) or self.DEFAULT_TEMPLATE
        rendered = base_template.format_map(_SafeFormatDict(values))
        return "\n".join(line.rstrip() for line in rendered.splitlines()).strip()


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""

