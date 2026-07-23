"""send_observer.py — Pure observation of LinkedIn's post-Send behaviour.

Called IMMEDIATELY after the Send button click succeeds.
Observes for 10 seconds at 250 ms resolution.

Pure observation only:
  - No clicks, no typing, no dismissals, no retries.
  - No changes to delivery logic, verification logic, or selectors.
  - Does not affect DeliveryResult in any way.

The goal is to discover the REAL success signal LinkedIn exposes after Send,
so that verify_sent() can be rewritten with evidence rather than guesses.

Output: debug_logs/send_observer/<YYYYMMDDTHHMMSS>_<slug>/
  timeline.json
  mutations.json
  conversation.json
  compose.json
  send_button.json
  screenshots/
  summary.json

Entry point:
    report = await observe_after_send(page, compose_locator, send_locator, message_text, slug)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUT_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "send_observer"

# ---------------------------------------------------------------------------
# JS payloads — all read-only
# ---------------------------------------------------------------------------

_JS_COMPOSE_STATE = """(sel) => {
    const el = sel ? document.querySelector(sel) : null;
    if (!el) {
        // Try common compose selectors as fallback
        const fallbacks = [
            '[data-lexical-editor][contenteditable]',
            '[role="textbox"][contenteditable]',
            'div[contenteditable="true"]',
            'textarea',
        ];
        for (const s of fallbacks) {
            const found = document.querySelector(s);
            if (found) {
                const r = found.getBoundingClientRect();
                return {
                    found: true,
                    selector: s,
                    visible: r.width > 0 && r.height > 0,
                    text: (found.innerText || found.value || '').trim().slice(0, 200),
                    outerHTML: found.outerHTML.slice(0, 600),
                    bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
                };
            }
        }
        return {found: false, selector: '', visible: false, text: '', outerHTML: '', bbox: null};
    }
    const r = el.getBoundingClientRect();
    return {
        found: true,
        selector: sel,
        visible: r.width > 0 && r.height > 0,
        text: (el.innerText || el.value || '').trim().slice(0, 200),
        outerHTML: el.outerHTML.slice(0, 600),
        bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
    };
}"""

_JS_SEND_BUTTON_STATE = """(sel) => {
    const el = sel ? document.querySelector(sel) : null;
    if (!el) {
        const fallbacks = [
            'button[aria-label*="Send" i]',
            'button:has-text("Send")',
            'button[type="submit"]',
            '[data-control-name*="send" i]',
        ];
        for (const s of fallbacks) {
            try {
                const found = document.querySelector(s);
                if (found) {
                    const r = found.getBoundingClientRect();
                    return {
                        found: true, selector: s,
                        visible: r.width > 0 && r.height > 0,
                        disabled: found.disabled || false,
                        ariaLabel: found.getAttribute('aria-label') || '',
                        text: (found.innerText || '').trim().slice(0, 80),
                        outerHTML: found.outerHTML.slice(0, 400),
                    };
                }
            } catch(e) {}
        }
        return {found: false, selector: '', visible: false, disabled: null, ariaLabel: '', text: '', outerHTML: ''};
    }
    const r = el.getBoundingClientRect();
    return {
        found: true, selector: sel,
        visible: r.width > 0 && r.height > 0,
        disabled: el.disabled || false,
        ariaLabel: el.getAttribute('aria-label') || '',
        text: (el.innerText || '').trim().slice(0, 80),
        outerHTML: el.outerHTML.slice(0, 400),
    };
}"""

_JS_CONVERSATION_STATE = """() => {
    // Outgoing bubbles — try multiple patterns LinkedIn uses
    const outgoingSelectors = [
        '[class*="sent"]',
        '[class*="outgoing"]',
        '[data-msg-sent]',
        '[aria-label*="sent" i]',
        '[aria-label*="delivered" i]',
        '.msg-s-message-list__event--left',
        '.msg-s-message-list__event--right',
        '[class*="message-list__event"]',
        '[class*="msg-s-event"]',
    ];
    const incomingSelectors = [
        '[class*="received"]',
        '[class*="incoming"]',
        '[aria-label*="received" i]',
    ];

    let outgoingCount = 0;
    let lastOutgoingText = '';
    let lastOutgoingHTML = '';
    for (const sel of outgoingSelectors) {
        try {
            const nodes = document.querySelectorAll(sel);
            if (nodes.length > 0) {
                outgoingCount = Math.max(outgoingCount, nodes.length);
                const last = nodes[nodes.length - 1];
                const t = (last.innerText || '').trim();
                if (t) { lastOutgoingText = t.slice(0, 200); lastOutgoingHTML = last.outerHTML.slice(0, 400); }
            }
        } catch(e) {}
    }

    let incomingCount = 0;
    for (const sel of incomingSelectors) {
        try { incomingCount = Math.max(incomingCount, document.querySelectorAll(sel).length); } catch(e) {}
    }

    // Toast / snackbar
    const toastSelectors = [
        '[class*="toast"]', '[class*="snackbar"]', '[class*="notification"]',
        '[role="alert"]', '[role="status"]', '[aria-live]',
        '[class*="artdeco-toast"]', '[class*="msg-overlay-bubble"]',
    ];
    const toasts = [];
    for (const sel of toastSelectors) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    const t = (el.innerText || '').trim();
                    if (t) toasts.push({sel, text: t.slice(0, 200), bbox: {x:r.x,y:r.y,w:r.width,h:r.height}});
                }
            });
        } catch(e) {}
    }

    // Conversation container HTML snippet
    let convHTML = '';
    const convSelectors = [
        '.msg-s-message-list', '[class*="message-list"]',
        '.msg-overlay-conversation-bubble', '[class*="conversation"]',
        '[class*="msg-convo"]',
    ];
    for (const sel of convSelectors) {
        try {
            const el = document.querySelector(sel);
            if (el) { convHTML = el.outerHTML.slice(0, 2000); break; }
        } catch(e) {}
    }

    return {
        outgoingCount,
        lastOutgoingText,
        lastOutgoingHTML,
        incomingCount,
        toasts,
        convHTML,
        url: window.location.href,
        title: document.title,
    };
}"""

_JS_ACTIVE_ELEMENT = """() => {
    const el = document.activeElement;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
        tag: el.tagName,
        role: el.getAttribute('role') || '',
        aria: el.getAttribute('aria-label') || '',
        ce: el.getAttribute('contenteditable') || '',
        text: (el.innerText || '').trim().slice(0, 80),
        bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
    };
}"""

_JS_MUTATION_START = """() => {
    window.__sendObsMutations = [];
    window.__sendObsObserver = new MutationObserver(records => {
        records.forEach(r => {
            const added = Array.from(r.addedNodes).map(n => ({
                type: n.nodeType,
                tag: n.tagName || '#text',
                text: (n.textContent || '').trim().slice(0, 100),
                html: n.outerHTML ? n.outerHTML.slice(0, 300) : '',
            }));
            const removed = Array.from(r.removedNodes).map(n => ({
                type: n.nodeType,
                tag: n.tagName || '#text',
                text: (n.textContent || '').trim().slice(0, 100),
            }));
            window.__sendObsMutations.push({
                t: Date.now(),
                type: r.type,
                target: r.target.tagName + (r.target.id ? '#'+r.target.id : '')
                        + (r.target.className && typeof r.target.className === 'string'
                           ? '.'+r.target.className.trim().split(/[ \t]+/)[0] : ''),
                attr: r.attributeName || '',
                added,
                removed,
            });
        });
    });
    window.__sendObsObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
        attributeOldValue: false,
        characterDataOldValue: false,
    });
    window.__sendObsT0 = Date.now();
    return 'started';
}"""

_JS_MUTATION_STOP = """() => {
    if (window.__sendObsObserver) window.__sendObsObserver.disconnect();
    const t0 = window.__sendObsT0 || Date.now();
    return (window.__sendObsMutations || []).map(m => ({
        ...m,
        elapsed_ms: m.t - t0,
    }));
}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save(path: Path, data: Any) -> None:
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("send_observer save_failed path=%s error=%s", path, exc)


