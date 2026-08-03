"""linkedin/job_posting — LinkedIn Job Posting infrastructure.

Phase 2.1: Discovery module.
Phase 2.3: Configurable worker execution mode.
"""
from app.linkedin.job_posting.job_posting_discovery import JobPostingDiscovery
from app.linkedin.job_posting.job_posting_spec import JobPostingSpec
from app.linkedin.job_posting.job_posting_result import JobPostingResult, StepDiagnostic, WorkerStatus
from app.linkedin.job_posting.job_posting_worker import JobPostingWorker
from app.linkedin.job_posting.job_posting_types import (
    DiscoveredAutocomplete,
    DiscoveredButton,
    DiscoveredDialog,
    DiscoveredEntryPoint,
    DiscoveredField,
    DiscoveredHiddenField,
    DiscoveredSection,
    DiscoveredStep,
    DiscoveryStatus,
    EntryPointKind,
    FieldType,
    JobPostingDiscoveryResult,
    NavigationKind,
    JobPostingExecutionMode,
)

__all__ = [
    "JobPostingDiscovery",
    "JobPostingWorker",
    "JobPostingSpec",
    "JobPostingResult",
    "StepDiagnostic",
    "WorkerStatus",
    "DiscoveredAutocomplete",
    "DiscoveredButton",
    "DiscoveredDialog",
    "DiscoveredEntryPoint",
    "DiscoveredField",
    "DiscoveredHiddenField",
    "DiscoveredSection",
    "DiscoveredStep",
    "DiscoveryStatus",
    "EntryPointKind",
    "FieldType",
    "JobPostingDiscoveryResult",
    "NavigationKind",
    "JobPostingExecutionMode",
]
