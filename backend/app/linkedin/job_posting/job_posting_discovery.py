"""job_posting_discovery.py — LinkedIn Job Posting discovery module.

DISCOVERY ONLY.
- Never clicks Publish / Post / Submit.
- Never saves a draft.
- Never creates or modifies LinkedIn data.
- Only navigates, inspects, and records.

Uses BrowserManager (read-only) and the Phase 1 infrastructure.
Does NOT modify any existing worker, service, or repository.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.browser_context import BrowserContextConfig
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
)
from app.linkedin.job_posting.job_posting_constants import (
    DATE_PICKER_SELECTORS,
    DIALOG_SELECTORS,
    DIRECT_POST_JOB_URL,
    DRAFT_TOKENS,
    ENTRY_POINT_SELECTORS,
    ENTRY_POINT_URLS,
    FILE_UPLOAD_SELECTORS,
    FORM_CONTAINER_SELECTORS,
    HEADING_SELECTORS,
    JOBS_HOMEPAGE_URL,
    KNOWN_FIELD_LABELS,
    MULTI_SELECT_SELECTORS,
    NEXT_BUTTON_SELECTORS,
    PROGRESS_SELECTORS,
    PUBLISH_BUTTON_SELECTORS,
    REVIEW_TOKENS,
    RICH_TEXT_SELECTORS,
    SAVE_DRAFT_SELECTORS,
    SUCCESS_TOKENS,
    VALIDATION_ERROR_SELECTORS,
)

logger = logging.getLogger(__name__)

_DEBUG_ROOT = Path(__file__).resolve().parents[4] / "debug_logs" / "job_posting_discovery"


# ---------------------------------------------------------------------------
# Internal helpers — debug output
# ---------------------------------------------------------------------------

def _output_dir(run_ts: str) -> Path:
    d = _DEBUG_ROOT / run_ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_json(path: Path, data: Any) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.debug("discovery saved json path=%s", path)
    except Exception as exc:
        logger.warning("discovery json_save_failed path=%s error=%s", path, exc)


async def _screenshot(page: Any, path: Path) -> str:
    try:
        await page.screenshot(path=str(path), full_page=False)
        logger.debug("discovery screenshot saved path=%s", path)
        return str(path)
    except Exception as exc:
        logger.warning("discovery screenshot_failed path=%s error=%s", path, exc)
        return ""


async def _save_html(page: Any, path: Path) -> str:
    try:
        html = await page.locator("body").inner_html(timeout=5000)
        path.write_text(str(html), encoding="utf-8")
        logger.debug("discovery html saved path=%s", path)
        return str(path)
    except Exception as exc:
        logger.warning("discovery html_save_failed path=%s error=%s", path, exc)
        return ""


async def _safe_text(locator: Any, timeout: int = 1000) -> str:
    for method in ("inner_text", "text_content"):
        try:
            val = str(await getattr(locator, method)(timeout=timeout) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


async def _safe_attr(locator: Any, attr: str) -> str:
    try:
        return str(await locator.get_attribute(attr) or "").strip()
    except Exception:
        return ""


async def _is_visible(locator: Any) -> bool:
    try:
        return bool(await locator.is_visible())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Field type detector
# ---------------------------------------------------------------------------

async def _detect_field_type(locator: Any) -> FieldType:
    """Inspect DOM attributes to classify a field's type."""
    try:
        attrs: dict = await locator.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                role: (el.getAttribute('role') || '').toLowerCase(),
                contenteditable: el.getAttribute('contenteditable'),
                lexical: !!el.getAttribute('data-lexical-editor'),
                draftjs: !!(el.getAttribute('data-contents')),
                prosemirror: el.classList.contains('ProseMirror'),
                multiple: el.multiple || false,
                ariaMultiselectable: el.getAttribute('aria-multiselectable'),
            })"""
        )
    except Exception:
        return FieldType.UNKNOWN

    tag = attrs.get("tag", "")
    itype = attrs.get("type", "")
    role = attrs.get("role", "")
    ce = attrs.get("contenteditable", "")

    if attrs.get("lexical") or attrs.get("draftjs") or attrs.get("prosemirror"):
        return FieldType.RICH_TEXT
    if ce == "true":
        return FieldType.RICH_TEXT
    if tag == "textarea":
        return FieldType.TEXTAREA
    if tag == "select":
        return FieldType.DROPDOWN_NATIVE if not attrs.get("multiple") else FieldType.MULTI_SELECT
    if tag == "input":
        if itype == "checkbox":
            return FieldType.CHECKBOX
        if itype == "radio":
            return FieldType.RADIO
        if itype == "file":
            return FieldType.FILE_UPLOAD
        if itype in ("date", "datetime-local", "month"):
            return FieldType.DATE_PICKER
        return FieldType.TEXT_INPUT
    if role == "listbox":
        multi = attrs.get("ariaMultiselectable", "false")
        return FieldType.MULTI_SELECT if multi == "true" else FieldType.DROPDOWN_ARIA
    if role == "combobox":
        return FieldType.DROPDOWN_COMBOBOX
    if role == "switch" or role == "checkbox":
        return FieldType.TOGGLE
    if role == "button":
        return FieldType.BUTTON
    return FieldType.UNKNOWN


# ---------------------------------------------------------------------------
# Single field extractor
# ---------------------------------------------------------------------------

async def _extract_field(
    locator: Any,
    *,
    step_index: int = 0,
    page_url: str = "",
    selector: str = "",
) -> DiscoveredField:
    """Extract all discoverable attributes from a single form element."""
    f = DiscoveredField(step_index=step_index, page_url=page_url, selector=selector)

    try:
        attrs: dict = await locator.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type') || '',
                role: el.getAttribute('role') || '',
                name: el.getAttribute('name') || '',
                id: el.id || '',
                placeholder: el.getAttribute('placeholder') || '',
                required: el.required || el.getAttribute('aria-required') === 'true',
                disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                readonly: el.readOnly || el.getAttribute('aria-readonly') === 'true',
                autocomplete: el.getAttribute('autocomplete') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                ariaDescribedby: el.getAttribute('aria-describedby') || '',
                ariaInvalid: el.getAttribute('aria-invalid') || '',
                value: el.value || '',
                outerHTML: el.outerHTML ? el.outerHTML.slice(0, 800) : '',
                contenteditable: el.getAttribute('contenteditable') || '',
                lexical: !!el.getAttribute('data-lexical-editor'),
            })"""
        )
    except Exception:
        attrs = {}

    f.role = attrs.get("role", "") or attrs.get("tag", "")
    f.input_type = attrs.get("type", "")
    f.placeholder = attrs.get("placeholder", "")
    f.required = bool(attrs.get("required", False))
    f.disabled = bool(attrs.get("disabled", False))
    f.readonly = bool(attrs.get("readonly", False))
    f.autocomplete = attrs.get("autocomplete", "")
    f.accessible_name = attrs.get("ariaLabel", "")
    f.outer_html = attrs.get("outerHTML", "")

    # Current value
    for method in ("input_value", "inner_text"):
        try:
            val = str(await getattr(locator, method)(timeout=800) or "").strip()
            if val:
                f.current_value = val[:200]
                break
        except Exception:
            continue

    # Field type
    f.field_type = await _detect_field_type(locator)

    # Label — try associated <label>, aria-label, placeholder, nearby text
    f.label = await _resolve_label(locator, attrs)

    # Annotate from known field labels
    for known_label, meta in KNOWN_FIELD_LABELS.items():
        if known_label.lower() in f.label.lower() or known_label.lower() in f.accessible_name.lower():
            if not f.required:
                f.required = bool(meta.get("required", False))
            break

    # Options for dropdowns
    if f.field_type in (FieldType.DROPDOWN_NATIVE, FieldType.MULTI_SELECT):
        f.options = await _extract_select_options(locator)

    # Help text via aria-describedby
    described_by = attrs.get("ariaDescribedby", "")
    if described_by:
        f.help_text = await _read_described_by(locator, described_by)

    # Bounding box
    try:
        bb = await locator.bounding_box()
        if bb:
            f.bounding_box = {k: round(v, 1) for k, v in bb.items()}
    except Exception:
        pass

    # Container path
    f.container_path = await _container_path(locator)

    return f


