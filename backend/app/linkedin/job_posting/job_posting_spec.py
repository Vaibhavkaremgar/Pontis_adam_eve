"""job_posting_spec.py — Normalized input spec for the LinkedIn Job Posting worker.

Pure dataclass.  No database access.  No Playwright imports.
The worker consumes only this object — nothing else from the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.linkedin.job_posting.job_posting_types import JobPostingExecutionMode


@dataclass
class JobPostingSpec:
    """All information required to populate the LinkedIn job posting wizard.

    Every field maps directly to a wizard control discovered in Phase 2.2.
    Optional fields default to empty string / empty list — the worker skips
    any field whose value is empty.

    REQUIRED fields (wizard will not advance without them):
        title, company, workplace_type, location, job_type, description

    OPTIONAL fields (wizard advances even if blank):
        experience_level, industry, job_function, skills,
        salary_min, salary_max, salary_currency,
        application_method, application_email, application_url
    """

    # ── Step 1: Job details ───────────────────────────────────────────────────
    title: str = ""                     # e.g. "Senior Software Engineer"
    company: str = ""                   # e.g. "Acme Corp"
    workplace_type: str = ""            # "On-site" | "Hybrid" | "Remote"
    location: str = ""                  # e.g. "San Francisco, CA"
    job_type: str = ""                  # "Full-time" | "Part-time" | "Contract" | …
    experience_level: str = ""          # "Entry level" | "Mid-Senior level" | …
    industry: str = ""                  # e.g. "Software Development"
    job_function: str = ""              # e.g. "Engineering"
    salary_min: str = ""                # e.g. "120000"
    salary_max: str = ""                # e.g. "160000"
    salary_currency: str = ""           # e.g. "USD"

    # ── Step 2: Job description ───────────────────────────────────────────────
    description: str = ""               # Full rich-text job description

    # ── Step 3: Skills ────────────────────────────────────────────────────────
    skills: list[str] = field(default_factory=list)   # e.g. ["Python", "AWS"]

    # ── Step 4: Applicant options ─────────────────────────────────────────────
    application_method: str = ""        # "Through LinkedIn" | "Through an external website"
    application_email: str = ""         # used when method == "Through LinkedIn"
    application_url: str = ""           # used when method == "Through an external website"

    # ── Metadata ──────────────────────────────────────────────────────────────
    execution_mode: JobPostingExecutionMode = JobPostingExecutionMode.DRY_RUN

    def required_fields_present(self) -> bool:
        """Return True if all wizard-required fields are populated."""
        return all([
            self.title.strip(),
            self.company.strip(),
            self.workplace_type.strip(),
            self.location.strip(),
            self.job_type.strip(),
        ])

    def missing_required(self) -> list[str]:
        """Return names of required fields that are empty."""
        checks = {
            "title": self.title,
            "company": self.company,
            "workplace_type": self.workplace_type,
            "location": self.location,
            "job_type": self.job_type,
        }
        return [name for name, val in checks.items() if not val.strip()]
