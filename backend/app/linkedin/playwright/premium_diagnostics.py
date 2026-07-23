"""premium_diagnostics.py — Read-only evidence collector for NAV_PREMIUM.

Called AFTER NavigationTracker returns NAV_PREMIUM.
Never dismisses popups.  Never clicks.  Never types.  Read-only.

Output directory: debug_logs/premium_debug/
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUT_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "premium_debug"

_PREMIUM_KEYWORDS = [
    "premium", "upgrade", "trial", "business", "sales navigator",
    "recruiter", "inmail", "unlock", "subscribe", "paid", "membership",
    "message anyone", "connect", "try premium", "career", "learning",
    "gold",
]

_JS_PAGE_STATE = """() => ({
    url:        window.location.href,
    title:      document.title,
    readyState: document.readyState,
    innerWidth:  window.innerWidth,
    innerHeight: window.innerHeight,
    userAgent:   navigator.userAgent,
    historyLen:  history.length,
})"""

_JS_DIALOGS = """() => {
    const results = [];
    const sel = '[role="dialog"],[role="alertdialog"],dialog';
    document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect();
        const cs = window.getComputedStyle(el);
        const data = {};
        for (const attr of el.attributes) {
            if (attr.name.startsWith('data-')) data[attr.name] = attr.value;
        }
        results.push({
            tag:             el.tagName,
            id:              el.id,
            className:       el.className,
            role:            el.getAttribute('role') || '',
            ariaLabel:       el.getAttribute('aria-label') || '',
            ariaLabelledby:  el.getAttribute('aria-labelledby') || '',
            ariaDescribedby: el.getAttribute('aria-describedby') || '',
            textContent:     (el.textContent || '').trim().slice(0, 500),
            outerHTML:       el.outerHTML.slice(0, 8000),
            visible:         r.width > 0 && r.height > 0,
            bbox:            {x: r.x, y: r.y, w: r.width, h: r.height},
            zIndex:          cs.zIndex,
            dataAttrs:       data,
        });
    });
    return results;
}"""

_JS_IFRAMES = """() => {
    const results = [];
    document.querySelectorAll('iframe').forEach(el => {
        const r = el.getBoundingClientRect();
        let sameOrigin = false;
        let iframeHTML = '';
        try {
            iframeHTML = el.contentDocument
                ? el.contentDocument.documentElement.outerHTML.slice(0, 4000)
                : '';
            sameOrigin = true;
        } catch(e) {}
        results.push({
            src:        el.src,
            title:      el.title,
            name:       el.name,
            visible:    r.width > 0 && r.height > 0,
            bbox:       {x: r.x, y: r.y, w: r.width, h: r.height},
            sameOrigin: sameOrigin,
            outerHTML:  iframeHTML,
        });
    });
    return results;
}"""

_JS_OVERLAYS = """() => {
    const results = [];
    document.querySelectorAll('*').forEach(el => {
        const cs = window.getComputedStyle(el);
        const pos = cs.position;
        const zi  = parseInt(cs.zIndex, 10) || 0;
        if (pos !== 'fixed' && pos !== 'sticky' && zi <= 100) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        results.push({
            tag:      el.tagName,
            id:       el.id,
            class:    el.className,
            role:     el.getAttribute('role') || '',
            ariaLabel:el.getAttribute('aria-label') || '',
            text:     (el.innerText || '').trim().slice(0, 200),
            outerHTML:el.outerHTML.slice(0, 3000),
            zIndex:   cs.zIndex,
            position: pos,
            bbox:     {x: r.x, y: r.y, w: r.width, h: r.height},
        });
    });
    return results.slice(0, 60);
}"""

_JS_HEADINGS = """() => {
    const results = [];
    const sel = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
    document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        results.push({
            text:      (el.innerText || '').trim(),
            level:     el.tagName,
            parentTag: el.parentElement ? el.parentElement.tagName : '',
        });
    });
    return results;
}"""

_JS_BUTTONS = """() => {
    const results = [];
    const sel = 'button,a[role="button"],input[type="button"],input[type="submit"]';
    document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect();
        const dialog = el.closest('[role="dialog"],[role="alertdialog"],dialog');
        const form   = el.closest('form');
        results.push({
            text:      (el.innerText || el.value || '').trim().slice(0, 120),
            ariaLabel: el.getAttribute('aria-label') || '',
            title:     el.getAttribute('title') || '',
            href:      el.getAttribute('href') || '',
            disabled:  el.disabled || false,
            visible:   r.width > 0 && r.height > 0,
            bbox:      {x: r.x, y: r.y, w: r.width, h: r.height},
            closestDialog: dialog ? (dialog.getAttribute('aria-label') || dialog.id || dialog.tagName) : '',
            closestForm:   form   ? (form.id || form.className || 'form') : '',
        });
    });
    return results.slice(0, 80);
}"""

_JS_LINKS = """() => {
    const results = [];
    document.querySelectorAll('a[href]').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        results.push({
            text:      (el.innerText || '').trim().slice(0, 120),
            href:      el.href,
            ariaLabel: el.getAttribute('aria-label') || '',
            target:    el.target,
        });
    });
    return results.slice(0, 100);
}"""

_JS_PREMIUM_KEYWORDS = """(keywords) => {
    const walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT, null
    );
    const hits = [];
    let node;
    while ((node = walker.nextNode())) {
        const txt = node.textContent || '';
        for (const kw of keywords) {
            if (txt.toLowerCase().includes(kw)) {
                const el = node.parentElement;
                if (!el) continue;
                const path = [];
                let cur = el;
                while (cur && cur !== document.body) {
                    path.unshift(cur.tagName + (cur.id ? '#'+cur.id : ''));
                    cur = cur.parentElement;
                }
                hits.push({
                    keyword:     kw,
                    matchedText: txt.trim().slice(0, 200),
                    domPath:     path.join(' > '),
                    parentHTML:  el.outerHTML.slice(0, 1000),
                });
                break;
            }
        }
    }
    return hits.slice(0, 50);
}"""

_JS_COMPOSE = """() => {
    const results = [];
    const sel = [
        'textarea',
        '[contenteditable="true"]',
        '[role="textbox"]',
        '[data-lexical-editor]',
        '[class*="DraftEditor"]',
        '[class*="message-composer"]',
        '[class*="msg-form"]',
        '[placeholder*="message" i]',
        '[aria-label*="message" i]',
        '[aria-label*="compose" i]',
    ].join(',');
    document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect();
        results.push({
            selector:    el.tagName + (el.id ? '#'+el.id : '') + (el.className ? '.'+el.className.split(' ')[0] : ''),
            outerHTML:   el.outerHTML.slice(0, 1000),
            visible:     r.width > 0 && r.height > 0,
            editable:    !el.disabled && el.getAttribute('contenteditable') !== 'false',
            bbox:        {x: r.x, y: r.y, w: r.width, h: r.height},
        });
    });
    return results;
}"""


async def collect(page: Any, slug: str = "") -> dict:
    """Collect complete page evidence after NAV_PREMIUM.

    Read-only.  Never dismisses popups.  Never clicks.  Never types.
    Saves all artifacts to debug_logs/premium_debug/<ts>_<slug>/
    Returns the premium_report dict.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    label = f"{ts}_{slug}" if slug else ts
    out = _OUT_DIR / label
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {"ts": ts, "slug": slug, "files": {}}

    # ── 1. Screenshots ────────────────────────────────────────────────────────
    for name, full in [("after_click.png", False), ("full_page.png", True)]:
        try:
            path = out / name
            await page.screenshot(path=str(path), full_page=full)
            report["files"][name] = str(path)
        except Exception as exc:
            logger.warning("premium_diag screenshot %s failed: %s", name, exc)

    # ── 2. Page state ─────────────────────────────────────────────────────────
    try:
        state = await page.evaluate(_JS_PAGE_STATE)
    except Exception as exc:
        state = {"error": str(exc)}
        logger.warning("premium_diag page_state failed: %s", exc)
    report["page_state"] = state

    # ── 3. Full HTML ──────────────────────────────────────────────────────────
    try:
        full_html = await page.evaluate("() => document.documentElement.outerHTML")
        html_path = out / "full_page.html"
        html_path.write_text(str(full_html), encoding="utf-8")
        report["files"]["full_page.html"] = str(html_path)
    except Exception as exc:
        logger.warning("premium_diag full_html failed: %s", exc)

    # ── 4. Dialogs ────────────────────────────────────────────────────────────
    try:
        dialogs = await page.evaluate(_JS_DIALOGS)
    except Exception as exc:
        dialogs = []
        logger.warning("premium_diag dialogs failed: %s", exc)
    report["dialogs"] = dialogs

    # Screenshot first visible dialog
    try:
        for d in dialogs:
            if d.get("visible") and d.get("bbox", {}).get("w", 0) > 0:
                b = d["bbox"]
                clip = {"x": b["x"], "y": b["y"], "width": b["w"], "height": b["h"]}
                path = out / "dialog.png"
                await page.screenshot(path=str(path), clip=clip)
                report["files"]["dialog.png"] = str(path)
                break
    except Exception as exc:
        logger.warning("premium_diag dialog_screenshot failed: %s", exc)

    # ── 5. Iframes ────────────────────────────────────────────────────────────
    try:
        iframes = await page.evaluate(_JS_IFRAMES)
    except Exception as exc:
        iframes = []
        logger.warning("premium_diag iframes failed: %s", exc)
    report["iframes"] = iframes

    # ── 6. Overlays ───────────────────────────────────────────────────────────
    try:
        overlays = await page.evaluate(_JS_OVERLAYS)
    except Exception as exc:
        overlays = []
        logger.warning("premium_diag overlays failed: %s", exc)
    report["overlays"] = overlays

    # Screenshot first visible overlay
    try:
        for ov in overlays:
            b = ov.get("bbox", {})
            if b.get("w", 0) > 0 and b.get("h", 0) > 0:
                clip = {"x": b["x"], "y": b["y"], "width": b["w"], "height": b["h"]}
                path = out / "overlay.png"
                await page.screenshot(path=str(path), clip=clip)
                report["files"]["overlay.png"] = str(path)
                break
    except Exception as exc:
        logger.warning("premium_diag overlay_screenshot failed: %s", exc)

    # ── 7. Headings ───────────────────────────────────────────────────────────
    try:
        headings = await page.evaluate(_JS_HEADINGS)
    except Exception as exc:
        headings = []
        logger.warning("premium_diag headings failed: %s", exc)
    report["headings"] = headings

    # ── 8. Buttons ────────────────────────────────────────────────────────────
    try:
        buttons = await page.evaluate(_JS_BUTTONS)
    except Exception as exc:
        buttons = []
        logger.warning("premium_diag buttons failed: %s", exc)
    report["buttons"] = buttons

    # ── 9. Links ──────────────────────────────────────────────────────────────
    try:
        links = await page.evaluate(_JS_LINKS)
    except Exception as exc:
        links = []
        logger.warning("premium_diag links failed: %s", exc)
    report["links"] = links

    # ── 10. Premium keyword scan ──────────────────────────────────────────────
    try:
        premium_hits = await page.evaluate(_JS_PREMIUM_KEYWORDS, _PREMIUM_KEYWORDS)
    except Exception as exc:
        premium_hits = []
        logger.warning("premium_diag keyword_scan failed: %s", exc)
    report["premium_keywords"] = premium_hits

    # ── 11. Compose UI scan ───────────────────────────────────────────────────
    try:
        compose_editors = await page.evaluate(_JS_COMPOSE)
    except Exception as exc:
        compose_editors = []
        logger.warning("premium_diag compose_scan failed: %s", exc)
    report["compose_editors"] = compose_editors

    # ── 12. Console messages (10 s capture already running via listener) ───────
    # Attach a one-shot listener; collect for 10 s then detach.
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
        await asyncio.sleep(10)
        page.remove_listener("console", _on_console)
    except Exception as exc:
        logger.warning("premium_diag console_capture failed: %s", exc)
    report["console"] = console_msgs

    # ── 13. Network requests (already captured by NavigationTracker events) ───
    # Re-read from page via performance entries for completeness.
    try:
        net_entries = await page.evaluate("""() => {
            return performance.getEntriesByType('resource').map(e => ({
                name:         e.name,
                type:         e.initiatorType,
                duration:     Math.round(e.duration),
                transferSize: e.transferSize || 0,
            })).filter(e => {
                const u = e.name.toLowerCase();
                return u.includes('premium') || u.includes('messaging') ||
                       u.includes('compose') || u.includes('conversation') ||
                       u.includes('thread') || u.includes('sales') ||
                       u.includes('recruiter');
            }).slice(0, 60);
        }""")
    except Exception as exc:
        net_entries = []
        logger.warning("premium_diag network_entries failed: %s", exc)
    report["network_summary"] = net_entries

    # ── 14. Write premium_report.json ─────────────────────────────────────────
    # Strip outerHTML blobs from the top-level report to keep it readable;
    # full HTML is already in full_page.html.
    slim = _slim_report(report)
    report_path = out / "premium_report.json"
    try:
        report_path.write_text(
            json.dumps(slim, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        report["files"]["premium_report.json"] = str(report_path)
        logger.info("premium_diag report saved path=%s", report_path)
    except Exception as exc:
        logger.warning("premium_diag report_write failed: %s", exc)

    # ── 15. Summary log ───────────────────────────────────────────────────────
    logger.warning(
        "premium_diag SUMMARY url=%s title=%r dialogs=%d overlays=%d "
        "premium_hits=%d compose_editors=%d headings=%s",
        state.get("url"), state.get("title"),
        len(dialogs), len(overlays),
        len(premium_hits), len(compose_editors),
        [h.get("text", "")[:60] for h in headings[:5]],
    )
    for hit in premium_hits[:10]:
        logger.warning(
            "premium_diag keyword=%r path=%s text=%r",
            hit.get("keyword"), hit.get("domPath"), hit.get("matchedText", "")[:120],
        )

    return report


def _slim_report(report: dict) -> dict:
    """Return a copy of the report with outerHTML fields truncated for JSON readability."""
    import copy
    slim = copy.deepcopy(report)
    for key in ("dialogs", "overlays"):
        for item in slim.get(key, []):
            if "outerHTML" in item:
                item["outerHTML"] = item["outerHTML"][:400] + "…"
    for item in slim.get("compose_editors", []):
        if "outerHTML" in item:
            item["outerHTML"] = item["outerHTML"][:400] + "…"
    return slim