async def _resolve_label(locator: Any, attrs: dict) -> str:
    """Try multiple strategies to find a human-readable label for a field."""
    # 1. aria-label
    aria = attrs.get("ariaLabel", "").strip()
    if aria:
        return aria

    # 2. Associated <label> via id
    field_id = attrs.get("id", "").strip()
    if field_id:
        try:
            label_text = await locator.evaluate(
                f"el => {{ const l = document.querySelector('label[for=\"{field_id}\"]'); "
                f"return l ? l.innerText.trim() : ''; }}"
            )
            if label_text:
                return str(label_text).strip()
        except Exception:
            pass

    # 3. Closest ancestor label
    try:
        label_text = await locator.evaluate(
            "el => { const l = el.closest('label'); return l ? l.innerText.trim() : ''; }"
        )
        if label_text:
            return str(label_text).strip()
    except Exception:
        pass

    # 4. Preceding sibling / parent label text
    try:
        label_text = await locator.evaluate(
            """el => {
                const parent = el.parentElement;
                if (!parent) return '';
                const labels = parent.querySelectorAll('label, legend, [class*=\"label\"], [class*=\"title\"]');
                for (const l of labels) {
                    const t = l.innerText.trim();
                    if (t) return t;
                }
                return '';
            }"""
        )
        if label_text:
            return str(label_text).strip()
    except Exception:
        pass

    # 5. Placeholder as last resort
    placeholder = attrs.get("placeholder", "").strip()
    if placeholder:
        return placeholder

    return attrs.get("name", "").strip()


async def _extract_select_options(locator: Any) -> list[str]:
    try:
        options: list = await locator.evaluate(
            "el => Array.from(el.options || []).map(o => o.text.trim()).filter(Boolean)"
        )
        return [str(o) for o in options[:50]]
    except Exception:
        return []


async def _read_described_by(locator: Any, described_by_id: str) -> str:
    try:
        text = await locator.evaluate(
            f"el => {{ const d = document.getElementById('{described_by_id}'); "
            f"return d ? d.innerText.trim() : ''; }}"
        )
        return str(text or "").strip()[:300]
    except Exception:
        return ""