async def _screenshot(page: Any, path: Path) -> None:
    try:
        await page.screenshot(path=str(path), full_page=False)
        logger.info("send_observer screenshot saved path=%s", path)
    except Exception as exc:
        logger.warning("send_observer screenshot_failed path=%s error=%s", path, exc)


def _extract_selector(locator: Any) -> str:
    """Best-effort: extract the selector string from a Playwright locator."""
    try:
        # Playwright locators expose _selector on some versions
        return str(getattr(locator, "_selector", "") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def observe_after_send(
    page: Any,
    compose_locator: Any,
    send_locator: Any,
    message_text: str,
    slug: str = "",
) -> dict:
    """Observe the page for 10 seconds after Send is clicked.

    Call this immediately after human_click(send_button) returns.

    Args:
        page:            Playwright page object.
        compose_locator: The compose field locator used during delivery.
        send_locator:    The Send button locator used during delivery.
        message_text:    The message that was sent (for text-match checks).
        slug:            Short identifier for the output directory.

    Returns:
        The full observation report dict (also written to summary.json).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    label = f"{ts}_{slug}" if slug else ts
    out = _OUT_DIR / label
    screenshots_dir = out / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    compose_sel = _extract_selector(compose_locator)
    send_sel = _extract_selector(send_locator)

    # ── Start MutationObserver ────────────────────────────────────────────────
    try:
        await page.evaluate(_JS_MUTATION_START)
    except Exception as exc:
        logger.warning("send_observer mutation_start_failed: %s", exc)

    # ── Capture initial state of compose and send button ─────────────────────
    compose_initial: dict = {}
    send_initial: dict = {}
    try:
        compose_initial = await page.evaluate(_JS_COMPOSE_STATE, compose_sel) or {}
    except Exception as exc:
        compose_initial = {"error": str(exc)}
    try:
        send_initial = await page.evaluate(_JS_SEND_BUTTON_STATE, send_sel) or {}
    except Exception as exc:
        send_initial = {"error": str(exc)}

    _save(out / "compose.json", compose_initial)
    _save(out / "send_button.json", send_initial)

    # ── Poll timeline ─────────────────────────────────────────────────────────
    observation_s = 10.0
    poll_interval_s = 0.25
    screenshot_at = {0.5, 1.0, 2.0, 5.0, 10.0}

    timeline: list[dict] = []
    start = time.monotonic()
    prev_elapsed = 0.0

    logger.info("send_observer starting 10s observation slug=%s", slug)

    while True:
        elapsed = time.monotonic() - start
        if elapsed > observation_s:
            break

        tick: dict = {"elapsed_s": round(elapsed, 3)}

        # URL + title
        try:
            tick["url"] = str(getattr(page, "url", ""))
        except Exception:
            tick["url"] = ""
        try:
            tick["title"] = str(await page.title())
        except Exception:
            tick["title"] = ""

        # Active element
        try:
            tick["active_element"] = await page.evaluate(_JS_ACTIVE_ELEMENT)
        except Exception:
            tick["active_element"] = None

        # Compose state
        try:
            tick["compose"] = await page.evaluate(_JS_COMPOSE_STATE, compose_sel) or {}
        except Exception as exc:
            tick["compose"] = {"error": str(exc)}

        # Send button state
        try:
            tick["send_button"] = await page.evaluate(_JS_SEND_BUTTON_STATE, send_sel) or {}
        except Exception as exc:
            tick["send_button"] = {"error": str(exc)}

        # Conversation state (bubbles, toasts, conv HTML)
        try:
            tick["conversation"] = await page.evaluate(_JS_CONVERSATION_STATE) or {}
        except Exception as exc:
            tick["conversation"] = {"error": str(exc)}

        timeline.append(tick)
        _log_tick(tick, slug, message_text)

        # Screenshots at designated times
        for t in list(screenshot_at):
            if prev_elapsed < t <= elapsed:
                name = f"t{t:04.1f}s.png".replace(".", "_")
                await _screenshot(page, screenshots_dir / name)
                screenshot_at.discard(t)

        prev_elapsed = elapsed
        remaining = poll_interval_s - (time.monotonic() - start - elapsed)
        if remaining > 0:
            await asyncio.sleep(remaining)

    # ── Stop MutationObserver ─────────────────────────────────────────────────
    mutations: list[dict] = []
    try:
        mutations = await page.evaluate(_JS_MUTATION_STOP) or []
    except Exception as exc:
        logger.warning("send_observer mutation_stop_failed: %s", exc)

    # ── Final conversation snapshot ───────────────────────────────────────────
    conversation_final: dict = {}
    try:
        conversation_final = await page.evaluate(_JS_CONVERSATION_STATE) or {}
    except Exception as exc:
        conversation_final = {"error": str(exc)}

    # ── Save artefacts ────────────────────────────────────────────────────────
    _save(out / "timeline.json", timeline)
    _save(out / "mutations.json", mutations)
    _save(out / "conversation.json", conversation_final)

    # ── Build summary ─────────────────────────────────────────────────────────
    summary = _build_summary(timeline, mutations, compose_initial, send_initial, message_text)
    _save(out / "summary.json", summary)

    # ── Log summary ───────────────────────────────────────────────────────────
    logger.warning(
        "send_observer SUMMARY slug=%s "
        "compose_disappeared=%s compose_cleared=%s "
        "send_disabled=%s send_disappeared=%s "
        "outgoing_bubble_appeared=%s conv_dom_changed=%s "
        "url_changed=%s toast_appeared=%s "
        "first_signal=%r at=%.3fs",
        slug,
        summary["compose_disappeared"],
        summary["compose_cleared"],
        summary["send_disabled"],
        summary["send_disappeared"],
        summary["outgoing_bubble_appeared"],
        summary["conv_dom_changed"],
        summary["url_changed"],
        summary["toast_appeared"],
        summary["first_signal"],
        summary["first_signal_at_s"] if summary["first_signal_at_s"] is not None else -1,
    )

    return summary


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(
    timeline: list[dict],
    mutations: list[dict],
    compose_initial: dict,
    send_initial: dict,
    message_text: str,
) -> dict:
    compose_disappeared = False
    compose_cleared = False
    send_disabled = False
    send_disappeared = False
    outgoing_bubble_appeared = False
    conv_dom_changed = False
    url_changed = False
    toast_appeared = False

    first_signal: str | None = None
    first_signal_at: float | None = None

    initial_url = timeline[0]["url"] if timeline else ""
    initial_outgoing = (
        (timeline[0].get("conversation") or {}).get("outgoingCount", 0)
        if timeline else 0
    )

    def _record(signal: str, t: float) -> None:
        nonlocal first_signal, first_signal_at
        if first_signal is None:
            first_signal = signal
            first_signal_at = t

    for tick in timeline:
        t = tick["elapsed_s"]
        compose = tick.get("compose") or {}
        send = tick.get("send_button") or {}
        conv = tick.get("conversation") or {}

        # Compose disappeared
        if not compose_disappeared and not compose.get("visible", True) and compose.get("found") is False:
            compose_disappeared = True
            _record("compose_disappeared", t)

        # Compose cleared (was visible, now empty)
        if not compose_cleared and compose.get("visible") and compose.get("text") == "":
            if compose_initial.get("text"):  # only meaningful if it had text before
                compose_cleared = True
                _record("compose_cleared", t)

        # Send disabled
        if not send_disabled and send.get("disabled") is True:
            send_disabled = True
            _record("send_disabled", t)

        # Send disappeared
        if not send_disappeared and send.get("found") is False:
            send_disappeared = True
            _record("send_disappeared", t)

        # Outgoing bubble appeared
        current_outgoing = conv.get("outgoingCount", 0)
        if not outgoing_bubble_appeared and current_outgoing > initial_outgoing:
            outgoing_bubble_appeared = True
            _record("outgoing_bubble_appeared", t)

        # Message text visible in conversation
        last_text = conv.get("lastOutgoingText", "")
        if not outgoing_bubble_appeared and message_text and message_text[:20] in last_text:
            outgoing_bubble_appeared = True
            _record("message_text_in_conversation", t)

        # URL changed
        if not url_changed and tick.get("url") and tick["url"] != initial_url:
            url_changed = True
            _record("url_changed", t)

        # Toast appeared
        if not toast_appeared and conv.get("toasts"):
            toast_appeared = True
            _record("toast_appeared", t)

    # Conv DOM changed — check mutations for message-list related nodes
    for m in mutations:
        target = m.get("target", "")
        added = m.get("added", [])
        if any(
            "msg" in (a.get("tag") or "").lower()
            or "message" in (a.get("html") or "").lower()
            or "sent" in (a.get("html") or "").lower()
            for a in added
        ):
            conv_dom_changed = True
            elapsed_ms = m.get("elapsed_ms", 0)
            _record("conv_dom_mutation", elapsed_ms / 1000.0)
            break

    # Collect all signals seen across the timeline
    signals_seen: list[dict] = []
    for tick in timeline:
        t = tick["elapsed_s"]
        compose = tick.get("compose") or {}
        send = tick.get("send_button") or {}
        conv = tick.get("conversation") or {}
        if not compose.get("visible") and compose.get("found") is False:
            signals_seen.append({"signal": "compose_not_visible", "t": t})
        if compose.get("text") == "" and compose_initial.get("text"):
            signals_seen.append({"signal": "compose_empty", "t": t})
        if send.get("disabled"):
            signals_seen.append({"signal": "send_disabled", "t": t})
        if not send.get("found"):
            signals_seen.append({"signal": "send_not_found", "t": t})
        if conv.get("outgoingCount", 0) > initial_outgoing:
            signals_seen.append({"signal": "outgoing_bubble", "t": t, "count": conv.get("outgoingCount")})
        if conv.get("toasts"):
            signals_seen.append({"signal": "toast", "t": t, "texts": [x.get("text") for x in conv["toasts"]]})

    # Deduplicate consecutive identical signals
    deduped: list[dict] = []
    for s in signals_seen:
        if not deduped or deduped[-1]["signal"] != s["signal"]:
            deduped.append(s)

    return {
        "compose_disappeared":       compose_disappeared,
        "compose_cleared":           compose_cleared,
        "send_disabled":             send_disabled,
        "send_disappeared":          send_disappeared,
        "outgoing_bubble_appeared":  outgoing_bubble_appeared,
        "conv_dom_changed":          conv_dom_changed,
        "url_changed":               url_changed,
        "toast_appeared":            toast_appeared,
        "first_signal":              first_signal,
        "first_signal_at_s":         first_signal_at,
        "all_signals_timeline":      deduped,
        "total_mutations":           len(mutations),
        "initial_outgoing_count":    initial_outgoing,
        "compose_initial_text":      (compose_initial.get("text") or "")[:100],
        "message_text_prefix":       message_text[:40],
    }


# ---------------------------------------------------------------------------
# Tick logger
# ---------------------------------------------------------------------------

def _log_tick(tick: dict, slug: str, message_text: str) -> None:
    t = tick["elapsed_s"]
    compose = tick.get("compose") or {}
    send = tick.get("send_button") or {}
    conv = tick.get("conversation") or {}

    logger.info(
        "send_obs t=%.3fs url=%s "
        "compose_visible=%s compose_text_len=%d "
        "send_found=%s send_disabled=%s "
        "outgoing=%d toasts=%d slug=%s",
        t,
        (tick.get("url") or "")[-60:],
        compose.get("visible"),
        len(compose.get("text") or ""),
        send.get("found"),
        send.get("disabled"),
        conv.get("outgoingCount", 0),
        len(conv.get("toasts") or []),
        slug,
    )

    # Log toasts immediately — they are the most informative signal
    for toast in (conv.get("toasts") or []):
        logger.warning(
            "send_obs TOAST t=%.3fs text=%r slug=%s",
            t, toast.get("text"), slug,
        )

    # Log when outgoing bubble appears
    if conv.get("lastOutgoingText") and message_text[:10] in conv.get("lastOutgoingText", ""):
        logger.warning(
            "send_obs OUTGOING_BUBBLE_WITH_TEXT t=%.3fs text=%r slug=%s",
            t, conv.get("lastOutgoingText", "")[:80], slug,
        )
