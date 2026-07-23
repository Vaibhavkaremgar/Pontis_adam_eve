"""post_click_observer.py — Pure DOM observation after a Message button click.

PURE OBSERVATION ONLY.
- Does NOT classify anything.
- Does NOT attempt to find compose.
- Does NOT attempt to find Premium.
- Does NOT make any decision.
- Does NOT click, type, dismiss, or interact with the page in any way.
- Does NOT modify any worker, detector, or classifier.

Single entry point:
    report = await observe(page, slug="profile-slug")

Saves all artifacts to:
    debug_logs/post_click_obs/<YYYYMMDDTHHMMSS>_<slug>/

Returns a dict that is also written as observation_report.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUT_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "post_click_obs"

# ---------------------------------------------------------------------------
# JavaScript payloads — all read-only, no side-effects
# ---------------------------------------------------------------------------

_JS_ACTIVE_ELEMENT = """() => {
    const el = document.activeElement;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
        tag:             el.tagName,
        role:            el.getAttribute('role') || '',
        ariaLabel:       el.getAttribute('aria-label') || '',
        placeholder:     el.getAttribute('placeholder') || '',
        contenteditable: el.getAttribute('contenteditable') || '',
        outerHTML:       el.outerHTML.slice(0, 500),
        bbox:            {x: r.x, y: r.y, w: r.width, h: r.height},
        inShadow:        el.getRootNode() !== document,
    };
}"""

_JS_EDITABLE_ELEMENTS = """() => {
    const selectors = [
        'textarea',
        'input',
        'input[type="text"]',
        'input:not([type])',
        'div[contenteditable]',
        '*[contenteditable]',
        '*[role="textbox"]',
        '*[aria-multiline]',
        '*[data-lexical-editor]',
        '*[data-testid]',
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
            results.push({
                matchedSelector: sel,
                tag:             el.tagName,
                role:            el.getAttribute('role') || '',
                ariaLabel:       el.getAttribute('aria-label') || '',
                placeholder:     el.getAttribute('placeholder') || '',
                contenteditable: el.getAttribute('contenteditable') || '',
                readonly:        el.getAttribute('readonly') || '',
                disabled:        el.disabled || false,
                type:            el.getAttribute('type') || '',
                dataTestid:      el.getAttribute('data-testid') || '',
                dataLexical:     el.getAttribute('data-lexical-editor') || '',
                visible:         r.width > 0 && r.height > 0
                                 && cs.visibility !== 'hidden'
                                 && cs.display !== 'none',
                bbox:            {x: r.x, y: r.y, w: r.width, h: r.height},
                outerHTML:       el.outerHTML.slice(0, 500),
                inShadow:        el.getRootNode() !== document,
            });
        });
    });
    return results;
}"""

_JS_DIALOGS = """() => {
    const results = [];
    document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog').forEach(el => {
        const r = el.getBoundingClientRect();
        const cs = window.getComputedStyle(el);
        results.push({
            tag:      el.tagName,
            id:       el.id,
            role:     el.getAttribute('role') || '',
            visible:  r.width > 0 && r.height > 0,
            bbox:     {x: r.x, y: r.y, w: r.width, h: r.height},
            zIndex:   cs.zIndex,
            text:     (el.innerText || '').trim().slice(0, 400),
            outerHTML:el.outerHTML.slice(0, 600),
        });
    });
    return results;
}"""

_JS_IFRAMES = """() => {
    const results = [];
    document.querySelectorAll('iframe').forEach(el => {
        const r = el.getBoundingClientRect();
        let sameOrigin = false;
        let innerEditables = 0;
        try {
            if (el.contentDocument) {
                sameOrigin = true;
                innerEditables = el.contentDocument
                    .querySelectorAll('textarea,[contenteditable],[role="textbox"]').length;
            }
        } catch(e) {}
        results.push({
            src:           el.src,
            title:         el.title,
            name:          el.name,
            visible:       r.width > 0 && r.height > 0,
            bbox:          {x: r.x, y: r.y, w: r.width, h: r.height},
            sameOrigin:    sameOrigin,
            innerEditables:innerEditables,
        });
    });
    return results;
}"""

_JS_SHADOW_EDITABLES = """() => {
    const results = [];
    function walk(root) {
        const nodes = root.querySelectorAll('*');
        nodes.forEach(el => {
            if (el.shadowRoot) {
                const sr = el.shadowRoot;
                sr.querySelectorAll(
                    'textarea,[contenteditable],[role="textbox"],[data-lexical-editor]'
                ).forEach(inner => {
                    const r = inner.getBoundingClientRect();
                    results.push({
                        shadowHost:      el.tagName + (el.id ? '#'+el.id : ''),
                        tag:             inner.tagName,
                        role:            inner.getAttribute('role') || '',
                        contenteditable: inner.getAttribute('contenteditable') || '',
                        visible:         r.width > 0 && r.height > 0,
                        bbox:            {x: r.x, y: r.y, w: r.width, h: r.height},
                        outerHTML:       inner.outerHTML.slice(0, 400),
                    });
                });
                walk(sr);
            }
        });
    }
    walk(document);
    return results;
}"""

_JS_NETWORK_ENTRIES = """() => {
    const keywords = [
        'messaging','compose','conversation','thread','premium','overlay'
    ];
    return performance.getEntriesByType('resource')
        .filter(e => keywords.some(k => e.name.toLowerCase().includes(k)))
        .map(e => ({
            url:          e.name,
            type:         e.initiatorType,
            duration_ms:  Math.round(e.duration),
        }))
        .slice(0, 80);
}"""

_JS_PAGE_STATE = """() => ({
    url:        window.location.href,
    title:      document.title,
    readyState: document.readyState,
})"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def observe(page: Any, slug: str = "") -> dict:
    """Observe the page for 12 seconds after a Message click.

    Pure observation — no clicks, no classification, no side-effects.

    Args:
        page:  Playwright page object (already past the click).
        slug:  Short identifier for the output directory (e.g. profile slug).

    Returns:
        The full observation report dict (also written to disk).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    label = f"{ts}_{slug}" if slug else ts
    out = _OUT_DIR / label
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "ts": ts,
        "slug": slug,
        "ticks": [],
        "screenshots": {},
        "html_snapshots": {},
        "console": [],
        "network": [],
        "files": {},
    }

    # ── Attach console listener before the observation window ─────────────────
    console_msgs: list[dict] = []

    def _on_console(msg: Any) -> None:
        try:
            console_msgs.append({
                "type": msg.type,
                "text": msg.text,
                "location": str(getattr(msg, "location", "")),
            })
        except Exception:
            pass

    try:
        page.on("console", _on_console)
    except Exception:
        pass

    # ── Human delay: 700–1200 ms, mouse idle ─────────────────────────────────
    human_delay = random.uniform(0.7, 1.2)
    logger.info("post_click_obs human_delay=%.3fs slug=%s", human_delay, slug)
    await asyncio.sleep(human_delay)

    # ── Screenshot timestamps (seconds from click) ────────────────────────────
    screenshot_times = {0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0}
    html_times = {1.0, 3.0, 8.0, 12.0}

    observation_duration = 12.0
    tick_interval = 0.5
    start = time.monotonic()
    elapsed_prev = 0.0

    logger.info("post_click_obs starting 12s window slug=%s out=%s", slug, out)

    while True:
        elapsed = time.monotonic() - start
        if elapsed > observation_duration:
            break

        # ── Collect one tick ──────────────────────────────────────────────────
        tick: dict = {"elapsed_s": round(elapsed, 2)}

        # Page state
        try:
            tick["page_state"] = await page.evaluate(_JS_PAGE_STATE)
        except Exception as exc:
            tick["page_state"] = {"error": str(exc)}

        # Active element
        try:
            tick["active_element"] = await page.evaluate(_JS_ACTIVE_ELEMENT)
        except Exception as exc:
            tick["active_element"] = {"error": str(exc)}

        # Editable elements
        try:
            tick["editable_elements"] = await page.evaluate(_JS_EDITABLE_ELEMENTS)
        except Exception as exc:
            tick["editable_elements"] = {"error": str(exc)}

        # Dialogs
        try:
            tick["dialogs"] = await page.evaluate(_JS_DIALOGS)
        except Exception as exc:
            tick["dialogs"] = {"error": str(exc)}

        # Iframes
        try:
            tick["iframes"] = await page.evaluate(_JS_IFRAMES)
        except Exception as exc:
            tick["iframes"] = {"error": str(exc)}

        # Shadow DOM editables
        try:
            tick["shadow_editables"] = await page.evaluate(_JS_SHADOW_EDITABLES)
        except Exception as exc:
            tick["shadow_editables"] = {"error": str(exc)}

        report["ticks"].append(tick)

        _log_tick(tick, slug)

        # ── Screenshots at designated times ───────────────────────────────────
        for t in list(screenshot_times):
            if elapsed_prev < t <= elapsed:
                name = f"t{t:04.1f}s.png".replace(".", "_")
                path = out / name
                try:
                    await page.screenshot(path=str(path), full_page=False)
                    report["screenshots"][f"{t}s"] = str(path)
                    logger.info("post_click_obs screenshot t=%.1fs path=%s", t, path)
                except Exception as exc:
                    logger.warning("post_click_obs screenshot t=%.1fs failed: %s", t, exc)
                screenshot_times.discard(t)

        # ── HTML snapshots at designated times ────────────────────────────────
        for t in list(html_times):
            if elapsed_prev < t <= elapsed:
                name = f"dom_t{t:04.1f}s.html".replace(".", "_")
                path = out / name
                try:
                    html = await page.evaluate(
                        "() => document.documentElement.outerHTML"
                    )
                    path.write_text(str(html), encoding="utf-8")
                    report["html_snapshots"][f"{t}s"] = str(path)
                    logger.info("post_click_obs html_snapshot t=%.1fs path=%s", t, path)
                except Exception as exc:
                    logger.warning("post_click_obs html_snapshot t=%.1fs failed: %s", t, exc)
                html_times.discard(t)

        elapsed_prev = elapsed
        remaining = tick_interval - (time.monotonic() - start - elapsed)
        if remaining > 0:
            await asyncio.sleep(remaining)

    # ── Final network snapshot ────────────────────────────────────────────────
    try:
        report["network"] = await page.evaluate(_JS_NETWORK_ENTRIES)
    except Exception as exc:
        report["network"] = {"error": str(exc)}

    # ── Detach console listener ───────────────────────────────────────────────
    try:
        page.remove_listener("console", _on_console)
    except Exception:
        pass
    report["console"] = console_msgs

    # ── Write report ──────────────────────────────────────────────────────────
    report_path = out / "observation_report.json"
    try:
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        report["files"]["observation_report.json"] = str(report_path)
        logger.info("post_click_obs report saved path=%s", report_path)
    except Exception as exc:
        logger.warning("post_click_obs report_write failed: %s", exc)

    # ── Final summary log ─────────────────────────────────────────────────────
    _log_summary(report, slug)

    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_tick(tick: dict, slug: str) -> None:
    elapsed = tick.get("elapsed_s", "?")
    ps = tick.get("page_state", {})
    url = ps.get("url", "?") if isinstance(ps, dict) else "?"

    ae = tick.get("active_element") or {}
    ae_tag = ae.get("tag", "-") if isinstance(ae, dict) else "-"
    ae_ce = ae.get("contenteditable", "") if isinstance(ae, dict) else ""
    ae_role = ae.get("role", "") if isinstance(ae, dict) else ""

    editables = tick.get("editable_elements", [])
    n_editable = len(editables) if isinstance(editables, list) else 0
    n_visible = sum(
        1 for e in (editables if isinstance(editables, list) else [])
        if e.get("visible")
    )

    dialogs = tick.get("dialogs", [])
    n_dialogs = len(dialogs) if isinstance(dialogs, list) else 0
    n_visible_dialogs = sum(
        1 for d in (dialogs if isinstance(dialogs, list) else [])
        if d.get("visible")
    )

    iframes = tick.get("iframes", [])
    n_iframes = len(iframes) if isinstance(iframes, list) else 0

    shadow = tick.get("shadow_editables", [])
    n_shadow = len(shadow) if isinstance(shadow, list) else 0

    logger.info(
        "post_click_obs tick=%.2fs url=%s "
        "active=%s(ce=%r role=%r) "
        "editables=%d visible=%d dialogs=%d visible_dialogs=%d "
        "iframes=%d shadow_editables=%d slug=%s",
        elapsed, url,
        ae_tag, ae_ce, ae_role,
        n_editable, n_visible, n_dialogs, n_visible_dialogs,
        n_iframes, n_shadow, slug,
    )

    # Log each visible editable element individually
    for e in (editables if isinstance(editables, list) else []):
        if e.get("visible"):
            logger.info(
                "post_click_obs   VISIBLE_EDITABLE t=%.2fs "
                "sel=%r tag=%s role=%r ce=%r placeholder=%r "
                "bbox=%s inShadow=%s",
                elapsed,
                e.get("matchedSelector"), e.get("tag"),
                e.get("role"), e.get("contenteditable"),
                e.get("placeholder"), e.get("bbox"),
                e.get("inShadow"),
            )

    # Log each visible dialog
    for d in (dialogs if isinstance(dialogs, list) else []):
        if d.get("visible"):
            logger.info(
                "post_click_obs   VISIBLE_DIALOG t=%.2fs "
                "tag=%s role=%r zIndex=%s text=%r",
                elapsed,
                d.get("tag"), d.get("role"),
                d.get("zIndex"), (d.get("text") or "")[:120],
            )

    # Log shadow editables
    for s in (shadow if isinstance(shadow, list) else []):
        logger.info(
            "post_click_obs   SHADOW_EDITABLE t=%.2fs host=%s tag=%s visible=%s",
            elapsed, s.get("shadowHost"), s.get("tag"), s.get("visible"),
        )


def _log_summary(report: dict, slug: str) -> None:
    ticks = report.get("ticks", [])

    # Collect all unique visible editable elements seen across all ticks
    seen_editables: dict[str, dict] = {}
    first_editable_t: float | None = None
    editable_in_shadow = False
    editable_in_iframe = False

    for tick in ticks:
        t = tick.get("elapsed_s", 0)
        for e in (tick.get("editable_elements") or []):
            if not isinstance(e, dict) or not e.get("visible"):
                continue
            key = e.get("matchedSelector", "") + "|" + e.get("tag", "")
            if key not in seen_editables:
                seen_editables[key] = {**e, "first_seen_t": t}
                if first_editable_t is None:
                    first_editable_t = t
            if e.get("inShadow"):
                editable_in_shadow = True

        for s in (tick.get("shadow_editables") or []):
            if isinstance(s, dict) and s.get("visible"):
                editable_in_shadow = True

        for f in (tick.get("iframes") or []):
            if isinstance(f, dict) and f.get("innerEditables", 0) > 0:
                editable_in_iframe = True

    # URL changes
    urls_seen = []
    for tick in ticks:
        ps = tick.get("page_state") or {}
        u = ps.get("url", "") if isinstance(ps, dict) else ""
        if u and (not urls_seen or urls_seen[-1] != u):
            urls_seen.append(u)

    navigated = len(urls_seen) > 1

    logger.warning(
        "post_click_obs SUMMARY slug=%s "
        "unique_visible_editables=%d "
        "first_editable_at=%.2fs "
        "editable_in_shadow=%s "
        "editable_in_iframe=%s "
        "url_navigated=%s "
        "urls_seen=%s "
        "console_msgs=%d "
        "network_entries=%d",
        slug,
        len(seen_editables),
        first_editable_t if first_editable_t is not None else -1,
        editable_in_shadow,
        editable_in_iframe,
        navigated,
        urls_seen,
        len(report.get("console") or []),
        len(report.get("network") or []),
    )

    for key, e in seen_editables.items():
        logger.warning(
            "post_click_obs EDITABLE_FOUND sel=%r tag=%s role=%r ce=%r "
            "placeholder=%r inShadow=%s first_seen_t=%.2fs",
            e.get("matchedSelector"), e.get("tag"), e.get("role"),
            e.get("contenteditable"), e.get("placeholder"),
            e.get("inShadow"), e.get("first_seen_t", -1),
        )
