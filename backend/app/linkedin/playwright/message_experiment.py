"""message_experiment.py — Forensic experiment comparing Playwright vs manual click.

Experiments A–E, pure diagnostics, zero production logic changes.

Output: debug_logs/message_experiment/<ts>_<slug>/
  strategy_locator_click/
  strategy_force_click/
  strategy_js_click/
  strategy_goto/
  comparison_report.json
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

_OUT_DIR = Path(__file__).resolve().parents[3] / "debug_logs" / "message_experiment"

# ---------------------------------------------------------------------------
# JS payloads
# ---------------------------------------------------------------------------

_JS_ELEMENT_FORENSICS = """el => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    const listeners = {};
    try {
        const evts = getEventListeners ? getEventListeners(el) : {};
        for (const [k, v] of Object.entries(evts)) {
            listeners[k] = v.length;
        }
    } catch(e) { listeners['_error'] = String(e); }
    return {
        tag:           el.tagName,
        id:            el.id || '',
        href:          el.getAttribute('href') || '',
        onclick:       el.onclick ? el.onclick.toString().slice(0, 300) : null,
        outerHTML:     el.outerHTML.slice(0, 1000),
        innerHTML:     el.innerHTML.slice(0, 800),
        ariaLabel:     el.getAttribute('aria-label') || '',
        role:          el.getAttribute('role') || '',
        class:         el.className || '',
        dataTestid:    el.getAttribute('data-testid') || '',
        disabled:      el.disabled || false,
        tabIndex:      el.tabIndex,
        bbox:          {x: r.x, y: r.y, w: r.width, h: r.height},
        pointerEvents: cs.pointerEvents,
        zIndex:        cs.zIndex,
        position:      cs.position,
        display:       cs.display,
        visibility:    cs.visibility,
        opacity:       cs.opacity,
        cursor:        cs.cursor,
        listeners:     listeners,
    };
}"""

_JS_PAGE_SNAPSHOT = """() => {
    const dialogs = [];
    document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog').forEach(el => {
        const r = el.getBoundingClientRect();
        dialogs.push({
            tag:      el.tagName,
            role:     el.getAttribute('role') || '',
            ariaLabel:el.getAttribute('aria-label') || '',
            text:     (el.innerText || '').trim().slice(0, 400),
            visible:  r.width > 0 && r.height > 0,
            bbox:     {x: r.x, y: r.y, w: r.width, h: r.height},
            outerHTML:el.outerHTML.slice(0, 600),
        });
    });
    const composeFields = [];
    [
        'textarea','[contenteditable="true"]','[role="textbox"]',
        '[data-lexical-editor]','[aria-label*="message" i]',
        '[placeholder*="message" i]',
    ].forEach(sel => {
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
    const iframes = [];
    document.querySelectorAll('iframe').forEach(el => {
        const r = el.getBoundingClientRect();
        iframes.push({src: el.src, visible: r.width > 0 && r.height > 0, bbox: {x:r.x,y:r.y,w:r.width,h:r.height}});
    });
    return {
        url:           window.location.href,
        title:         document.title,
        readyState:    document.readyState,
        dialogs:       dialogs,
        composeFields: composeFields,
        iframes:       iframes,
    };
}"""

_JS_ACTIVE_ELEMENT = """() => {
    const el = document.activeElement;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
        tag:  el.tagName,
        role: el.getAttribute('role') || '',
        aria: el.getAttribute('aria-label') || '',
        ce:   el.getAttribute('contenteditable') || '',
        bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
    };
}"""

_JS_NETWORK_ENTRIES = """() =>
    performance.getEntriesByType('resource').map(e => ({
        url:      e.name,
        type:     e.initiatorType,
        duration: Math.round(e.duration),
        size:     e.transferSize || 0,
    }))
