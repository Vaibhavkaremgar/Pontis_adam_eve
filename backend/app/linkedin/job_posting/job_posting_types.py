"""job_posting_types.py — Data types for LinkedIn Job Posting discovery.

All types are pure dataclasses / enums.
No Playwright imports. No database imports. No worker imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DiscoveryStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    LOGIN_REQUIRED = "login_required"
    SESSION_EXPIRED = "session_expired"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class FieldType(str, Enum):
    TEXT_INPUT = "text_input"
    TEXTAREA = "textarea"
    RICH_TEXT = "rich_text"          # contenteditable / Lexical / Draft.js
    DROPDOWN_NATIVE = "dropdown_native"
    DROPDOWN_ARIA = "dropdown_aria"
    DROPDOWN_COMBOBOX = "dropdown_combobox"
    DROPDOWN_SEARCHABLE = "dropdown_searchable"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    TOGGLE = "toggle"
    FILE_UPLOAD = "file_upload"
    DATE_PICKER = "date_picker"
    MULTI_SELECT = "multi_select"
    BUTTON = "button"
    UNKNOWN = "unknown"


class NavigationKind(str, Enum):
    SAME_PAGE = "same_page"          # DOM mutation, no URL change
    URL_CHANGE = "url_change"        # full navigation
    DIALOG_OPEN = "dialog_open"      # modal/dialog appeared
    DIALOG_CLOSE = "dialog_close"
    STEP_FORWARD = "step_forward"    # wizard step advance
    STEP_BACK = "step_back"


class EntryPointKind(str, Enum):
    JOBS_HOMEPAGE = "jobs_homepage"
    COMPANY_PAGE = "company_page"
    RECRUITER_DASHBOARD = "recruiter_dashboard"
    BUSINESS_MANAGER = "business_manager"
    DIRECT_URL = "direct_url"
    UNKNOWN = "unknown"


class JobPostingExecutionMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"

    @classmethod
    def normalize(cls, value: Any) -> "JobPostingExecutionMode":
        if isinstance(value, cls):
            return value
        if hasattr(value, "value") and str(getattr(value, "value")).strip().upper() == cls.LIVE.value:
            return cls.LIVE
        normalized = str(value or "").strip().upper()
        if normalized == cls.LIVE.value:
            return cls.LIVE
        return cls.DRY_RUN


class LinkedInSessionState(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CHECKPOINT = "CHECKPOINT"
    CAPTCHA = "CAPTCHA"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LinkedInSessionValidation:
    state: LinkedInSessionState = LinkedInSessionState.UNKNOWN
    reason: str = ""
    url: str = ""
    title: str = ""
    signals: list[str] = field(default_factory=list)


class PublishLifecycleState(str, Enum):
    PUBLISH_REQUESTED = "publish_requested"
    PUBLISH_CLICKED = "publish_clicked"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    PUBLISH_CONFIRMED = "publish_confirmed"
    PUBLISH_FAILED = "publish_failed"


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredField:
    """Complete description of a single form field found during discovery."""
    label: str = ""
    accessible_name: str = ""
    role: str = ""
    input_type: str = ""
    field_type: FieldType = FieldType.UNKNOWN
    placeholder: str = ""
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    autocomplete: str = ""
    current_value: str = ""
    help_text: str = ""
    validation_message: str = ""
    options: list[str] = field(default_factory=list)   # for dropdowns / selects
    selector: str = ""
    container_path: str = ""          # CSS path of parent containers
    outer_html: str = ""
    bounding_box: dict[str, float] = field(default_factory=dict)
    step_index: int = 0
    page_url: str = ""


# ---------------------------------------------------------------------------
# Step / page discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredButton:
    label: str = ""
    aria_label: str = ""
    role: str = ""
    disabled: bool = False
    selector: str = ""
    is_submit: bool = False
    is_next: bool = False
    is_back: bool = False
    is_publish: bool = False
    is_save_draft: bool = False
    is_cancel: bool = False


@dataclass
class DiscoveredDialog:
    title: str = ""
    body_text: str = ""
    buttons: list[DiscoveredButton] = field(default_factory=list)
    selector: str = ""
    is_confirmation: bool = False
    is_error: bool = False
    is_premium_gate: bool = False


@dataclass
class DiscoveredSection:
    """A named section / fieldset within a wizard step."""
    label: str = ""
    selector: str = ""
    field_count: int = 0
    outer_html_preview: str = ""      # first 300 chars of outerHTML


@dataclass
class DiscoveredHiddenField:
    """An input[type=hidden] found on the page — useful for understanding form structure."""
    name: str = ""
    value: str = ""
    id: str = ""


@dataclass
class DiscoveredAutocomplete:
    """An autocomplete / typeahead widget."""
    label: str = ""
    selector: str = ""
    input_selector: str = ""
    listbox_selector: str = ""
    aria_autocomplete: str = ""       # "list" | "inline" | "both"
    aria_expanded: str = ""
    aria_haspopup: str = ""


@dataclass
class DiscoveredStep:
    """Everything captured on a single wizard step / page."""
    step_index: int = 0
    step_label: str = ""              # extracted from live DOM
    url: str = ""
    title: str = ""
    headings: list[str] = field(default_factory=list)
    sections: list[DiscoveredSection] = field(default_factory=list)
    fields: list[DiscoveredField] = field(default_factory=list)
    hidden_fields: list[DiscoveredHiddenField] = field(default_factory=list)
    autocomplete_widgets: list[DiscoveredAutocomplete] = field(default_factory=list)
    buttons: list[DiscoveredButton] = field(default_factory=list)
    dialogs: list[DiscoveredDialog] = field(default_factory=list)
    validation_messages: list[str] = field(default_factory=list)
    progress_text: str = ""           # e.g. "Step 2 of 4"
    progress_percent: float = 0.0
    has_rich_text: bool = False
    has_file_upload: bool = False
    has_dropdown: bool = False
    has_multi_select: bool = False
    has_date_picker: bool = False
    has_autocomplete: bool = False
    screenshot_path: str = ""
    html_path: str = ""
    dom_snapshot_path: str = ""       # full outerHTML of the form container
    fields_json_path: str = ""
    buttons_json_path: str = ""
    dialogs_json_path: str = ""
    progress_json_path: str = ""
    navigation_kind: NavigationKind = NavigationKind.SAME_PAGE
    dom_change_summary: str = ""      # diff summary vs previous step


# ---------------------------------------------------------------------------
# Entry point discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredEntryPoint:
    kind: EntryPointKind = EntryPointKind.UNKNOWN
    url: str = ""
    trigger_selector: str = ""
    trigger_label: str = ""
    reachable: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Full discovery result
# ---------------------------------------------------------------------------

@dataclass
class JobPostingDiscoveryResult:
    """Complete output of one discovery run."""
    status: DiscoveryStatus = DiscoveryStatus.FAILED
    account_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0

    # Entry points found
    entry_points: list[DiscoveredEntryPoint] = field(default_factory=list)
    primary_entry_point: DiscoveredEntryPoint = field(default_factory=DiscoveredEntryPoint)

    # Workflow
    total_steps: int = 0
    steps: list[DiscoveredStep] = field(default_factory=list)
    urls_visited: list[str] = field(default_factory=list)

    # Aggregated field inventory
    all_fields: list[DiscoveredField] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    rich_text_fields: list[str] = field(default_factory=list)
    dropdown_fields: list[str] = field(default_factory=list)
    autocomplete_fields: list[str] = field(default_factory=list)
    multi_select_fields: list[str] = field(default_factory=list)
    upload_fields: list[str] = field(default_factory=list)

    # Workflow flags
    has_draft_support: bool = False
    has_review_page: bool = False
    success_signal: str = ""          # how success is indicated
    validation_pattern: str = ""      # how validation errors are shown

    # Output paths
    output_dir: str = ""
    overview_json_path: str = ""
    workflow_json_path: str = ""

    # Errors / warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
