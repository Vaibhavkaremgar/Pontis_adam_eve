"""click_audit.py — Message button click audit instrumentation.

Pure evidence collection.  No business logic changes.
Instruments:
  1. Every visible candidate button before selection.
  2. Exact element chosen (text, aria-label, role, href, class, data-testid,
     outerHTML, bounding box).
  3. Screenshots with .png extension before and after click.
  4. Container classification (profile header / overlay / other).
  5. Action type verification (Message / Premium / InMail / Connect / other).
  6. Final report explaining why Premium dialog appears instead of composer.

Output: debug_logs/click_audit/<YYYYMMDDTHHMMSS>_<slug>/
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUT_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "click_audit"

# ---------------------------------------------------------------------------
# JS payloads — read-only
# ---------------------------------------------------------------------------

_JS_ELEMENT_DETAIL = """el => {
    const r = el.getBoundingClientRect();
    const header = el.closest('header, [data-view-name*="profile"] header, main header');
    const overlay = el.closest(
        '[class*="overlay"], [class*="modal"], [role="dialog"], [role="alertdialog"], ' +
        '[class*="msg-overlay"], [class*="msg-convo"]'
    );
    const toolbar = el.closest(
        '[data-view-name*="profile-actions"], .pvs-profile-actions, ' +
        '[role="toolbar"], [data-view-name*="actions"]'
    );
    let container = 'other';
    if (header)   container = 'profile_header';
    else if (overlay) container = 'overlay';
    else if (toolbar) container = 'profile_toolbar';

    const text = (el.innerText || el.textContent || '').trim().slice(0, 200);
    const ariaLabel = el.getAttribute('aria-label') || '';
    const combined = (text + ' ' + ariaLabel).toLowerCase();

    let actionType = 'other';
    if (/^message$/i.test(text.trim()) || /^message$/i.test(ariaLabel.trim()))
        actionType = 'Message';
    else if (/inmail/i.test(combined))
        actionType = 'InMail';
    else if (/premium|upgrade|try premium/i.test(combined))
        actionType = 'Premium';
    else if (/^connect$/i.test(text.trim()) || /^connect$/i.test(ariaLabel.trim()))
        actionType = 'Connect';
    else if (/message/i.test(combined))
        actionType = 'Message_partial';

    return {
        tag:         el.tagName,
        role:        el.getAttribute('role') || '',
        ariaLabel:   ariaLabel,
        href:        el.getAttribute('href') || '',
        class:       el.className || '',
        dataTestid:  el.getAttribute('data-testid') || '',
        text:        text,
        outerHTML:   el.outerHTML.slice(0, 600),
        bbox:        {x: r.x, y: r.y, w: r.width, h: r.height},
        container:   container,
        actionType:  actionType,
        disabled:    el.disabled || false,
        visible:     r.width > 0 && r.height > 0,
    };
}"""

_JS_ALL_CANDIDATES = """() => {
    const selectors = [
        'button',
        '[role="button"]',
        'a[role="button"]',
        '[aria-label]',
        'a[href^="/messaging/"]',
    ];
    const seen = new Set();
    const results = [];
    selectors.forEach(sel => {
        let nodes;
        try { nodes = document.querySelectorAll(sel); } catch(e) { return; }
        nodes.forEach(el => {
            if (seen.has(el)) return;
            seen.add(el);
            const r = el.getBoundingClientRect();
            const cs = window.getComputedStyle(el);
            const visible = r.width > 0 && r.height > 0
                && cs.visibility !== 'hidden'
                && cs.display !== 'none'
                && cs.opacity !== '0';
            if (!visible) return;

            const text = (el.innerText || el.textContent || '').trim().slice(0, 120);
            const ariaLabel = el.getAttribute('aria-label') || '';
            const combined = (text + ' ' + ariaLabel).toLowerCase();

            // Only log action-relevant buttons
            if (!/message|connect|follow|inmail|premium|more|pending|withdraw|remove/i.test(combined))
                return;

            const header  = el.closest('header, main header');
            const overlay = el.closest('[role="dialog"],[class*="overlay"],[class*="modal"]');
            const toolbar = el.closest('[role="toolbar"],[data-view-name*="actions"],[class*="pvs-profile-actions"]');
            let container = 'other';
            if (header)   container = 'profile_header';
            else if (overlay) container = 'overlay';
            else if (toolbar) container = 'profile_toolbar';

            results.push({
                matchedSel:  sel,
                tag:         el.tagName,
                role:        el.getAttribute('role') || '',
                ariaLabel:   ariaLabel,
                href:        el.getAttribute('href') || '',
                class:       el.className || '',
                dataTestid:  el.getAttribute('data-testid') || '',
                text:        text,
                outerHTML:   el.outerHTML.slice(0, 400),
                bbox:        {x: r.x, y: r.y, w: r.width, h: r.height},
                container:   container,
                disabled:    el.disabled || false,
            });
        });
    });
    return results;
}"""

_JS_POST_CLICK_STATE = """() => {
    const dialogs = [];
    document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        dialogs.push({
            tag:      el.tagName,
            role:     el.getAttribute('role') || '',
            ariaLabel:el.getAttribute('aria-label') || '',
            text:     (el.innerText || '').trim().slice(0, 300),
            visible:  r.width > 0 && r.height > 0,
            bbox:     {x: r.x, y: r.y, w: r.width, h: r.height},
            outerHTML:el.outerHTML.slice(0, 800),
        });
    });
    const composeFields = [];
    const compSels = [
        'textarea', '[contenteditable="true"]', '[role="textbox"]',
        '[data-lexical-editor]', '[aria-label*="message" i]',
    ];
    compSels.forEach(sel => {
        try {
            document.querySelectorAll(sel).forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return;
                composeFields.push({
                    sel:  sel,
                    tag:  el.tagName,
                    role: el.getAttribute('role') || '',
                    ce:   el.getAttribute('contenteditable') || '',
                    aria: el.getAttribute('aria-label') || '',
                    bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
                });
            });
        } catch(e) {}
    });
    return {
        url:           window.location.href,
        title:         document.title,
        dialogs:       dialogs,
        composeFields: composeFields,
    };
}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def audit_before_click(
    page: Any,
    slug: str = "",
    out_dir: Path | None = None,
) -> tuple[Path, list[dict]]:
    """Capture all visible candidate buttons and take a pre-click screenshot.

    Returns (output_dir, candidates_list).
    Call this immediately before clicking the Message button.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    label = f"{ts}_{slug}" if slug else ts
    out = (out_dir or _OUT_DIR) / label
    out.mkdir(parents=True, exist_ok=True)

    # Screenshot before click
    await _screenshot(page, out / "before_click.png", full_page=False)

    # Collect all visible action-relevant candidates
    candidates: list[dict] = []
    try:
        candidates = await page.evaluate(_JS_ALL_CANDIDATES) or []
    except Exception as exc:
        logger.warning("click_audit candidates_failed: %s", exc)

    # Log every candidate
    logger.info(
        "click_audit CANDIDATES_BEFORE_CLICK count=%d slug=%s",
        len(candidates), slug,
    )
    for i, c in enumerate(candidates):
        logger.info(
            "click_audit   candidate[%d] tag=%s text=%r ariaLabel=%r "
            "role=%r href=%r class=%r dataTestid=%r container=%s "
            "bbox=%s disabled=%s",
            i, c.get("tag"), c.get("text"), c.get("ariaLabel"),
            c.get("role"), c.get("href"), c.get("class"),
            c.get("dataTestid"), c.get("container"),
            c.get("bbox"), c.get("disabled"),
        )

    # Save candidates JSON
    try:
        (out / "candidates_before_click.json").write_text(
            json.dumps(candidates, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("click_audit candidates_json_failed: %s", exc)

    return out, candidates


async def audit_chosen_element(
    element: Any,
    out_dir: Path,
    slug: str = "",
) -> dict:
    """Log the exact element that was chosen for clicking.

    Call this with the locator that find_message_action() returned,
    immediately before human_click().
    """
    detail: dict = {}
    try:
        detail = await element.evaluate(_JS_ELEMENT_DETAIL) or {}
    except Exception as exc:
        logger.warning("click_audit chosen_element_eval_failed: %s", exc)
        return detail

    logger.warning(
        "click_audit CHOSEN_ELEMENT tag=%s text=%r ariaLabel=%r "
        "role=%r href=%r class=%r dataTestid=%r "
        "container=%s actionType=%s bbox=%s disabled=%s "
        "outerHTML=%r",
        detail.get("tag"), detail.get("text"), detail.get("ariaLabel"),
        detail.get("role"), detail.get("href"), detail.get("class"),
        detail.get("dataTestid"), detail.get("container"),
        detail.get("actionType"), detail.get("bbox"),
        detail.get("disabled"), (detail.get("outerHTML") or "")[:300],
    )

    # Warn if the chosen element is NOT a plain "Message" button
    action_type = detail.get("actionType", "other")
    if action_type != "Message":
        logger.error(
            "click_audit WRONG_ELEMENT_SELECTED actionType=%s "
            "text=%r ariaLabel=%r — expected 'Message', got '%s'. "
            "This is likely the root cause of the Premium dialog.",
            action_type, detail.get("text"), detail.get("ariaLabel"), action_type,
        )

    try:
        (out_dir / "chosen_element.json").write_text(
            json.dumps(detail, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("click_audit chosen_element_json_failed: %s", exc)

    return detail


async def audit_after_click(
    page: Any,
    out_dir: Path,
    chosen_detail: dict,
    slug: str = "",
) -> dict:
    """Capture post-click state and produce the final root-cause report.

    Call this after human_click() and after the surface detector has run.
    """
    # Screenshot after click
    await _screenshot(page, out_dir / "after_click.png", full_page=False)

    # Post-click DOM state
    post_state: dict = {}
    try:
        post_state = await page.evaluate(_JS_POST_CLICK_STATE) or {}
    except Exception as exc:
        logger.warning("click_audit post_click_state_failed: %s", exc)

    dialogs = post_state.get("dialogs", [])
    compose_fields = post_state.get("composeFields", [])
    url_after = post_state.get("url", "")
    title_after = post_state.get("title", "")

    # Log post-click state
    logger.warning(
        "click_audit POST_CLICK url=%s title=%r dialogs=%d compose_fields=%d",
        url_after, title_after, len(dialogs), len(compose_fields),
    )
    for d in dialogs:
        if d.get("visible"):
            logger.warning(
                "click_audit   DIALOG_VISIBLE role=%r ariaLabel=%r text=%r bbox=%s",
                d.get("role"), d.get("ariaLabel"),
                (d.get("text") or "")[:200], d.get("bbox"),
            )
    for f in compose_fields:
        logger.info(
            "click_audit   COMPOSE_FIELD sel=%r tag=%s role=%r ce=%r aria=%r bbox=%s",
            f.get("sel"), f.get("tag"), f.get("role"),
            f.get("ce"), f.get("aria"), f.get("bbox"),
        )

    # ── Root cause analysis ───────────────────────────────────────────────────
    report = _build_root_cause_report(chosen_detail, post_state, url_after)

    logger.warning(
        "click_audit ROOT_CAUSE_REPORT verdict=%r explanation=%r",
        report["verdict"], report["explanation"],
    )

    # Save full report
    full_report = {
        "slug": slug,
        "chosen_element": chosen_detail,
        "post_click_state": post_state,
        "root_cause": report,
    }
    try:
        (out_dir / "click_audit_report.json").write_text(
            json.dumps(full_report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("click_audit report saved path=%s", out_dir / "click_audit_report.json")
    except Exception as exc:
        logger.warning("click_audit report_write_failed: %s", exc)

    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_root_cause_report(
    chosen: dict,
    post_state: dict,
    url_after: str,
) -> dict:
    """Produce a structured root-cause verdict without changing any logic."""
    dialogs = post_state.get("dialogs", [])
    compose_fields = post_state.get("composeFields", [])
    action_type = chosen.get("actionType", "other")
    container = chosen.get("container", "other")
    text = chosen.get("text", "")
    aria = chosen.get("ariaLabel", "")
    href = chosen.get("href", "")

    visible_dialogs = [d for d in dialogs if d.get("visible")]
    premium_dialog = any(
        re.search(r"premium|upgrade|inmail|trial|unlock", (d.get("text") or ""), re.I)
        for d in visible_dialogs
    )
    compose_open = bool(compose_fields)
    navigated_to_messaging = "/messaging/" in url_after

    # Determine verdict
    if action_type == "Message" and premium_dialog:
        verdict = "CORRECT_BUTTON_BUT_PREMIUM_GATED"
        explanation = (
            f"The correct 'Message' button was clicked (container={container}), "
            f"but LinkedIn responded with a Premium upsell dialog. "
            f"This profile requires LinkedIn Premium / InMail to message. "
            f"The button label is 'Message' but the underlying action is InMail-gated."
        )
    elif action_type in ("InMail", "Premium"):
        verdict = "WRONG_BUTTON_CLICKED"
        explanation = (
            f"The element clicked has actionType='{action_type}' "
            f"(text={text!r}, ariaLabel={aria!r}, container={container}). "
            f"find_message_action() selected an InMail/Premium button instead of "
            f"a plain Message button. The selector matched the wrong element."
        )
    elif action_type == "Message_partial" and premium_dialog:
        verdict = "PARTIAL_MATCH_PREMIUM_GATED"
        explanation = (
            f"The clicked element partially matches 'message' (text={text!r}, "
            f"ariaLabel={aria!r}) but is not a plain Message button. "
            f"LinkedIn showed a Premium dialog — the element is likely an InMail CTA."
        )
    elif action_type == "Connect":
        verdict = "CONNECT_BUTTON_CLICKED_INSTEAD_OF_MESSAGE"
        explanation = (
            f"A Connect button was clicked instead of Message "
            f"(text={text!r}, ariaLabel={aria!r}, container={container}). "
            f"The toolbar selector returned the wrong element."
        )
    elif compose_open or navigated_to_messaging:
        verdict = "SUCCESS_COMPOSE_OPENED"
        explanation = (
            f"Message button clicked successfully. "
            f"compose_fields={len(compose_fields)}, url={url_after}"
        )
    elif visible_dialogs and not premium_dialog:
        verdict = "UNKNOWN_DIALOG_APPEARED"
        dialog_text = (visible_dialogs[0].get("text") or "")[:200]
        explanation = (
            f"An unrecognised dialog appeared after clicking "
            f"(text={dialog_text!r}). Not a Premium dialog."
        )
    elif not visible_dialogs and not compose_open:
        verdict = "NO_REACTION"
        explanation = (
            f"Click produced no visible dialog and no compose field. "
            f"Possible causes: element was not interactive, click missed, "
            f"or LinkedIn navigation is pending."
        )
    else:
        verdict = "PREMIUM_DIALOG_APPEARED"
        explanation = (
            f"Premium dialog appeared. Clicked element: "
            f"actionType={action_type}, text={text!r}, ariaLabel={aria!r}, "
            f"href={href!r}, container={container}."
        )

    return {
        "verdict": verdict,
        "explanation": explanation,
        "action_type_of_clicked_element": action_type,
        "container_of_clicked_element": container,
        "premium_dialog_visible": premium_dialog,
        "compose_field_visible": compose_open,
        "navigated_to_messaging": navigated_to_messaging,
        "url_after_click": url_after,
        "visible_dialog_count": len(visible_dialogs),
        "visible_dialog_texts": [(d.get("text") or "")[:200] for d in visible_dialogs],
    }


async def _screenshot(page: Any, path: Path, *, full_page: bool = False) -> None:
    try:
        await page.screenshot(path=str(path), full_page=full_page)
        logger.info("click_audit screenshot saved path=%s", path)
    except Exception as exc:
        logger.warning("click_audit screenshot_failed path=%s error=%s", path, exc)
