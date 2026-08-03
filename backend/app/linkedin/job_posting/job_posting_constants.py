"""job_posting_constants.py — Selectors, URLs, and known labels for LinkedIn Job Posting.

All constants are read-only.
No Playwright imports. No business logic.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Entry point URLs to probe
# ---------------------------------------------------------------------------

ENTRY_POINT_URLS: list[str] = [
    # Primary: Jobs homepage post-a-job button
    "https://www.linkedin.com/jobs/",
    # Recruiter / Talent Solutions
    "https://www.linkedin.com/talent/",
    # Business Manager
    "https://business.linkedin.com/",
    # Direct job posting wizard
    "https://www.linkedin.com/job-posting/",
    # Company admin pages (requires company page admin access)
    "https://www.linkedin.com/company/",
]

# Direct wizard URL — most reliable entry point for automation
DIRECT_POST_JOB_URL = "https://www.linkedin.com/job-posting/new"

# Jobs homepage
JOBS_HOMEPAGE_URL = "https://www.linkedin.com/jobs/"

# ---------------------------------------------------------------------------
# Real flow selectors — Jobs nav → sidebar → title modal → summary → edit
# ---------------------------------------------------------------------------

# Step 1: Click the "Jobs" tab in the top nav
JOBS_NAV_SELECTORS: list[str] = [
    "a[href*='/jobs'][data-link-to*='jobs' i]",
    "nav a[href*='/jobs/']",
    "a[href='/jobs/']",
    "[aria-label*='Jobs' i][href*='jobs']",
    "li a:has-text('Jobs')",
]

# Step 2: "Post a free job" in the left sidebar on the Jobs page
POST_FREE_JOB_SELECTORS: list[str] = [
    "a:has-text('Post a free job')",
    "button:has-text('Post a free job')",
    "a:has-text('Post a job')",
    "button:has-text('Post a job')",
    "[aria-label*='Post a free job' i]",
    "[aria-label*='Post a job' i]",
    "a[href*='job-posting']",
]

# Step 3: Job title input in the title-entry modal/page
TITLE_INPUT_SELECTORS: list[str] = [
    "input[aria-label*='Job title' i]",
    "input[placeholder*='Job title' i]",
    "input[placeholder*='title' i]",
    "input[id*='job-title' i]",
    "input[name*='title' i]",
    "input[id*='title' i]",
    "#job-posting-title-input",
    "input[type='text']",
]

# Step 3: Continue button after entering title
CONTINUE_BUTTON_SELECTORS: list[str] = [
    "button:has-text('Continue')",
    "button:has-text('Get started')",
    "button:has-text('Next')",
    "[aria-label*='Continue' i]",
]

# Step 4: Edit pencil / edit button on the summary page
EDIT_PENCIL_SELECTORS: list[str] = [
    "button[aria-label*='Edit' i]",
    "[aria-label*='Edit job details' i]",
    "[aria-label*='Edit details' i]",
    "button:has-text('Edit')",
    "[data-control-name*='edit' i]",
    "svg[data-test-icon*='pencil' i]",
    "li-icon[type*='pencil' i]",
    "button .pencil-icon",
]

# Step 5: Description editor — AI-generated or manual
DESCRIPTION_EDITOR_SELECTORS: list[str] = [
    "[data-lexical-editor='true']",
    "[contenteditable='true'][role='textbox']",
    ".ql-editor",
    ".ProseMirror",
    "div[contenteditable='true']",
    "[aria-label*='description' i][contenteditable='true']",
]

# "Use AI" / "Generate" button for AI description
AI_DESCRIPTION_SELECTORS: list[str] = [
    "button:has-text('Write with AI')",
    "button:has-text('Generate')",
    "button:has-text('Use AI')",
    "[aria-label*='AI' i]",
    "[aria-label*='generate description' i]",
]

# "Continue without promoting" button after description
CONTINUE_WITHOUT_PROMOTE_SELECTORS: list[str] = [
    "button:has-text('Continue without promoting')",
    "button:has-text('Continue without')",
    "button:has-text('Skip')",
    "button:has-text('No thanks')",
    "[aria-label*='Continue without' i]",
    "[aria-label*='Skip promoting' i]",
]

# "Post job" button — DRY RUN: detect only, never click
POST_JOB_BUTTON_SELECTORS: list[str] = [
    "button:has-text('Post job')",
    "button:has-text('Post for free')",
    "[aria-label*='Post job' i]",
]

# ---------------------------------------------------------------------------
# Entry point trigger selectors
# (tried in order; first visible match wins)
# ---------------------------------------------------------------------------

ENTRY_POINT_SELECTORS: list[str] = [
    # Jobs homepage "Post a free job" / "Post a job" button
    "a[href*='job-posting']",
    "button:has-text('Post a job')",
    "a:has-text('Post a job')",
    "a:has-text('Post a free job')",
    "[data-control-name*='post_job']",
    "[aria-label*='Post a job' i]",
    # Recruiter dashboard
    "a[href*='/talent/post-a-job']",
    "button:has-text('Post')",
    # Generic fallback
    "[data-test*='post-job']",
    "[class*='post-job']",
]

# ---------------------------------------------------------------------------
# Wizard / form container selectors
# (the root container that scopes all form fields)
# ---------------------------------------------------------------------------

FORM_CONTAINER_SELECTORS: list[str] = [
    # Primary wizard container
    "[data-test-job-posting-form]",
    "[data-test='job-posting-form']",
    "form[action*='job']",
    # Modal / dialog containers (wizard may render inside a dialog)
    "[role='dialog'][aria-label*='job' i]",
    "[role='dialog']",
    ".artdeco-modal__content",
    # LinkedIn SPA main content area — no <form> wrapper in current DOM
    "#workspace",
    "[role='main']",
    "main",
    # Legacy fallbacks
    "[role='main'] form",
    "main form",
    "form",
]

# ---------------------------------------------------------------------------
# Progress / step indicator selectors
# ---------------------------------------------------------------------------

PROGRESS_SELECTORS: list[str] = [
    "[role='progressbar']",
    "[aria-label*='step' i]",
    "[class*='progress']",
    "[class*='stepper']",
    "[class*='wizard']",
    "ol[class*='step']",
    "ul[class*='step']",
    "[data-test*='step']",
    "[aria-valuenow]",
]

# ---------------------------------------------------------------------------
# Navigation button selectors (Next / Back / Publish / Save Draft)
# ---------------------------------------------------------------------------

NEXT_BUTTON_SELECTORS: list[str] = [
    "button:has-text('Next')",
    "button:has-text('Continue')",
    "[aria-label*='Next' i]",
    "[data-control-name*='next']",
    "button[type='submit']:has-text('Next')",
]

BACK_BUTTON_SELECTORS: list[str] = [
    "button:has-text('Back')",
    "button:has-text('Previous')",
    "[aria-label*='Back' i]",
    "[data-control-name*='back']",
]

PUBLISH_BUTTON_SELECTORS: list[str] = [
    "button:has-text('Post job')",
    "button:has-text('Publish')",
    "button:has-text('Post')",
    "button:has-text('Submit')",
    "[aria-label*='Post job' i]",
    "[aria-label*='Publish' i]",
    "[data-control-name*='publish']",
    "[data-control-name*='post_job']",
]

SAVE_DRAFT_SELECTORS: list[str] = [
    "button:has-text('Save draft')",
    "button:has-text('Save as draft')",
    "[aria-label*='Save draft' i]",
    "[data-control-name*='save_draft']",
]

CANCEL_SELECTORS: list[str] = [
    "button:has-text('Cancel')",
    "button:has-text('Discard')",
    "[aria-label*='Cancel' i]",
    "[aria-label*='Close' i]",
    "[data-control-name*='cancel']",
]

# ---------------------------------------------------------------------------
# Known field labels (from LinkedIn's job posting wizard)
# Used to annotate discovered fields with semantic meaning.
# ---------------------------------------------------------------------------

KNOWN_FIELD_LABELS: dict[str, dict] = {
    # Step 1 — Job details
    "Job title": {
        "required": True,
        "field_type": "text_input",
        "autocomplete": True,
        "step_hint": 1,
    },
    "Company": {
        "required": True,
        "field_type": "dropdown_searchable",
        "autocomplete": True,
        "step_hint": 1,
    },
    "Workplace type": {
        "required": True,
        "field_type": "dropdown_aria",
        "options_hint": ["On-site", "Hybrid", "Remote"],
        "step_hint": 1,
    },
    "Job location": {
        "required": True,
        "field_type": "dropdown_searchable",
        "autocomplete": True,
        "step_hint": 1,
    },
    "Job type": {
        "required": True,
        "field_type": "dropdown_aria",
        "options_hint": ["Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Internship", "Other"],
        "step_hint": 1,
    },
    # Step 2 — Job description
    "Description": {
        "required": True,
        "field_type": "rich_text",
        "step_hint": 2,
    },
    "Job description": {
        "required": True,
        "field_type": "rich_text",
        "step_hint": 2,
    },
    # Step 3 — Skills
    "Skills": {
        "required": False,
        "field_type": "multi_select",
        "autocomplete": True,
        "step_hint": 3,
    },
    "Add skills": {
        "required": False,
        "field_type": "multi_select",
        "autocomplete": True,
        "step_hint": 3,
    },
    # Step 4 — Applicant options
    "How would you like to receive applications?": {
        "required": True,
        "field_type": "radio",
        "options_hint": ["Through LinkedIn", "Through an external website"],
        "step_hint": 4,
    },
    "Application email": {
        "required": False,
        "field_type": "text_input",
        "step_hint": 4,
    },
    "External application URL": {
        "required": False,
        "field_type": "text_input",
        "step_hint": 4,
    },
    # Screening questions (optional step)
    "Add screening questions": {
        "required": False,
        "field_type": "multi_select",
        "step_hint": 4,
    },
    # Salary (optional)
    "Salary": {
        "required": False,
        "field_type": "text_input",
        "step_hint": 1,
    },
    "Salary range": {
        "required": False,
        "field_type": "text_input",
        "step_hint": 1,
    },
    # Experience level
    "Experience level": {
        "required": False,
        "field_type": "dropdown_aria",
        "options_hint": ["Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive"],
        "step_hint": 1,
    },
    # Industry
    "Industry": {
        "required": False,
        "field_type": "dropdown_searchable",
        "autocomplete": True,
        "step_hint": 1,
    },
    # Function
    "Job function": {
        "required": False,
        "field_type": "dropdown_searchable",
        "autocomplete": True,
        "step_hint": 1,
    },
}

# ---------------------------------------------------------------------------
# Success signal tokens
# ---------------------------------------------------------------------------

SUCCESS_TOKENS: list[str] = [
    "job posted",
    "job has been posted",
    "successfully posted",
    "your job is live",
    "job is now live",
    "posted successfully",
    "job listing is live",
    "congratulations",
    "view your job",
]

# ---------------------------------------------------------------------------
# Validation error signal selectors
# ---------------------------------------------------------------------------

VALIDATION_ERROR_SELECTORS: list[str] = [
    "[aria-invalid='true']",
    "[class*='error']",
    "[class*='invalid']",
    "[role='alert']",
    "[aria-describedby*='error']",
    ".artdeco-inline-feedback--error",
    "[data-test*='error']",
]

# ---------------------------------------------------------------------------
# Draft support signal tokens
# ---------------------------------------------------------------------------

DRAFT_TOKENS: list[str] = [
    "save draft",
    "saved as draft",
    "draft saved",
    "save as draft",
    "your draft",
]

# ---------------------------------------------------------------------------
# Review page signal tokens
# ---------------------------------------------------------------------------

REVIEW_TOKENS: list[str] = [
    "review",
    "preview",
    "review your job",
    "review job posting",
    "confirm",
    "review and post",
]

# ---------------------------------------------------------------------------
# Rich text editor selectors (for field type detection)
# ---------------------------------------------------------------------------

RICH_TEXT_SELECTORS: list[str] = [
    "[data-lexical-editor='true']",
    "[data-lexical-editor][contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "[contenteditable='true'][aria-multiline='true']",
    ".ql-editor",                    # Quill
    ".ProseMirror",                  # ProseMirror
    "[data-contents='true']",        # Draft.js
    "div[contenteditable='true']",
]

# ---------------------------------------------------------------------------
# File upload selectors
# ---------------------------------------------------------------------------

FILE_UPLOAD_SELECTORS: list[str] = [
    "input[type='file']",
    "[data-test*='upload']",
    "[aria-label*='upload' i]",
    "[class*='upload']",
    "[class*='file-input']",
]

# ---------------------------------------------------------------------------
# Multi-select / tag input selectors
# ---------------------------------------------------------------------------

MULTI_SELECT_SELECTORS: list[str] = [
    "select[multiple]",
    "[role='listbox'][aria-multiselectable='true']",
    "[data-test*='multi-select']",
    "[class*='multi-select']",
    "[class*='tag-input']",
    "[class*='typeahead']",
]

# ---------------------------------------------------------------------------
# Date picker selectors
# ---------------------------------------------------------------------------

DATE_PICKER_SELECTORS: list[str] = [
    "input[type='date']",
    "input[type='datetime-local']",
    "[data-test*='date']",
    "[aria-label*='date' i]",
    "[class*='date-picker']",
    "[class*='datepicker']",
]

# ---------------------------------------------------------------------------
# Dialog selectors
# ---------------------------------------------------------------------------

DIALOG_SELECTORS: list[str] = [
    "[role='dialog']",
    "[role='alertdialog']",
    "dialog",
    ".artdeco-modal",
    "[data-test-modal]",
    "[class*='modal']",
]

# ---------------------------------------------------------------------------
# Heading selectors (for step label extraction)
# ---------------------------------------------------------------------------

HEADING_SELECTORS: list[str] = [
    "h1", "h2", "h3",
    "[role='heading']",
    "[class*='title']",
    "[class*='heading']",
    "[data-test*='title']",
]
