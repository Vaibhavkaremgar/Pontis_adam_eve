"""job_posting_result.py — Result type returned by JobPostingWorker.

Pure dataclass.  No Playwright imports.  No database imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.linkedin.job_posting.job_posting_types import JobPostingExecutionMode, LinkedInSessionState, PublishLifecycleState


class WorkerStatus(str, Enum):
    OK = "ok"                           # reached review page cleanly
    PARTIAL = "partial"                 # advanced some steps but did not reach review
    VALIDATION_ERROR = "validation_error"  # wizard showed a validation error
    LOGIN_REQUIRED = "login_required"
    SESSION_EXPIRED = "session_expired"
    CHECKPOINT = "checkpoint"
    CAPTCHA = "captcha"
    UNKNOWN_SESSION = "unknown_session"
    LOCK_TIMEOUT = "lock_timeout"       # could not acquire account lock
    BROWSER_ERROR = "browser_error"
    SPEC_INVALID = "spec_invalid"       # required fields missing from spec
    FAILED = "failed"                   # unexpected error


@dataclass
class StepDiagnostic:
    """Per-step execution record."""
    step_index: int = 0
    step_label: str = ""
    url: str = ""
    fields_filled: list[str] = field(default_factory=list)
    fields_skipped: list[str] = field(default_factory=list)
    verification_passed: bool = False
    validation_errors: list[str] = field(default_factory=list)
    navigation_succeeded: bool = False
    elapsed_ms: int = 0                 # wall-clock time spent on this step
    screenshot_path: str = ""           # populated on failure
    html_path: str = ""                 # populated on failure
    notes: str = ""


@dataclass
class JobPostingResult:
    """Complete output of one JobPostingWorker.run() call.

    review_reached is True when the worker successfully navigated to the
    Review page. execution_mode records whether the worker stopped before
    publish or actually published.
    """
    status: WorkerStatus = WorkerStatus.FAILED
    execution_mode: JobPostingExecutionMode = JobPostingExecutionMode.DRY_RUN
    dry_run: bool = True
    session_state: LinkedInSessionState = LinkedInSessionState.UNKNOWN
    session_reason: str = ""
    session_signals: list[str] = field(default_factory=list)
    publish_state: PublishLifecycleState = PublishLifecycleState.PUBLISH_REQUESTED
    publish_clicked: bool = False
    published: bool = False
    publish_confirmed: bool = False

    # Navigation outcome
    review_reached: bool = False
    current_step: int = 0
    current_step_label: str = ""
    completed_steps: list[str] = field(default_factory=list)

    # Timing
    duration_ms: int = 0

    # Per-step diagnostics
    diagnostics: list[StepDiagnostic] = field(default_factory=list)

    # Errors / warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
