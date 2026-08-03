"""probe_job_posting_dom.py — Read-only DOM probe for the LinkedIn job posting wizard.

Navigates to the wizard URL, waits for the page to settle, then dumps:
  1. All <form> elements (tag, id, class, action)
  2. All [role='main'], [role='dialog'], [role='region'] elements
  3. All elements whose class contains 'job', 'posting', 'wizard', 'form', 'modal'
  4. The first 6000 chars of <body> outerHTML
  5. A screenshot

Never clicks anything. Never fills anything. Read-only.

Usage (from backend/):
    python -m scripts.probe_job_posting_dom --account-id linkedin-dev-account
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.job_posting.job_posting_constants import DIRECT_POST_JOB_URL

_OUT = Path(__file__).resolve().parents[1] / "debug_logs" / "job_posting_dom_probe"


async def main(account_id: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = _OUT / ts
    out.mkdir(parents=True, exist_ok=True)

    manager = BrowserManager(account_id=account_id)
    context = await manager.get_browser()
    page = await context.new_page()
    page.set_default_timeout(30_000)

    try:
        print(f"Navigating to {DIRECT_POST_JOB_URL} ...")
        await page.goto(DIRECT_POST_JOB_URL, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(3.0)   # let React/SPA finish rendering

        url = page.url
        title = await page.title()
        print(f"URL   : {url}")
        print(f"Title : {title}")

        # ── Screenshot ────────────────────────────────────────────────────────
        ss = out / "page.png"
        await page.screenshot(path=str(ss), full_page=True)
        print(f"Screenshot → {ss}")

        # ── Probe 1: all <form> elements ──────────────────────────────────────
        forms = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('form')).map(el => ({
                id:      el.id || '',
                classes: el.className || '',
                action:  el.action || '',
                name:    el.name || '',
                outerHTML_preview: el.outerHTML.slice(0, 300),
            }))
        """)
        print(f"\n── FORMS ({len(forms)}) ──")
        for f in forms:
            print(f"  id={f['id']!r}  class={f['classes'][:80]!r}  action={f['action']!r}")

        # ── Probe 2: landmark roles ───────────────────────────────────────────
        landmarks = await page.evaluate("""() =>
            Array.from(document.querySelectorAll(
                "[role='main'],[role='dialog'],[role='region'],[role='form'],[role='alertdialog']"
            )).map(el => ({
                tag:     el.tagName.toLowerCase(),
                role:    el.getAttribute('role') || '',
                id:      el.id || '',
                classes: el.className.toString().slice(0, 120) || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                outerHTML_preview: el.outerHTML.slice(0, 200),
            }))
        """)
        print(f"\n── LANDMARK ROLES ({len(landmarks)}) ──")
        for l in landmarks:
            print(f"  <{l['tag']} role={l['role']!r} id={l['id']!r} aria-label={l['ariaLabel']!r}")
            print(f"    class={l['classes']!r}")

        # ── Probe 3: class-keyword elements ───────────────────────────────────
        kw_els = await page.evaluate("""() => {
            const keywords = ['job-posting', 'job_posting', 'jobPosting',
                              'wizard', 'stepper', 'modal', 'dialog',
                              'scaffold', 'artdeco-modal'];
            const results = [];
            for (const kw of keywords) {
                const els = document.querySelectorAll(`[class*='${kw}']`);
                for (const el of Array.from(els).slice(0, 5)) {
                    results.push({
                        kw,
                        tag:     el.tagName.toLowerCase(),
                        id:      el.id || '',
                        classes: el.className.toString().slice(0, 150),
                        outerHTML_preview: el.outerHTML.slice(0, 250),
                    });
                }
            }
            return results;
        }""")
        print(f"\n── CLASS-KEYWORD ELEMENTS ({len(kw_els)}) ──")
        for e in kw_els:
            print(f"  kw={e['kw']!r}  <{e['tag']} id={e['id']!r}")
            print(f"    class={e['classes']!r}")

        # ── Probe 4: data-* attributes that look like form markers ────────────
        data_attrs = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('*')).filter(el => {
                const attrs = Array.from(el.attributes).map(a => a.name);
                return attrs.some(a => a.startsWith('data-test') || a.includes('job') || a.includes('posting'));
            }).slice(0, 20).map(el => ({
                tag:     el.tagName.toLowerCase(),
                id:      el.id || '',
                classes: el.className.toString().slice(0, 100),
                attrs:   Array.from(el.attributes)
                              .filter(a => a.name.startsWith('data-') || a.name.includes('job'))
                              .map(a => `${a.name}=${a.value.slice(0,60)}`)
                              .slice(0, 8),
                outerHTML_preview: el.outerHTML.slice(0, 200),
            }))
        """)
        print(f"\n── DATA-* / JOB ATTR ELEMENTS ({len(data_attrs)}) ──")
        for e in data_attrs:
            print(f"  <{e['tag']} id={e['id']!r}  attrs={e['attrs']}")
            print(f"    class={e['classes']!r}")

        # ── Probe 5: body outerHTML head ──────────────────────────────────────
        body_html = await page.evaluate("() => document.body.outerHTML.slice(0, 8000)")
        html_path = out / "body_head.html"
        html_path.write_text(body_html, encoding="utf-8")
        print(f"\n── BODY HTML (first 8000 chars) → {html_path}")

        # ── Probe 6: full body HTML ───────────────────────────────────────────
        full_html = await page.locator("body").inner_html(timeout=10_000)
        full_path = out / "body_full.html"
        full_path.write_text(full_html, encoding="utf-8")
        print(f"── FULL BODY HTML → {full_path}")

        # ── Save JSON summary ─────────────────────────────────────────────────
        summary = {
            "url": url, "title": title,
            "forms": forms,
            "landmarks": landmarks,
            "kw_elements": kw_els,
            "data_attr_elements": data_attrs,
        }
        json_path = out / "probe_summary.json"
        json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"── JSON summary → {json_path}")
        print(f"\nAll artifacts in: {out}")

    finally:
        await page.close()
        await manager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only DOM probe for LinkedIn job posting wizard")
    parser.add_argument("--account-id", default="linkedin-dev-account")
    args = parser.parse_args()
    asyncio.run(main(args.account_id))