async def _container_path(locator: Any) -> str:
    try:
        path: str = await locator.evaluate(
            """el => {
                const parts = [];
                let node = el.parentElement;
                let depth = 0;
                while (node && depth < 5) {
                    const tag = node.tagName.toLowerCase();
                    const id = node.id ? '#' + node.id : '';
                    const cls = node.className
                        ? '.' + node.className.trim().split(/\\s+/).slice(0, 2).join('.')
                        : '';
                    parts.unshift(tag + id + cls);
                    node = node.parentElement;
                    depth++;
                }
                return parts.join(' > ');
            }"""
        )
        return str(path or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Button extractor
# ---------------------------------------------------------------------------

async def _extract_buttons(container: Any) -> list[DiscoveredButton]:
    """Collect all visible buttons inside *container*."""
    buttons: list[DiscoveredButton] = []
    seen: set[str] = set()

    for sel in ("button", "[role='button']", "input[type='submit']", "input[type='button']"):
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 30)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                label = await _safe_text(item)
                aria = await _safe_attr(item, "aria-label")
                key = (label or aria).lower().strip()
                if not key or key in seen:
                    continue
                seen.add(key)

                disabled = False
                try:
                    disabled = bool(await item.evaluate("el => el.disabled || false"))
                except Exception:
                    pass

                btn = DiscoveredButton(
                    label=label,
                    aria_label=aria,
                    role=await _safe_attr(item, "role") or "button",
                    disabled=disabled,
                    selector=sel,
                )
                kl = key
                btn.is_next = any(t in kl for t in ("next", "continue"))
                btn.is_back = any(t in kl for t in ("back", "previous"))
                btn.is_publish = any(t in kl for t in ("post job", "publish", "post", "submit"))
                btn.is_save_draft = any(t in kl for t in ("save draft", "save as draft"))
                btn.is_cancel = any(t in kl for t in ("cancel", "discard", "close"))
                btn.is_submit = btn.is_publish

                buttons.append(btn)
        except Exception:
            continue

    return buttons


# ---------------------------------------------------------------------------
# Dialog extractor
# ---------------------------------------------------------------------------

async def _extract_dialogs(page: Any) -> list[DiscoveredDialog]:
    """Collect all visible dialogs on the page."""
    dialogs: list[DiscoveredDialog] = []
    for sel in DIALOG_SELECTORS:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 5)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                body = await _safe_text(item, timeout=2000)
                title = ""
                for hsel in ("h1", "h2", "h3", "[role='heading']"):
                    try:
                        h = item.locator(hsel).first
                        if await _is_visible(h):
                            title = await _safe_text(h)
                            break
                    except Exception:
                        continue

                btns = await _extract_buttons(item)
                body_lower = body.lower()
                dlg = DiscoveredDialog(
                    title=title,
                    body_text=body[:500],
                    buttons=btns,
                    selector=sel,
                    is_confirmation=any(t in body_lower for t in ("confirm", "are you sure", "review")),
                    is_error=any(t in body_lower for t in ("error", "invalid", "required", "failed")),
                    is_premium_gate=any(t in body_lower for t in ("premium", "upgrade", "recruiter")),
                )
                dialogs.append(dlg)
        except Exception:
            continue
    return dialogs


# ---------------------------------------------------------------------------
# Progress extractor
# ---------------------------------------------------------------------------

async def _extract_progress(page: Any) -> tuple[str, float]:
    """Return (progress_text, progress_percent) from any visible progress indicator."""
    for sel in PROGRESS_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not await _is_visible(loc):
                continue
            text = await _safe_text(loc)
            percent = 0.0
            try:
                val = await _safe_attr(loc, "aria-valuenow")
                max_val = await _safe_attr(loc, "aria-valuemax") or "100"
                if val:
                    percent = round(float(val) / float(max_val) * 100, 1)
            except Exception:
                pass
            if text or percent:
                return text, percent
        except Exception:
            continue
    return "", 0.0


# ---------------------------------------------------------------------------
# Heading extractor
# ---------------------------------------------------------------------------

async def _extract_headings(container: Any) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for sel in HEADING_SELECTORS:
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 10)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                text = await _safe_text(item)
                if text and text not in seen:
                    seen.add(text)
                    headings.append(text)
        except Exception:
            continue
    return headings


# ---------------------------------------------------------------------------
# Form container resolver
# ---------------------------------------------------------------------------

async def _find_form_container(page: Any) -> Any | None:
    """Return the first visible form container on the page."""
    for sel in FORM_CONTAINER_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await _is_visible(loc):
                logger.debug("discovery form_container found sel=%s", sel)
                return loc
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Hidden field extractor
# ---------------------------------------------------------------------------

