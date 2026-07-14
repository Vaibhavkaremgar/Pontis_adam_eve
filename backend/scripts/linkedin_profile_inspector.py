from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin.config import LINKEDIN_PROFILE_ROOT
from app.linkedin.playwright import BrowserManager, LinkedInProfileInspector
from scripts.linkedin_dev_account import get_development_account_id


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _debug_dir() -> Path:
    return Path("backend/debug_logs/linkedin").resolve()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


async def _run(profile_url: str, *, debug: bool) -> int:
    started_at = datetime.now(timezone.utc)
    print(f"[{_now()}] Browser startup")
    account_id = get_development_account_id()
    profile_dir = Path(LINKEDIN_PROFILE_ROOT).expanduser().resolve() / account_id
    print(f"Selected LinkedIn account: {account_id}")
    print(f"Profile directory: {profile_dir}")
    print(f"Profile exists: {profile_dir.exists()}")
    manager = BrowserManager(account_id=account_id)
    try:
        context = await manager.start()
        print(f"[{_now()}] Browser running status: {manager.is_running()}")

        if debug:
            debug_paths = await _capture_dom_debug(context, profile_url, account_id=account_id)
            for path in debug_paths:
                print(f"Debug output: {path}")

        print(f"[{_now()}] Inspection navigation started")

        inspector = LinkedInProfileInspector(context)
        result = await inspector.inspect(profile_url)

        print(f"[{_now()}] Inspection completed")
        print(json.dumps(_serialize(result), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        logger.exception("linkedin profile inspector harness failed")
        print(f"ERROR: {exc}")
        return 1
    finally:
        print(f"[{_now()}] Browser shutdown")
        try:
            await manager.stop()
        except Exception as exc:
            logger.exception("linkedin profile inspector shutdown failed")
            print(f"Shutdown error: {exc}")
        finished_at = datetime.now(timezone.utc)
        print(f"Execution duration: {(finished_at - started_at).total_seconds():.3f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual validation harness for LinkedInProfileInspector")
    parser.add_argument("--account", dest="account_id", default="", help="LinkedIn development account id")
    parser.add_argument("--debug", action="store_true", help="Capture DOM diagnostics to backend/debug_logs/linkedin/")
    parser.add_argument("profile_url", help="LinkedIn profile URL to inspect")
    args = parser.parse_args()
    if args.account_id:
        os.environ["LINKEDIN_DEV_ACCOUNT_ID"] = args.account_id
    return asyncio.run(_run(args.profile_url, debug=bool(args.debug)))


async def _capture_dom_debug(context: Any, profile_url: str, *, account_id: str) -> list[Path]:
    page = await context.new_page()
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        data = {
            "profile_url": profile_url,
            "current_url": str(getattr(page, "url", "") or ""),
            "page_title": await _safe_title(page),
            "first_h1_text": await _first_visible_text(page, ["h1"]),
            "all_h1_text": await _all_visible_text(page, "h1"),
            "profile_header_html": await _container_html(
                page,
                [
                    '[data-view-name*="profile"] header',
                    '[data-view-name*="profile"] .pv-top-card',
                    'main header',
                    'main section',
                ],
            ),
            "profile_action_toolbar_html": await _container_html(
                page,
                [
                    '[data-view-name*="profile"] [role="toolbar"]',
                    '[data-view-name*="profile"] [role="region"]',
                    '[data-view-name*="profile"] .pv-top-card',
                    'main [role="toolbar"]',
                    'main [class*="pv-top-card"]',
                ],
            ),
            "visible_action_toolbar_buttons": await _visible_toolbar_buttons(page),
            "open_graph_meta_tags": await _open_graph_meta_tags(page),
            "json_ld_structured_data": await _json_ld_structured_data(page),
        }

        debug_root = _debug_dir()
        debug_root.mkdir(parents=True, exist_ok=True)
        stamp = _timestamp_slug()
        base = debug_root / f"{stamp}_{account_id}"
        json_path = base.with_suffix(".json")
        html_path = base.with_suffix(".html")
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        html_path.write_text(
            "<html><body><pre>" + json.dumps(data, indent=2, ensure_ascii=False).replace("<", "&lt;").replace(">", "&gt;") + "</pre></body></html>",
            encoding="utf-8",
        )
        return [json_path, html_path]
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _safe_title(page: Any) -> str:
    try:
        return str(await page.title() or "")
    except Exception:
        return ""


async def _first_visible_text(page: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible():
                text = str(await locator.inner_text(timeout=5000) or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _all_visible_text(page: Any, selector: str) -> list[str]:
    values: list[str] = []
    try:
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            item = locator.nth(index)
            try:
                if await item.is_visible():
                    text = str(await item.inner_text(timeout=5000) or "").strip()
                    if text:
                        values.append(text)
            except Exception:
                continue
    except Exception:
        return values
    return values


async def _container_html(page: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible():
                return str(await locator.evaluate("(el) => el.outerHTML") or "")
        except Exception:
            continue
    return ""


async def _visible_toolbar_buttons(page: Any) -> list[dict[str, str]]:
    toolbar = await _first_visible_locator(
        page,
        [
            '[data-view-name*="profile"] [role="toolbar"]',
            '[data-view-name*="profile"] [role="region"]',
            '[data-view-name*="profile"] .pv-top-card',
            'main [role="toolbar"]',
            'main [class*="pv-top-card"]',
        ],
    )
    if toolbar is None:
        return []
    buttons: list[dict[str, str]] = []
    for selector in ["button", "[role='button']", "[aria-label]"]:
        try:
            locator = toolbar.locator(selector)
            count = await locator.count()
            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    box = await item.bounding_box()
                    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
                        continue
                    text = str(await item.inner_text(timeout=5000) or "").strip()
                    aria = str(await item.get_attribute("aria-label") or "").strip()
                    if text or aria:
                        buttons.append({"text": text, "aria_label": aria})
                except Exception:
                    continue
        except Exception:
            continue
    return buttons


async def _open_graph_meta_tags(page: Any) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    try:
        locator = page.locator("meta[property], meta[name]")
        count = await locator.count()
        for index in range(count):
            item = locator.nth(index)
            try:
                prop = str(await item.get_attribute("property") or await item.get_attribute("name") or "").strip()
                content = str(await item.get_attribute("content") or "").strip()
                if prop and content and (
                    prop.lower().startswith("og")
                    or prop.lower().startswith("twitter")
                    or prop.lower() in {"description", "title"}
                ):
                    tags.append({"name": prop, "content": content})
            except Exception:
                continue
    except Exception:
        return tags
    return tags


async def _json_ld_structured_data(page: Any) -> list[Any]:
    data: list[Any] = []
    try:
        scripts = page.locator("script[type='application/ld+json']")
        count = await scripts.count()
        for index in range(count):
            try:
                raw = str(await scripts.nth(index).inner_text(timeout=5000) or "").strip()
                if raw:
                    data.append(json.loads(raw))
            except Exception:
                continue
    except Exception:
        return data
    return data


async def _first_visible_locator(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


if __name__ == "__main__":
    raise SystemExit(main())