"""

_JS_MUTATION_OBSERVER_START = """() => {
    window.__mutations = [];
    window.__mutationObserver = new MutationObserver(records => {
        records.forEach(r => {
            window.__mutations.push({
                type:     r.type,
                target:   r.target.tagName + (r.target.id ? '#'+r.target.id : ''),
                added:    r.addedNodes.length,
                removed:  r.removedNodes.length,
                attr:     r.attributeName || '',
                addedTags: Array.from(r.addedNodes).map(n => n.tagName || '#text').slice(0,5),
            });
        });
    });
    window.__mutationObserver.observe(document.body, {
        childList: true, subtree: true, attributes: true, attributeOldValue: false
    });
    return 'started';
}"""

_JS_MUTATION_OBSERVER_STOP = """() => {
    if (window.__mutationObserver) window.__mutationObserver.disconnect();
    return window.__mutations || [];
}"""

_JS_PREVENT_DEFAULT_PROBE = """el => {
    window.__clickIntercepted = false;
    window.__defaultPrevented = false;
    window.__navigationCancelled = false;
    const handler = e => {
        window.__clickIntercepted = true;
        window.__defaultPrevented = e.defaultPrevented;
        window.__navigationCancelled = e.defaultPrevented;
    };
    el.addEventListener('click', handler, {once: true, capture: true});
    document.addEventListener('click', handler, {once: true, capture: true});
    return 'probe_attached';
}"""

_JS_PREVENT_DEFAULT_READ = """() => ({
    clickIntercepted:     window.__clickIntercepted || false,
    defaultPrevented:     window.__defaultPrevented || false,
    navigationCancelled:  window.__navigationCancelled || false,
    currentUrl:           window.location.href,
})"""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _save(path: Path, data: Any) -> None:
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("experiment save_failed path=%s error=%s", path, exc)


async def _screenshot(page: Any, path: Path) -> None:
    try:
        await page.screenshot(path=str(path), full_page=False)
    except Exception as exc:
        logger.warning("experiment screenshot_failed path=%s error=%s", path, exc)


async def _snapshot(page: Any) -> dict:
    try:
        return await page.evaluate(_JS_PAGE_SNAPSHOT) or {}
    except Exception as exc:
        return {"error": str(exc)}


async def _network(page: Any) -> list:
    try:
        return await page.evaluate(_JS_NETWORK_ENTRIES) or []
    except Exception:
        return []


def _attach_event_listeners(page: Any, store: dict) -> None:
    """Attach all page-level event listeners. Stores into `store` dict."""
    store.setdefault("requests", [])
    store.setdefault("responses", [])
    store.setdefault("navigations", [])
    store.setdefault("popups", [])
    store.setdefault("dialogs", [])
    store.setdefault("console", [])
    store.setdefault("errors", [])

    def _on_request(req: Any) -> None:
        try:
            store["requests"].append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "t": time.monotonic(),
            })
        except Exception:
            pass

    def _on_response(resp: Any) -> None:
        try:
            store["responses"].append({
                "url": resp.url,
                "status": resp.status,
                "t": time.monotonic(),
            })
        except Exception:
            pass

    def _on_framenavigated(frame: Any) -> None:
        try:
            store["navigations"].append({
                "url": frame.url,
                "name": frame.name,
                "t": time.monotonic(),
            })
        except Exception:
            pass

    def _on_popup(popup: Any) -> None:
        try:
            store["popups"].append({"url": getattr(popup, "url", ""), "t": time.monotonic()})
        except Exception:
            pass

    def _on_dialog(dialog: Any) -> None:
        try:
            store["dialogs"].append({
                "type": dialog.type,
                "message": dialog.message,
                "t": time.monotonic(),
            })
            asyncio.ensure_future(dialog.dismiss())
        except Exception:
            pass

    def _on_console(msg: Any) -> None:
        try:
            store["console"].append({
                "type": msg.type,
                "text": msg.text,
                "t": time.monotonic(),
            })
        except Exception:
            pass

    def _on_pageerror(exc: Any) -> None:
        try:
            store["errors"].append({"error": str(exc), "t": time.monotonic()})
        except Exception:
            pass

    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("framenavigated", _on_framenavigated)
    page.on("popup", _on_popup)
    page.on("dialog", _on_dialog)
    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)

    store["_handlers"] = {
        "request": _on_request,
        "response": _on_response,
        "framenavigated": _on_framenavigated,
        "popup": _on_popup,
        "dialog": _on_dialog,
        "console": _on_console,
        "pageerror": _on_pageerror,
    }


def _detach_event_listeners(page: Any, store: dict) -> None:
    handlers = store.pop("_handlers", {})
    for event, handler in handlers.items():
        try:
            page.remove_listener(event, handler)
        except Exception:
            pass


async def _observe_timeline(page: Any, duration_s: float, interval_s: float = 0.1) -> list[dict]:
    """Experiment C: poll every interval_s for duration_s seconds."""
    timeline = []
    start = time.monotonic()
    while time.monotonic() - start < duration_s:
        elapsed = round(time.monotonic() - start, 3)
        tick: dict = {"t": elapsed}
        try:
            tick["url"] = str(getattr(page, "url", ""))
        except Exception:
            tick["url"] = ""
        try:
            tick["title"] = str(await page.title())
        except Exception:
            tick["title"] = ""
        try:
            tick["active_element"] = await page.evaluate(_JS_ACTIVE_ELEMENT)
        except Exception:
            tick["active_element"] = None
        try:
            snap = await page.evaluate(_JS_PAGE_SNAPSHOT)
            tick["dialogs"] = snap.get("dialogs", [])
            tick["compose_fields"] = snap.get("composeFields", [])
            tick["iframes"] = snap.get("iframes", [])
        except Exception:
            tick["dialogs"] = []
            tick["compose_fields"] = []
            tick["iframes"] = []
        try:
            tick["shadow_editables"] = await page.evaluate("""() => {
                const r = [];
                function walk(root) {
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) {
                            el.shadowRoot.querySelectorAll(
                                'textarea,[contenteditable],[role="textbox"]'
                            ).forEach(inner => {
                                const b = inner.getBoundingClientRect();
                                r.push({host: el.tagName, tag: inner.tagName,
                                        visible: b.width > 0 && b.height > 0});
                            });
                            walk(el.shadowRoot);
                        }
                    });
                }
                walk(document);
                return r;
            }""")
        except Exception:
            tick["shadow_editables"] = []
        timeline.append(tick)
        await asyncio.sleep(interval_s)
    return timeline


# ---------------------------------------------------------------------------
# Experiment A — pre-click element forensics
# ---------------------------------------------------------------------------

async def experiment_a(page: Any, element: Any, out: Path) -> dict:
    """Capture full forensic detail of the element before any click."""
    out.mkdir(parents=True, exist_ok=True)
    await _screenshot(page, out / "pre_click.png")

    detail: dict = {}
    try:
        detail = await element.evaluate(_JS_ELEMENT_FORENSICS) or {}
    except Exception as exc:
        detail = {"error": str(exc)}

    _save(out / "element_forensics.json", detail)

    logger.warning(
        "experiment_A href=%r onclick=%r pointerEvents=%r zIndex=%r "
        "display=%r visibility=%r opacity=%r cursor=%r bbox=%s listeners=%s",
        detail.get("href"), detail.get("onclick"),
        detail.get("pointerEvents"), detail.get("zIndex"),
        detail.get("display"), detail.get("visibility"),
        detail.get("opacity"), detail.get("cursor"),
        detail.get("bbox"), detail.get("listeners"),
    )
    return detail


# ---------------------------------------------------------------------------
# Experiment B — full event capture for 15 seconds
# ---------------------------------------------------------------------------

async def experiment_b(page: Any, element: Any, out: Path, href: str) -> dict:
    """Attach all listeners, click, capture 15 s of events."""
    out.mkdir(parents=True, exist_ok=True)
    store: dict = {}
    _attach_event_listeners(page, store)

    net_before = await _network(page)
    await _screenshot(page, out / "before_click.png")

    t0 = time.monotonic()
    try:
        await element.click(timeout=8000)
    except Exception as exc:
        logger.warning("experiment_B click_failed: %s", exc)
        store["click_error"] = str(exc)

    await asyncio.sleep(15)
    await _screenshot(page, out / "after_15s.png")

    net_after = await _network(page)
    _detach_event_listeners(page, store)

    # Normalise timestamps to elapsed seconds from click
    for key in ("requests", "responses", "navigations", "popups", "dialogs", "console", "errors"):
        for item in store.get(key, []):
            item["elapsed_s"] = round(item.pop("t", t0) - t0, 3)

    new_net = [e for e in net_after if e not in net_before]

    result = {
        "requests":   store.get("requests", []),
        "responses":  store.get("responses", []),
        "navigations":store.get("navigations", []),
        "popups":     store.get("popups", []),
        "dialogs_pw": store.get("dialogs", []),
        "console":    store.get("console", []),
        "errors":     store.get("errors", []),
        "new_network_entries": new_net,
        "click_error": store.get("click_error", ""),
    }

    _save(out / "events.json", result)
    _save(out / "network.json", new_net)
    _save(out / "console.json", store.get("console", []))
    _save(out / "requests.json", store.get("requests", []))
    _save(out / "responses.json", store.get("responses", []))

    logger.warning(
        "experiment_B requests=%d responses=%d navigations=%d "
        "popups=%d dialogs=%d console=%d errors=%d new_net=%d",
        len(result["requests"]), len(result["responses"]),
        len(result["navigations"]), len(result["popups"]),
        len(result["dialogs_pw"]), len(result["console"]),
        len(result["errors"]), len(new_net),
    )
    return result


# ---------------------------------------------------------------------------
# Experiment C — 15 s timeline at 100 ms resolution
# ---------------------------------------------------------------------------

async def experiment_c(page: Any, element: Any, out: Path) -> dict:
    """Start MutationObserver, click, then poll every 100 ms for 15 s."""
    out.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    # Start mutation observer
    try:
        await page.evaluate(_JS_MUTATION_OBSERVER_START)
    except Exception as exc:
        logger.warning("experiment_C mutation_observer_start failed: %s", exc)

    await _screenshot(page, screenshots_dir / "t0000ms.png")

    try:
        await element.click(timeout=8000)
    except Exception as exc:
        logger.warning("experiment_C click_failed: %s", exc)

    # Screenshots at key moments while timeline runs
    async def _timed_screenshots() -> None:
        for ms in (500, 1000, 2000, 5000, 10000, 15000):
            await asyncio.sleep(ms / 1000)
            await _screenshot(page, screenshots_dir / f"t{ms:05d}ms.png")

    timeline_task = asyncio.ensure_future(_observe_timeline(page, 15.0, 0.1))
    screenshots_task = asyncio.ensure_future(_timed_screenshots())
    timeline = await timeline_task
    await screenshots_task

    # Stop mutation observer
    mutations: list = []
    try:
        mutations = await page.evaluate(_JS_MUTATION_OBSERVER_STOP) or []
    except Exception as exc:
        logger.warning("experiment_C mutation_observer_stop failed: %s", exc)

    result = {"timeline": timeline, "mutations": mutations}
    _save(out / "timeline.json", timeline)
    _save(out / "mutations.json", mutations)

    # Summary
    urls_seen = list(dict.fromkeys(t["url"] for t in timeline if t.get("url")))
    first_compose = next(
        (t["t"] for t in timeline if t.get("compose_fields")), None
    )
    first_dialog = next(
        (t["t"] for t in timeline if any(d.get("visible") for d in t.get("dialogs", []))), None
    )
    logger.warning(
        "experiment_C urls_seen=%s first_compose_at=%s first_dialog_at=%s mutations=%d",
        urls_seen, first_compose, first_dialog, len(mutations),
    )
    return result


# ---------------------------------------------------------------------------
# Experiment D — preventDefault / navigation intercept probe
# ---------------------------------------------------------------------------

async def experiment_d(page: Any, element: Any, out: Path, href: str) -> dict:
    """Probe whether click is intercepted and whether href becomes the URL."""
    out.mkdir(parents=True, exist_ok=True)
    await _screenshot(page, out / "before.png")

    # Attach probe
    try:
        await element.evaluate(_JS_PREVENT_DEFAULT_PROBE)
    except Exception as exc:
        logger.warning("experiment_D probe_attach failed: %s", exc)

    url_before = str(getattr(page, "url", ""))

    try:
        await element.click(timeout=8000)
    except Exception as exc:
        logger.warning("experiment_D click_failed: %s", exc)

    await asyncio.sleep(3)

    probe_result: dict = {}
    try:
        probe_result = await page.evaluate(_JS_PREVENT_DEFAULT_READ) or {}
    except Exception as exc:
        probe_result = {"error": str(exc)}

    url_after = str(getattr(page, "url", ""))
    href_became_url = href and (href in url_after or url_after.startswith("https://www.linkedin.com" + href))

    await _screenshot(page, out / "after.png")

    result = {
        "href":                href,
        "url_before":          url_before,
        "url_after":           url_after,
        "href_became_url":     href_became_url,
        "click_intercepted":   probe_result.get("clickIntercepted"),
        "default_prevented":   probe_result.get("defaultPrevented"),
        "navigation_cancelled":probe_result.get("navigationCancelled"),
    }
    _save(out / "prevent_default_probe.json", result)

    logger.warning(
        "experiment_D href=%r href_became_url=%s click_intercepted=%s "
        "default_prevented=%s navigation_cancelled=%s url_after=%s",
        href, href_became_url,
        result["click_intercepted"], result["default_prevented"],
        result["navigation_cancelled"], url_after,
    )
    return result


# ---------------------------------------------------------------------------
# Experiment E — four click strategies
# ---------------------------------------------------------------------------

async def _run_strategy(
    page: Any,
    element: Any,
    href: str,
    strategy: str,
    out: Path,
) -> dict:
    """Run one click strategy and capture full state."""
    out.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    store: dict = {}
    _attach_event_listeners(page, store)

    try:
        await page.evaluate(_JS_MUTATION_OBSERVER_START)
    except Exception:
        pass

    net_before = await _network(page)
    snap_before = await _snapshot(page)
    await _screenshot(page, screenshots_dir / "before.png")

    click_error = ""
    t0 = time.monotonic()

    if strategy == "locator_click":
        try:
            await element.click(timeout=8000)
        except Exception as exc:
            click_error = str(exc)

    elif strategy == "force_click":
        try:
            await element.click(force=True, timeout=8000)
        except Exception as exc:
            click_error = str(exc)

    elif strategy == "js_click":
        try:
            await element.evaluate("el => el.click()")
        except Exception as exc:
            click_error = str(exc)

    elif strategy == "goto":
        try:
            full_href = href if href.startswith("http") else f"https://www.linkedin.com{href}"
            await page.goto(full_href, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            click_error = str(exc)

    # Observe 15 s timeline
    timeline = await _observe_timeline(page, 15.0, 0.5)

    for ms in (1000, 3000, 8000, 15000):
        idx = min(int(ms / 500), len(timeline) - 1)
        if idx >= 0:
            await _screenshot(page, screenshots_dir / f"t{ms:05d}ms.png")

    mutations: list = []
    try:
        mutations = await page.evaluate(_JS_MUTATION_OBSERVER_STOP) or []
    except Exception:
        pass

    net_after = await _network(page)
    snap_after = await _snapshot(page)
    _detach_event_listeners(page, store)

    for key in ("requests", "responses", "navigations", "console", "errors"):
        for item in store.get(key, []):
            item["elapsed_s"] = round(item.pop("t", t0) - t0, 3)

    new_net = [e for e in net_after if e not in net_before]
    urls_seen = list(dict.fromkeys(t["url"] for t in timeline if t.get("url")))
    first_compose = next((t["t"] for t in timeline if t.get("compose_fields")), None)
    first_dialog = next(
        (t["t"] for t in timeline if any(d.get("visible") for d in t.get("dialogs", []))), None
    )
    final_url = str(getattr(page, "url", ""))

    final_state = {
        "strategy":       strategy,
        "click_error":    click_error,
        "final_url":      final_url,
        "urls_seen":      urls_seen,
        "compose_detected": first_compose is not None,
        "first_compose_at": first_compose,
        "premium_dialog": any(
            any(
                __import__("re").search(r"premium|upgrade|inmail|trial|unlock",
                    (d.get("text") or ""), __import__("re").I)
                for d in t.get("dialogs", []) if d.get("visible")
            )
            for t in timeline
        ),
        "first_dialog_at": first_dialog,
        "navigated_to_messaging": any("/messaging/" in u for u in urls_seen),
        "snap_before":    snap_before,
        "snap_after":     snap_after,
    }

    _save(out / "final_state.json", final_state)
    _save(out / "timeline.json", timeline)
    _save(out / "mutations.json", mutations)
    _save(out / "network.json", new_net)
    _save(out / "console.json", store.get("console", []))
    _save(out / "requests.json", store.get("requests", []))
    _save(out / "responses.json", store.get("responses", []))

    logger.warning(
        "experiment_E strategy=%s compose=%s premium=%s navigated=%s "
        "final_url=%s click_error=%r",
        strategy, final_state["compose_detected"],
        final_state["premium_dialog"],
        final_state["navigated_to_messaging"],
        final_url, click_error,
    )
    return final_state


async def experiment_e(
    page_factory: Any,   # async callable() → fresh page
    element_factory: Any,  # async callable(page) → element locator
    href: str,
    out: Path,
) -> dict:
    """Run all four strategies, each on a fresh page, collect results."""
    out.mkdir(parents=True, exist_ok=True)
    strategies = ["locator_click", "force_click", "js_click", "goto"]
    results: dict[str, dict] = {}

    for strategy in strategies:
        folder_name = f"strategy_{strategy}"
        strategy_out = out / folder_name
        logger.warning("experiment_E starting strategy=%s", strategy)
        try:
            page = await page_factory()
            element = await element_factory(page)
            if element is None and strategy != "goto":
                logger.warning("experiment_E element_not_found strategy=%s", strategy)
                results[strategy] = {"error": "element_not_found", "strategy": strategy}
                try:
                    await page.close()
                except Exception:
                    pass
                continue
            result = await _run_strategy(page, element, href, strategy, strategy_out)
            results[strategy] = result
        except Exception as exc:
            logger.exception("experiment_E strategy=%s failed", strategy)
            results[strategy] = {"error": str(exc), "strategy": strategy}
        finally:
            try:
                await page.close()
            except Exception:
                pass

    return results


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def build_comparison_report(
    exp_a: dict,
    exp_b: dict,
    exp_c: dict,
    exp_d: dict,
    exp_e: dict,
) -> dict:
    """Produce comparison_report.json from all experiment results."""
    import re

    strategies = exp_e if isinstance(exp_e, dict) else {}

    opened_compose   = [s for s, r in strategies.items() if r.get("compose_detected")]
    showed_premium   = [s for s, r in strategies.items() if r.get("premium_dialog")]
    navigated        = [s for s, r in strategies.items() if r.get("navigated_to_messaging")]
    stayed_on_profile = [
        s for s, r in strategies.items()
        if not r.get("navigated_to_messaging") and not r.get("compose_detected")
    ]

    # Network diff: which strategies triggered messaging API calls
    net_by_strategy: dict[str, list] = {}
    for s, r in strategies.items():
        net_by_strategy[s] = [
            e["url"] for e in (r.get("snap_after", {}).get("network") or [])
            if "messaging" in e.get("url", "").lower()
        ]

    # Mutation diff: which strategies produced the most DOM mutations
    # (stored in mutations.json, not in final_state — summarise from timeline)
    mutation_counts: dict[str, int] = {}
    for s, r in strategies.items():
        mutation_counts[s] = len(r.get("mutations", []))

    # Experiment D verdict
    d_verdict = "unknown"
    if exp_d.get("default_prevented"):
        d_verdict = "CLICK_INTERCEPTED_DEFAULT_PREVENTED — SPA router consumed the click"
    elif exp_d.get("href_became_url"):
        d_verdict = "HREF_BECAME_URL — normal navigation occurred"
    elif not exp_d.get("href_became_url") and exp_d.get("href"):
        d_verdict = "HREF_DID_NOT_BECOME_URL — click did not navigate; SPA handler likely fired"

    # Root cause hypothesis
    hypotheses: list[str] = []

    if exp_a.get("pointerEvents") == "none":
        hypotheses.append(
            "HYPOTHESIS_A1: pointer-events=none on the element — "
            "Playwright click lands on a different element underneath."
        )
    if exp_a.get("onclick"):
        hypotheses.append(
            "HYPOTHESIS_A2: onclick handler present — "
            "may fire Premium upsell instead of navigation."
        )
    if exp_d.get("default_prevented"):
        hypotheses.append(
            "HYPOTHESIS_D1: SPA router intercepted the click and called preventDefault(). "
            "Playwright's locator.click() triggers the SPA handler which opens Premium modal. "
            "page.goto(href) bypasses the handler and may work correctly."
        )
    if "goto" in opened_compose and "locator_click" not in opened_compose:
        hypotheses.append(
            "HYPOTHESIS_E1: page.goto(href) opens compose but locator.click() does not. "
            "Root cause: the click event triggers a Premium upsell handler before navigation. "
            "Fix: use page.goto(href) instead of clicking the element."
        )
    if "js_click" in opened_compose and "locator_click" not in opened_compose:
        hypotheses.append(
            "HYPOTHESIS_E2: JS el.click() opens compose but locator.click() does not. "
            "Root cause: Playwright's synthetic click event differs from a native DOM click "
            "in a way that triggers a different event handler."
        )
    if not hypotheses:
        hypotheses.append(
            "HYPOTHESIS_UNKNOWN: No clear divergence detected. "
            "Review timeline.json and mutations.json for each strategy manually."
        )

    report = {
        "summary": {
            "strategies_that_opened_compose":    opened_compose,
            "strategies_that_showed_premium":    showed_premium,
            "strategies_that_navigated":         navigated,
            "strategies_that_stayed_on_profile": stayed_on_profile,
        },
        "experiment_a": {
            "href":           exp_a.get("href"),
            "onclick":        exp_a.get("onclick"),
            "pointer_events": exp_a.get("pointerEvents"),
            "z_index":        exp_a.get("zIndex"),
            "cursor":         exp_a.get("cursor"),
            "listeners":      exp_a.get("listeners"),
            "bbox":           exp_a.get("bbox"),
        },
        "experiment_b": {
            "total_requests":    len(exp_b.get("requests", [])),
            "total_responses":   len(exp_b.get("responses", [])),
            "navigations":       exp_b.get("navigations", []),
            "popups":            exp_b.get("popups", []),
            "pw_dialogs":        exp_b.get("dialogs_pw", []),
            "console_errors":    [m for m in exp_b.get("console", []) if m.get("type") == "error"],
            "page_errors":       exp_b.get("errors", []),
        },
        "experiment_c": {
            "first_compose_at":  next(
                (t["t"] for t in exp_c.get("timeline", []) if t.get("compose_fields")), None
            ),
            "first_dialog_at":   next(
                (t["t"] for t in exp_c.get("timeline", [])
                 if any(d.get("visible") for d in t.get("dialogs", []))), None
            ),
            "urls_seen":         list(dict.fromkeys(
                t["url"] for t in exp_c.get("timeline", []) if t.get("url")
            )),
            "total_mutations":   len(exp_c.get("mutations", [])),
        },
        "experiment_d": {
            "href":                exp_d.get("href"),
            "href_became_url":     exp_d.get("href_became_url"),
            "click_intercepted":   exp_d.get("click_intercepted"),
            "default_prevented":   exp_d.get("default_prevented"),
            "navigation_cancelled":exp_d.get("navigation_cancelled"),
            "verdict":             d_verdict,
        },
        "experiment_e": {
            s: {
                "compose_detected":          r.get("compose_detected"),
                "premium_dialog":            r.get("premium_dialog"),
                "navigated_to_messaging":    r.get("navigated_to_messaging"),
                "final_url":                 r.get("final_url"),
                "first_compose_at":          r.get("first_compose_at"),
                "first_dialog_at":           r.get("first_dialog_at"),
                "click_error":               r.get("click_error"),
            }
            for s, r in strategies.items()
        },
        "root_cause_hypotheses": hypotheses,
    }
    return report


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_full_experiment(
    page_factory: Any,
    element_factory: Any,
    href: str,
    slug: str = "",
) -> dict:
    """Run all experiments A–E and produce comparison_report.json.

    Args:
        page_factory:    async callable() → fresh Playwright page (already navigated to profile)
        element_factory: async callable(page) → the Message button locator
        href:            the href attribute of the Message button
        slug:            short identifier for the output directory

    Returns:
        The full comparison report dict.
    """
    ts = _ts()
    label = f"{ts}_{slug}" if slug else ts
    out = _OUT_DIR / label
    out.mkdir(parents=True, exist_ok=True)

    logger.warning("message_experiment START slug=%s out=%s", slug, out)

    # Experiments A–D share one page (already on profile, element already found)
    page_abd = await page_factory()
    element_abd = await element_factory(page_abd)

    exp_a = await experiment_a(page_abd, element_abd, out / "exp_a_forensics")
    href = href or exp_a.get("href", "")

    # Reload page for B (fresh event state)
    try:
        await page_abd.reload(wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)
    except Exception:
        pass
    element_abd = await element_factory(page_abd)
    exp_b = await experiment_b(page_abd, element_abd, out / "exp_b_events", href)

    # Reload for C
    try:
        await page_abd.reload(wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)
    except Exception:
        pass
    element_abd = await element_factory(page_abd)
    exp_c = await experiment_c(page_abd, element_abd, out / "exp_c_timeline")

    # Reload for D
    try:
        await page_abd.reload(wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)
    except Exception:
        pass
    element_abd = await element_factory(page_abd)
    exp_d = await experiment_d(page_abd, element_abd, out / "exp_d_prevent_default", href)

    try:
        await page_abd.close()
    except Exception:
        pass

    # Experiment E — four independent strategies, each on a fresh page
    exp_e = await experiment_e(page_factory, element_factory, href, out)

    # Comparison report
    report = build_comparison_report(exp_a, exp_b, exp_c, exp_d, exp_e)
    _save(out / "comparison_report.json", report)

    logger.warning(
        "message_experiment DONE slug=%s "
        "compose_strategies=%s premium_strategies=%s hypotheses=%s",
        slug,
        report["summary"]["strategies_that_opened_compose"],
        report["summary"]["strategies_that_showed_premium"],
        [h[:80] for h in report["root_cause_hypotheses"]],
    )
    return report