async def _extract_hidden_fields(page: Any) -> list[DiscoveredHiddenField]:
    """Collect all input[type=hidden] on the page — reveals form structure."""
    try:
        raw: list[dict] = await page.evaluate(
            """() => Array.from(
                document.querySelectorAll("input[type='hidden']"),
                el => ({ name: el.name || '', value: el.value || '', id: el.id || '' })
            ).slice(0, 60)"""
        )
        return [
            DiscoveredHiddenField(name=r["name"], value=r["value"][:200], id=r["id"])
            for r in raw
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Section extractor
# ---------------------------------------------------------------------------

async def _extract_sections(container: Any) -> list[DiscoveredSection]:
    """Collect named sections / fieldsets inside the form container."""
    sections: list[DiscoveredSection] = []
    seen: set[str] = set()
    for sel in (
        "fieldset",
        "section",
        "[role='group']",
        "[class*='section']",
        "[class*='fieldset']",
        "[class*='form-group']",
        "[class*='form-section']",
    ):
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                # Label from legend / aria-label / first heading
                label = ""
                for lsel in ("legend", "[aria-label]", "h2", "h3", "[role='heading']"):
                    try:
                        l = item.locator(lsel).first
                        if await _is_visible(l):
                            label = await _safe_text(l) or await _safe_attr(l, "aria-label")
                            if label:
                                break
                    except Exception:
                        continue
                key = label or sel
                if key in seen:
                    continue
                seen.add(key)
                # Count fields inside
                field_count = 0
                try:
                    field_count = await item.locator(
                        "input:not([type='hidden']), textarea, select, [contenteditable='true']"
                    ).count()
                except Exception:
                    pass
                # Preview of outerHTML
                html_preview = ""
                try:
                    html_preview = str(
                        await item.evaluate("el => el.outerHTML.slice(0, 300)") or ""
                    )
                except Exception:
                    pass
                sections.append(DiscoveredSection(
                    label=label,
                    selector=sel,
                    field_count=field_count,
                    outer_html_preview=html_preview,
                ))
        except Exception:
            continue
    return sections


# ---------------------------------------------------------------------------
# Autocomplete widget extractor
# ---------------------------------------------------------------------------

async def _extract_autocomplete_widgets(container: Any) -> list[DiscoveredAutocomplete]:
    """Find all autocomplete / typeahead widgets inside the container."""
    widgets: list[DiscoveredAutocomplete] = []
    seen: set[str] = set()
    for sel in (
        "[aria-autocomplete]",
        "[role='combobox']",
        "[aria-haspopup='listbox']",
        "[aria-haspopup='true']",
        "[class*='typeahead']",
        "[class*='autocomplete']",
    ):
        try:
            locs = container.locator(sel)
            count = await locs.count()
            for i in range(min(count, 15)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                aria_label = await _safe_attr(item, "aria-label")
                key = aria_label or sel + str(i)
                if key in seen:
                    continue
                seen.add(key)
                widgets.append(DiscoveredAutocomplete(
                    label=aria_label,
                    selector=sel,
                    input_selector=sel,
                    aria_autocomplete=await _safe_attr(item, "aria-autocomplete"),
                    aria_expanded=await _safe_attr(item, "aria-expanded"),
                    aria_haspopup=await _safe_attr(item, "aria-haspopup"),
                ))
        except Exception:
            continue
    return widgets


# ---------------------------------------------------------------------------
# Validation message extractor
# ---------------------------------------------------------------------------

async def _extract_validation_messages(page: Any) -> list[str]:
    """Collect any visible validation / error messages on the page."""
    messages: list[str] = []
    seen: set[str] = set()
    for sel in VALIDATION_ERROR_SELECTORS:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                text = await _safe_text(item)
                if text and text not in seen:
                    seen.add(text)
                    messages.append(text[:300])
        except Exception:
            continue
    return messages


# ---------------------------------------------------------------------------
# DOM snapshot saver
# ---------------------------------------------------------------------------

async def _save_dom_snapshot(page: Any, path: Path) -> str:
    """Save the full outerHTML of the form container (or body) as a DOM snapshot."""
    try:
        container = await _find_form_container(page)
        if container is not None:
            html = str(await container.evaluate("el => el.outerHTML") or "")
        else:
            html = str(await page.locator("body").inner_html(timeout=5000) or "")
        path.write_text(html, encoding="utf-8")
        logger.debug("discovery dom_snapshot saved path=%s", path)
        return str(path)
    except Exception as exc:
        logger.warning("discovery dom_snapshot_failed path=%s error=%s", path, exc)
        return ""


# ---------------------------------------------------------------------------
# Step scanner — captures everything on the current wizard step
# ---------------------------------------------------------------------------

_FIELD_SELECTORS = [
    "input:not([type='hidden']):not([type='submit']):not([type='button'])",
    "textarea",
    "select",
    "[contenteditable='true']",
    "[role='combobox']",
    "[role='listbox']",
    "[role='switch']",
]


async def _scan_step(
    page: Any,
    *,
    step_index: int,
    out_dir: Path,
    step_label: str = "",
) -> DiscoveredStep:
    """Capture the full state of the current wizard step."""
    url = str(getattr(page, "url", "") or "")
    title = ""
    try:
        title = str(await page.title() or "").strip()
    except Exception:
        pass

    step = DiscoveredStep(
        step_index=step_index,
        step_label=step_label,
        url=url,
        title=title,
    )

    # Screenshot + HTML
    prefix = out_dir / f"step{step_index}"
    step.screenshot_path = await _screenshot(page, Path(str(prefix) + ".png"))
    step.html_path = await _save_html(page, Path(str(prefix) + ".html"))

    # DOM snapshot
    step.dom_snapshot_path = await _save_dom_snapshot(page, Path(str(prefix) + "_dom.html"))

    # Headings
    try:
        step.headings = await _extract_headings(page.locator("body"))
    except Exception:
        pass

    # Progress
    step.progress_text, step.progress_percent = await _extract_progress(page)
    progress_path = Path(str(prefix) + "_progress.json")
    _save_json(progress_path, {"text": step.progress_text, "percent": step.progress_percent})
    step.progress_json_path = str(progress_path)

    # Dialogs
    step.dialogs = await _extract_dialogs(page)
    dialogs_path = Path(str(prefix) + "_dialogs.json")
    _save_json(dialogs_path, [asdict(d) for d in step.dialogs])
    step.dialogs_json_path = str(dialogs_path)

    # Hidden fields
    step.hidden_fields = await _extract_hidden_fields(page)

    # Validation messages
    step.validation_messages = await _extract_validation_messages(page)

    # Form container
    container = await _find_form_container(page)
    scan_root = container if container is not None else page.locator("body")

    # Sections
    step.sections = await _extract_sections(scan_root)

    # Autocomplete widgets
    step.autocomplete_widgets = await _extract_autocomplete_widgets(scan_root)
    step.has_autocomplete = bool(step.autocomplete_widgets)

    # Buttons
    step.buttons = await _extract_buttons(scan_root)
    buttons_path = Path(str(prefix) + "_buttons.json")
    _save_json(buttons_path, [asdict(b) for b in step.buttons])
    step.buttons_json_path = str(buttons_path)

    # Fields
    fields: list[DiscoveredField] = []
    seen_selectors: set[str] = set()

    for sel in _FIELD_SELECTORS:
        try:
            locs = scan_root.locator(sel)
            count = await locs.count()
            for i in range(min(count, 40)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                # Deduplicate by outer HTML prefix
                try:
                    key = str(await item.evaluate("el => el.outerHTML.slice(0, 120)") or "")
                    if key in seen_selectors:
                        continue
                    seen_selectors.add(key)
                except Exception:
                    pass

                f = await _extract_field(
                    item,
                    step_index=step_index,
                    page_url=url,
                    selector=sel,
                )
                fields.append(f)
        except Exception:
            continue

    # Also probe known rich-text selectors explicitly
    for sel in RICH_TEXT_SELECTORS:
        try:
            locs = scan_root.locator(sel)
            count = await locs.count()
            for i in range(min(count, 5)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                key = str(await item.evaluate("el => el.outerHTML.slice(0, 120)") or "")
                if key in seen_selectors:
                    continue
                seen_selectors.add(key)
                f = await _extract_field(item, step_index=step_index, page_url=url, selector=sel)
                f.field_type = FieldType.RICH_TEXT
                fields.append(f)
        except Exception:
            continue

    step.fields = fields

    # Aggregate flags
    step.has_rich_text = any(f.field_type == FieldType.RICH_TEXT for f in fields)
    step.has_file_upload = any(f.field_type == FieldType.FILE_UPLOAD for f in fields)
    step.has_dropdown = any(f.field_type in (
        FieldType.DROPDOWN_NATIVE, FieldType.DROPDOWN_ARIA,
        FieldType.DROPDOWN_COMBOBOX, FieldType.DROPDOWN_SEARCHABLE,
    ) for f in fields)
    step.has_multi_select = any(f.field_type == FieldType.MULTI_SELECT for f in fields)
    step.has_date_picker = any(f.field_type == FieldType.DATE_PICKER for f in fields)

    # Save fields JSON
    fields_path = Path(str(prefix) + "_fields.json")
    _save_json(fields_path, [asdict(f) for f in fields])
    step.fields_json_path = str(fields_path)

    logger.info(
        "discovery step_scanned step=%d url=%s fields=%d buttons=%d headings=%s",
        step_index, url, len(fields), len(step.buttons),
        step.headings[:2],
    )
    return step


# ---------------------------------------------------------------------------
# Entry point prober
# ---------------------------------------------------------------------------

async def _probe_entry_points(page: Any) -> list[DiscoveredEntryPoint]:
    """Visit known entry point URLs and record which ones expose a job-posting trigger."""
    results: list[DiscoveredEntryPoint] = []

    for url in ENTRY_POINT_URLS:
        ep = DiscoveredEntryPoint(url=url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1.5)

            current_url = str(getattr(page, "url", "") or "")

            # Detect login wall
            title = str(await page.title() or "").lower()
            body = ""
            try:
                body = str(await page.locator("body").inner_text(timeout=3000) or "").lower()
            except Exception:
                pass

            if any(t in f"{current_url} {title} {body}" for t in ("sign in", "login", "log in")):
                ep.notes = "login_required"
                ep.reachable = False
                results.append(ep)
                logger.info("discovery entry_point url=%s status=login_required", url)
                continue

            # Look for a job-posting trigger
            for sel in ENTRY_POINT_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await _is_visible(loc):
                        ep.trigger_selector = sel
                        ep.trigger_label = await _safe_text(loc) or await _safe_attr(loc, "aria-label")
                        ep.reachable = True
                        break
                except Exception:
                    continue

            # Classify entry point kind
            if "jobs" in url:
                ep.kind = EntryPointKind.JOBS_HOMEPAGE
            elif "talent" in url:
                ep.kind = EntryPointKind.RECRUITER_DASHBOARD
            elif "business" in url:
                ep.kind = EntryPointKind.BUSINESS_MANAGER
            elif "company" in url:
                ep.kind = EntryPointKind.COMPANY_PAGE
            elif "job-posting" in url:
                ep.kind = EntryPointKind.DIRECT_URL
            else:
                ep.kind = EntryPointKind.UNKNOWN

            ep.notes = f"title={title[:60]}"
            results.append(ep)
            logger.info(
                "discovery entry_point url=%s kind=%s reachable=%s trigger=%r",
                url, ep.kind, ep.reachable, ep.trigger_selector,
            )
        except Exception as exc:
            ep.notes = f"error={exc}"
            ep.reachable = False
            results.append(ep)
            logger.warning("discovery entry_point_probe_failed url=%s error=%s", url, exc)

    return results


# ---------------------------------------------------------------------------
# Workflow navigator — walks through wizard steps WITHOUT submitting
# ---------------------------------------------------------------------------

async def _navigate_workflow(
    page: Any,
    *,
    out_dir: Path,
    result: JobPostingDiscoveryResult,
    max_steps: int = 10,
) -> None:
    """Navigate the job posting wizard step by step.

    SAFETY RULES enforced here:
      - Never clicks Publish / Post / Submit.
      - Never clicks Save Draft.
      - Stops if a Publish button is the only forward option.
      - Stops after max_steps to prevent infinite loops.
    """
    urls_visited: list[str] = []
    steps: list[DiscoveredStep] = []
    step_index = 0

    while step_index < max_steps:
        await asyncio.sleep(0.8)  # let DOM settle

        current_url = str(getattr(page, "url", "") or "")
        if current_url not in urls_visited:
            urls_visited.append(current_url)

        logger.info("discovery workflow step=%d url=%s", step_index, current_url)

        # Scan current step
        step = await _scan_step(
            page,
            step_index=step_index,
            out_dir=out_dir,
            step_label=await _extract_live_step_label(page),
        )
        steps.append(step)

        # Check for draft support
        body_lower = " ".join(step.headings).lower()
        if any(t in body_lower for t in DRAFT_TOKENS):
            result.has_draft_support = True
        for btn in step.buttons:
            if btn.is_save_draft:
                result.has_draft_support = True

        # Check for review page
        if any(t in body_lower for t in REVIEW_TOKENS):
            result.has_review_page = True

        # Check for success signal
        try:
            page_text = str(await page.locator("body").inner_text(timeout=2000) or "").lower()
            for token in SUCCESS_TOKENS:
                if token in page_text:
                    result.success_signal = token
                    break
        except Exception:
            pass

        # Check for validation pattern
        for sel in VALIDATION_ERROR_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await _is_visible(loc):
                    result.validation_pattern = sel
                    break
            except Exception:
                continue

        # Find the Next button — NEVER click Publish
        next_btn = await _find_safe_next_button(page, step.buttons)

        if next_btn is None:
            logger.info(
                "discovery workflow no_safe_next_button step=%d — stopping navigation",
                step_index,
            )
            break

        # Capture URL before click to detect navigation kind
        url_before = str(getattr(page, "url", "") or "")

        logger.info("discovery workflow clicking_next step=%d selector=%s", step_index, next_btn)
        try:
            loc = page.locator(next_btn).first
            if not await _is_visible(loc):
                logger.info("discovery workflow next_button_not_visible step=%d", step_index)
                break
            await loc.click(timeout=8000)
            await asyncio.sleep(1.2)
        except Exception as exc:
            logger.warning("discovery workflow next_click_failed step=%d error=%s", step_index, exc)
            break

        # Detect navigation kind
        url_after = str(getattr(page, "url", "") or "")
        if url_after != url_before:
            step.navigation_kind = NavigationKind.URL_CHANGE
        else:
            step.navigation_kind = NavigationKind.STEP_FORWARD

        step_index += 1

    result.steps = steps
    result.urls_visited = urls_visited
    result.total_steps = len(steps)


def _infer_step_label(step_index: int, page: Any) -> str:
    """Fallback step label — only used when live DOM extraction returns nothing."""
    _KNOWN_STEPS = {
        0: "Job details",
        1: "Job description",
        2: "Skills",
        3: "Applicant options",
        4: "Review",
    }
    return _KNOWN_STEPS.get(step_index, f"Step {step_index + 1}")


async def _extract_live_step_label(page: Any) -> str:
    """Extract the current wizard step label directly from the live DOM.

    Tries multiple strategies in order:
    1. Active step in a stepper / progress nav (aria-current, aria-selected)
    2. Primary heading inside the form container
    3. Page <h1> / <h2>
    4. Page <title> stripped of " | LinkedIn" suffix
    """
    # Strategy 1 — active stepper item
    for sel in (
        "[aria-current='step']",
        "[aria-current='true']",
        "[aria-selected='true']",
        "[class*='step'][class*='active']",
        "[class*='stepper'] [class*='active']",
        "li[class*='active']",
    ):
        try:
            loc = page.locator(sel).first
            if await _is_visible(loc):
                text = await _safe_text(loc)
                if text and len(text) < 80:
                    return text
        except Exception:
            continue

    # Strategy 2 — primary heading inside the form container
    container = await _find_form_container(page)
    if container is not None:
        for hsel in ("h1", "h2", "legend", "[role='heading']"):
            try:
                loc = container.locator(hsel).first
                if await _is_visible(loc):
                    text = await _safe_text(loc)
                    if text and len(text) < 120:
                        return text
            except Exception:
                continue

    # Strategy 3 — page h1 / h2
    for hsel in ("h1", "h2"):
        try:
            loc = page.locator(hsel).first
            if await _is_visible(loc):
                text = await _safe_text(loc)
                if text and len(text) < 120:
                    return text
        except Exception:
            continue

    # Strategy 4 — page title
    try:
        title = str(await page.title() or "").strip()
        return title.split(" | ")[0].strip()
    except Exception:
        return ""


async def _find_safe_next_button(page: Any, buttons: list[DiscoveredButton]) -> str | None:
    """Return a CSS selector for the Next button, or None if only Publish is available.

    SAFETY: Never returns a selector that would trigger Publish / Post / Submit.
    """
    # First check buttons already discovered in the step scan
    for btn in buttons:
        if btn.is_next and not btn.disabled and not btn.is_publish:
            # Re-verify it's still visible on the live page
            for sel in NEXT_BUTTON_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await _is_visible(loc):
                        label = (await _safe_text(loc)).lower()
                        if any(t in label for t in ("next", "continue")):
                            return sel
                except Exception:
                    continue

    # Direct selector probe
    for sel in NEXT_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not await _is_visible(loc):
                continue
            label = (await _safe_text(loc)).lower()
            # Reject if it looks like a publish/submit action
            if any(t in label for t in ("post", "publish", "submit")):
                logger.info("discovery safe_next rejected publish-like button label=%r", label)
                continue
            disabled = bool(await loc.evaluate("el => el.disabled || false"))
            if not disabled:
                return sel
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Result aggregator
# ---------------------------------------------------------------------------

def _aggregate_fields(result: JobPostingDiscoveryResult) -> None:
    """Flatten all per-step fields into result-level lists."""
    all_fields: list[DiscoveredField] = []
    for step in result.steps:
        all_fields.extend(step.fields)
    result.all_fields = all_fields

    result.required_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.required and (f.label or f.accessible_name)
    })
    result.optional_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if not f.required and (f.label or f.accessible_name)
    })
    result.rich_text_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.field_type == FieldType.RICH_TEXT and (f.label or f.accessible_name)
    })
    result.dropdown_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.field_type in (
            FieldType.DROPDOWN_NATIVE, FieldType.DROPDOWN_ARIA,
            FieldType.DROPDOWN_COMBOBOX, FieldType.DROPDOWN_SEARCHABLE,
        ) and (f.label or f.accessible_name)
    })
    result.autocomplete_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.autocomplete and (f.label or f.accessible_name)
    })
    result.multi_select_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.field_type == FieldType.MULTI_SELECT and (f.label or f.accessible_name)
    })
    result.upload_fields = sorted({
        f.label or f.accessible_name
        for f in all_fields
        if f.field_type == FieldType.FILE_UPLOAD and (f.label or f.accessible_name)
    })


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def _write_outputs(result: JobPostingDiscoveryResult, out_dir: Path) -> None:
    """Write overview.json and workflow.json to the output directory."""
    # overview.json — high-level summary
    overview = {
        "status": result.status,
        "account_id": result.account_id,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "duration_ms": result.duration_ms,
        "total_steps": result.total_steps,
        "urls_visited": result.urls_visited,
        "entry_points": [asdict(ep) for ep in result.entry_points],
        "primary_entry_point": asdict(result.primary_entry_point),
        "has_draft_support": result.has_draft_support,
        "has_review_page": result.has_review_page,
        "success_signal": result.success_signal,
        "validation_pattern": result.validation_pattern,
        "required_fields": result.required_fields,
        "optional_fields": result.optional_fields,
        "rich_text_fields": result.rich_text_fields,
        "dropdown_fields": result.dropdown_fields,
        "autocomplete_fields": result.autocomplete_fields,
        "multi_select_fields": result.multi_select_fields,
        "upload_fields": result.upload_fields,
        "errors": result.errors,
        "warnings": result.warnings,
    }
    overview_path = out_dir / "overview.json"
    _save_json(overview_path, overview)
    result.overview_json_path = str(overview_path)

    # workflow.json — per-step detail
    workflow = {
        "total_steps": result.total_steps,
        "steps": [
            {
                "step_index": s.step_index,
                "step_label": s.step_label,
                "url": s.url,
                "title": s.title,
                "headings": s.headings,
                "progress_text": s.progress_text,
                "progress_percent": s.progress_percent,
                "navigation_kind": s.navigation_kind,
                "has_rich_text": s.has_rich_text,
                "has_file_upload": s.has_file_upload,
                "has_dropdown": s.has_dropdown,
                "has_multi_select": s.has_multi_select,
                "has_date_picker": s.has_date_picker,
                "field_count": len(s.fields),
                "button_labels": [b.label for b in s.buttons],
                "dialog_count": len(s.dialogs),
                "screenshot_path": s.screenshot_path,
                "html_path": s.html_path,
                "fields_json_path": s.fields_json_path,
                "buttons_json_path": s.buttons_json_path,
                "dialogs_json_path": s.dialogs_json_path,
                "progress_json_path": s.progress_json_path,
                "dom_snapshot_path": s.dom_snapshot_path,
                "has_autocomplete": s.has_autocomplete,
                "section_count": len(s.sections),
                "section_labels": [sec.label for sec in s.sections],
                "hidden_field_names": [h.name for h in s.hidden_fields],
                "validation_messages": s.validation_messages,
                "autocomplete_labels": [a.label for a in s.autocomplete_widgets],
            }
            for s in result.steps
        ],
    }
    workflow_path = out_dir / "workflow.json"
    _save_json(workflow_path, workflow)
    result.workflow_json_path = str(workflow_path)

    logger.info(
        "discovery outputs written overview=%s workflow=%s",
        overview_path, workflow_path,
    )


# ---------------------------------------------------------------------------
# Main discovery class
# ---------------------------------------------------------------------------

class JobPostingDiscovery:
    """LinkedIn Job Posting discovery runner.

    DISCOVERY ONLY — never publishes, never saves, never modifies data.

    Usage:
        discovery = JobPostingDiscovery(account_id="linkedin-dev-account")
        result = await discovery.run()
        print(result.overview_json_path)
    """

    def __init__(
        self,
        account_id: str,
        *,
        config: BrowserContextConfig | None = None,
        max_steps: int = 10,
    ) -> None:
        self._account_id = account_id
        self._config = config or BrowserContextConfig()
        self._max_steps = max_steps
        self._browser_manager = BrowserManager(
            account_id=account_id,
            config=self._config,
        )

    async def run(self) -> JobPostingDiscoveryResult:
        """Execute the full discovery run and return a structured result."""
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _output_dir(run_ts)
        started_ms = time.monotonic()

        result = JobPostingDiscoveryResult(
            account_id=self._account_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            output_dir=str(out_dir),
        )

        logger.info(
            "discovery run started account_id=%s output_dir=%s",
            self._account_id, out_dir,
        )

        context = None
        page = None
        try:
            context = await self._browser_manager.get_browser()
            page = await context.new_page()
            if hasattr(page, "set_default_timeout"):
                page.set_default_timeout(30000)
            if hasattr(page, "set_default_navigation_timeout"):
                page.set_default_navigation_timeout(15000)

            # ── Phase A: probe entry points ───────────────────────────────
            logger.info("discovery phase=entry_point_probe")
            result.entry_points = await _probe_entry_points(page)

            # Select primary entry point (prefer direct URL, then jobs homepage)
            primary = next(
                (ep for ep in result.entry_points if ep.kind == EntryPointKind.DIRECT_URL and ep.reachable),
                next(
                    (ep for ep in result.entry_points if ep.kind == EntryPointKind.JOBS_HOMEPAGE and ep.reachable),
                    next((ep for ep in result.entry_points if ep.reachable), DiscoveredEntryPoint()),
                ),
            )
            result.primary_entry_point = primary
            logger.info(
                "discovery primary_entry_point kind=%s url=%s reachable=%s",
                primary.kind, primary.url, primary.reachable,
            )

            # ── Phase B: navigate to the job posting wizard ───────────────
            logger.info("discovery phase=wizard_navigation")
            wizard_url = DIRECT_POST_JOB_URL

            # If direct URL is not reachable, try the jobs homepage trigger
            if not primary.reachable:
                result.warnings.append("No reachable entry point found — attempting direct URL anyway")
                logger.warning("discovery no_reachable_entry_point — trying direct url")

            try:
                await page.goto(wizard_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2.0)
            except Exception as exc:
                result.errors.append(f"wizard_navigation_failed: {exc}")
                logger.error("discovery wizard_navigation_failed error=%s", exc)
                result.status = DiscoveryStatus.FAILED
                return result

            # Check for login wall after navigation
            current_url = str(getattr(page, "url", "") or "")
            title = str(await page.title() or "").lower()
            try:
                body = str(await page.locator("body").inner_text(timeout=3000) or "").lower()
            except Exception:
                body = ""

            if any(t in f"{current_url} {title} {body}" for t in ("sign in", "login", "log in")):
                result.status = DiscoveryStatus.LOGIN_REQUIRED
                result.errors.append("login_required after wizard navigation")
                logger.warning("discovery login_required after wizard navigation")
                # Still capture what we can
                step = await _scan_step(page, step_index=0, out_dir=out_dir, step_label="Login wall")
                result.steps = [step]
                result.total_steps = 1
                result.urls_visited = [current_url]
                _write_outputs(result, out_dir)
                return result

            # ── Phase C: walk the wizard steps ───────────────────────────
            logger.info("discovery phase=step_walk")
            await _navigate_workflow(
                page,
                out_dir=out_dir,
                result=result,
                max_steps=self._max_steps,
            )

            # ── Phase D: aggregate and write outputs ──────────────────────
            _aggregate_fields(result)
            result.status = DiscoveryStatus.OK if result.steps else DiscoveryStatus.PARTIAL
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            _write_outputs(result, out_dir)

            logger.info(
                "discovery run completed status=%s steps=%d fields=%d duration_ms=%d",
                result.status, result.total_steps, len(result.all_fields), result.duration_ms,
            )
            return result

        except Exception as exc:
            logger.exception("discovery run failed account_id=%s", self._account_id)
            result.errors.append(str(exc))
            result.status = DiscoveryStatus.FAILED
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            try:
                _write_outputs(result, out_dir)
            except Exception:
                pass
            return result

        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            try:
                await self._browser_manager.stop()
            except Exception:
                logger.debug("discovery browser_stop_failed", exc_info=True)
