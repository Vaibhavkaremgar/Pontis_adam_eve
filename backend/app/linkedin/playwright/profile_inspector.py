from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.linkedin.playwright.action_discovery import (
    _INTERACTIVE_SELECTORS,
    _PRIMARY_ACTION_SELECTORS,
    _HEADER_SELECTORS,
    _action_score,
    _is_visible as _ad_is_visible,
    _read_label as _ad_read_label,
    _normalize as _ad_normalize,
    build_capabilities,
    close_messaging_overlays,
    find_toolbar,
    find_primary_actions,
    find_connect_action,
    find_message_action,
)
from app.linkedin.playwright.profile_types import (
    LinkedInAvailableAction,
    LinkedInProfileConnectionState,
    LinkedInProfileInspectionResult,
    ProfileCapabilities,
)

logger = logging.getLogger(__name__)


async def find_connect_button_on_page(page: Any, timeout_ms: int = 30000) -> Any | None:
    """Return the visible Connect-button Locator on *page*, or None.

    Delegates to action_discovery.find_connect_action — single implementation.
    """
    return await find_connect_action(page)


async def find_message_button_on_page(page: Any, timeout_ms: int = 30000) -> Any | None:
    """Return the visible Message-button Locator on *page*, or None.

    Delegates to action_discovery.find_message_action — single implementation.
    """
    return await find_message_action(page)


class LinkedInProfileInspector:
    def __init__(self, browser_context: Any, timeout_ms: int = 30000) -> None:
        self.browser_context = browser_context
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = min(max(timeout_ms, 10000), 15000)
        self._last_connection_reason = ""
        self._field_debug: dict[str, str] = {}
        self._timings: list[tuple[str, int]] = []

    async def inspect(self, profile_url: str) -> LinkedInProfileInspectionResult:
        started_at = datetime.now(timezone.utc)
        self._field_debug = {}
        self._timings = []
        page = None
        try:
            page = await self.browser_context.new_page()
            if hasattr(page, "set_default_timeout"):
                page.set_default_timeout(self.timeout_ms)
            if hasattr(page, "set_default_navigation_timeout"):
                page.set_default_navigation_timeout(self.navigation_timeout_ms)

            page_state = await self._timed_step("navigation", self._stage_navigation, page, profile_url=profile_url)
            profile_state = await self._timed_step("profile extraction", self._stage_profile_extraction, page, profile_url, page_state)
            toolbar_state = await self._timed_step("toolbar extraction", self._stage_toolbar_extraction, page, profile_url, profile_state)
            overflow_state = await self._timed_step("overflow extraction", self._stage_overflow_extraction, page, profile_url, toolbar_state)

            raw_button_labels = list(toolbar_state["button_labels"])
            for label in overflow_state["overflow_labels"]:
                if label not in raw_button_labels:
                    raw_button_labels.append(label)

            connection_state = self._detect_connection_state(
                button_labels=raw_button_labels,
                body_text=page_state["body_text"],
                page_url=page_state["page_url"],
                profile_private=page_state["profile_private"],
                login_required=page_state["login_required"],
                profile_not_found=page_state["profile_not_found"],
                account_restricted=page_state["account_restricted"],
            )
            if profile_state["profile_exists"] and connection_state == LinkedInProfileConnectionState.PROFILE_NOT_FOUND:
                connection_state = LinkedInProfileConnectionState.UNKNOWN
                self._last_connection_reason = "profile signals present; ignoring false not-found classification"

            available_actions, _, _ = self._detect_available_actions(
                raw_button_labels,
                connection_state,
                toolbar_selector=toolbar_state["toolbar_selector"],
                button_debug=toolbar_state["button_debug"],
            )

            # Build capability model alongside legacy state.
            caps = await build_capabilities(
                page,
                body_text=page_state["body_text"],
                page_url=page_state["page_url"],
                login_required=page_state["login_required"],
                session_expired=page_state["account_restricted"],
                profile_not_found=page_state["profile_not_found"],
                profile_private=page_state["profile_private"],
            )
            caps.log_summary(profile_url, logger)

            result = LinkedInProfileInspectionResult(
                profile_url=profile_url,
                profile_name=profile_state["profile_name"],
                current_title=profile_state["current_title"],
                company=profile_state["company"],
                page_loaded=True,
                profile_exists=profile_state["profile_exists"],
                profile_private=page_state["profile_private"],
                login_required=page_state["login_required"],
                connection_state=connection_state,
                available_actions=available_actions,
                inspection_timestamp=started_at.isoformat(),
                raw_button_labels=raw_button_labels,
                page_url=page_state["page_url"],
                capabilities=caps,
            )
            try:
                object.__setattr__(result, "connect_location", toolbar_state["connect_location"])
            except Exception:
                pass
            return result
        except Exception as exc:
            logger.exception("linkedin profile inspection failed profile_url=%s", profile_url)
            return LinkedInProfileInspectionResult(
                profile_url=profile_url,
                page_loaded=False,
                profile_exists=False,
                login_required=False,
                connection_state=LinkedInProfileConnectionState.UNKNOWN,
                available_actions=[LinkedInAvailableAction.NONE],
                inspection_timestamp=started_at.isoformat(),
                error=str(exc),
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def inspect_capabilities(self, profile_url: str) -> ProfileCapabilities:
        """Lightweight inspection that returns only the capability model.

        Navigates to the profile, reads the toolbar, and returns a
        ProfileCapabilities without the full profile extraction pipeline.
        Workers that only need capabilities should call this.
        """
        page = None
        try:
            page = await self.browser_context.new_page()
            if hasattr(page, "set_default_timeout"):
                page.set_default_timeout(self.timeout_ms)
            if hasattr(page, "set_default_navigation_timeout"):
                page.set_default_navigation_timeout(self.navigation_timeout_ms)

            await page.goto(profile_url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=1500)
            except Exception:
                pass
            try:
                await page.wait_for_selector("main, [role='main'], body", timeout=2000)
            except Exception:
                pass
            await close_messaging_overlays(page)

            page_url = str(getattr(page, "url", "") or "")
            title = ""
            try:
                title = str(await page.title() or "")
            except Exception:
                pass
            body_text = ""
            try:
                body_text = str(await page.locator("body").inner_text(timeout=1500) or "")
            except Exception:
                pass

            login_required = self._looks_like_login_required(page_url, title, body_text)
            session_expired = self._looks_like_restricted(page_url, title, body_text)
            profile_not_found = self._looks_like_not_found(page_url, title, body_text)
            profile_private = self._looks_like_private(page_url, title, body_text)

            caps = await build_capabilities(
                page,
                body_text=body_text,
                page_url=page_url,
                login_required=login_required,
                session_expired=session_expired,
                profile_not_found=profile_not_found,
                profile_private=profile_private,
            )
            caps.log_summary(profile_url, logger)
            return caps
        except Exception as exc:
            logger.exception("inspect_capabilities failed profile_url=%s", profile_url)
            return ProfileCapabilities()
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _stage_navigation(self, page: Any, profile_url: str) -> dict[str, Any]:
        await self._navigate(page, profile_url)
        await self._wait_for_dom_ready(page)
        page_url = str(getattr(page, "url", "") or "")
        title = await self._safe_title(page)
        body_text = await self._safe_body_text(page)
        return {
            "page_url": page_url,
            "title": title,
            "body_text": body_text,
            "login_required": self._looks_like_login_required(page_url, title, body_text),
            "account_restricted": self._looks_like_restricted(page_url, title, body_text),
            "profile_not_found": self._looks_like_not_found(page_url, title, body_text),
            "profile_private": self._looks_like_private(page_url, title, body_text),
        }

    async def _stage_profile_extraction(self, page: Any, profile_url: str, page_state: dict[str, Any]) -> dict[str, Any]:
        profile_header = await self._find_profile_header(page)
        profile_name, profile_name_reason = await self._extract_profile_field(page, profile_header, ["h1", "h2"], field_name="profile_name")
        current_title, current_title_reason = await self._extract_profile_field(page, profile_header, ["p", "[data-view-name*='profile'] p"], field_name="current_title")
        company, company_reason = await self._extract_profile_field(page, profile_header, ["a[href*='/company/']", "[aria-label*='company']"], field_name="company")

        if not profile_name:
            profile_name, profile_name_reason = await self._extract_open_graph_field(page, "profile_name")
        if not current_title:
            current_title, current_title_reason = await self._extract_open_graph_field(page, "current_title")
        if not company:
            company, company_reason = await self._extract_open_graph_field(page, "company")
        if not profile_name:
            profile_name, profile_name_reason = await self._extract_jsonld_field(page, "profile_name")
        if not current_title:
            current_title, current_title_reason = await self._extract_jsonld_field(page, "current_title")
        if not company:
            company, company_reason = await self._extract_jsonld_field(page, "company")
        if not profile_name:
            profile_name, profile_name_reason = await self._extract_header_text_fallback(page, profile_header, "profile_name")
        if not current_title:
            current_title, current_title_reason = await self._extract_header_text_fallback(page, profile_header, "current_title")
        if not company:
            company, company_reason = await self._extract_header_text_fallback(page, profile_header, "company")

        self._field_debug = {
            "profile_name": profile_name_reason,
            "current_title": current_title_reason,
            "company": company_reason,
        }
        profile_signals = await self._collect_profile_signals(page, profile_header, None)
        profile_exists = bool(profile_signals) and not (page_state["login_required"] or page_state["account_restricted"])
        if page_state["profile_not_found"] and profile_signals:
            profile_exists = True
        return {
            "profile_header": profile_header,
            "profile_name": profile_name,
            "profile_name_reason": profile_name_reason,
            "current_title": current_title,
            "current_title_reason": current_title_reason,
            "company": company,
            "company_reason": company_reason,
            "profile_signals": profile_signals,
            "profile_exists": profile_exists,
        }

    async def _stage_toolbar_extraction(self, page: Any, profile_url: str, profile_state: dict[str, Any]) -> dict[str, Any]:
        action_toolbar, toolbar_selector = await self._find_action_toolbar(page, profile_state["profile_header"])

        # --- instrumentation: stage 1 & 2 — toolbar found? which selector? ---
        if action_toolbar is None:
            logger.info("toolbar_stage toolbar_found=False profile_url=%s", profile_url)
        else:
            logger.info("toolbar_stage toolbar_found=True selector=%r profile_url=%s", toolbar_selector, profile_url)

        button_labels, button_debug = await self._collect_action_labels(action_toolbar)

        # --- instrumentation: stage 4 — enumerate every interactive element ---
        await self._dump_profile_toolbar_debug(page, action_toolbar, toolbar_selector, profile_url)

        # Use the same tightened match as _detect_connection_state: exact "connect"
        # or "connect " prefix.  This prevents a suggestion-widget label from
        # suppressing the overflow expansion that reveals "Remove connection".
        connect_location = "PRIMARY" if any(
            label.lower() == "connect" or label.lower().startswith("connect ")
            for label in button_labels
        ) else "NONE"
        return {
            "action_toolbar": action_toolbar,
            "toolbar_selector": toolbar_selector,
            "button_labels": button_labels,
            "button_debug": button_debug,
            "connect_location": connect_location,
        }

    async def _stage_overflow_extraction(self, page: Any, profile_url: str, toolbar_state: dict[str, Any]) -> dict[str, Any]:
        if toolbar_state["connect_location"] != "NONE" or toolbar_state["action_toolbar"] is None:
            return {"overflow_labels": [], "overflow_debug": ""}
        overflow_labels, overflow_debug = await self._extract_overflow_actions(page, toolbar_state["action_toolbar"], profile_url=profile_url)
        return {"overflow_labels": overflow_labels, "overflow_debug": overflow_debug}

    async def _navigate(self, page: Any, profile_url: str) -> None:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)

    async def _wait_for_dom_ready(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1500)
        except Exception:
            pass
        try:
            await page.wait_for_selector("main, [role='main'], body", timeout=2000)
        except Exception:
            pass

    async def _safe_title(self, page: Any) -> str:
        try:
            return str(await page.title() or "")
        except Exception:
            return ""

    async def _safe_body_text(self, page: Any) -> str:
        try:
            return str(await page.locator("body").inner_text(timeout=1500) or "")
        except Exception:
            return ""

    async def _find_profile_header(self, page: Any) -> Any | None:
        return await self._find_first_visible_locator(page, _HEADER_SELECTORS)

    async def _find_action_toolbar(self, page: Any, profile_header: Any | None = None) -> tuple[Any | None, str]:
        """Delegate to action_discovery.find_toolbar — single implementation."""
        # Tier 1 — scoped to header
        if profile_header is not None:
            for selector in _PRIMARY_ACTION_SELECTORS:
                try:
                    loc = profile_header.locator(selector).first
                    if await self._is_visible(loc):
                        summary = await self._summarize_action_candidate(loc)
                        if summary["action_score"] > 0:
                            return loc, f"header::{selector}"
                except Exception:
                    continue

        # Tier 2 — page-wide
        for selector in _PRIMARY_ACTION_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await self._is_visible(loc):
                    summary = await self._summarize_action_candidate(loc)
                    if summary["action_score"] > 0:
                        return loc, f"page::{selector}"
            except Exception:
                continue

        # Tier 3 — header itself
        if profile_header is not None:
            try:
                if await self._is_visible(profile_header):
                    summary = await self._summarize_action_candidate(profile_header)
                    if summary["action_score"] > 0:
                        return profile_header, "profile_header"
            except Exception:
                pass

        return None, ""

    async def _collect_action_labels(self, toolbar: Any | None) -> tuple[list[str], dict[str, str]]:
        labels: list[str] = []
        debug: dict[str, str] = {"matched_selector": "", "ignored_labels": ""}
        if toolbar is None:
            return labels, debug
        for item in await self._read_interactive_items(toolbar, max_items=20):
            label = self._normalize_menu_label(item["label"])
            if label and label not in labels:
                labels.append(label)
        debug["matched_selector"] = "interactive_items"
        return labels, debug

    async def _extract_overflow_actions(self, page: Any, toolbar: Any | None, *, profile_url: str = "") -> tuple[list[str], str]:
        if toolbar is None:
            return [], ""
        more_button = await self._find_overflow_button(toolbar)
        if more_button is None:
            return [], ""

        before_snapshot = await self._overflow_state_snapshot(page)
        expanded_before = await self._read_aria_expanded(more_button)
        try:
            await more_button.click(timeout=self.timeout_ms)
        except Exception:
            return [], "click_failed"

        popup = await self._wait_for_overflow_popup(page, before_snapshot, more_button)
        if popup is None:
            return [], "popup_not_detected"
        labels = await self._collect_overflow_menu_labels(popup)
        if expanded_before is not None:
            await self._wait_for_aria_expanded_change(more_button, expanded_before)
        return labels, "popup_detected"

    async def _find_overflow_button(self, toolbar: Any) -> Any | None:
        for selector in ["button[aria-label*='More']", "button", "[role='button']"]:
            try:
                locator = toolbar.locator(selector)
                count = await locator.count()
                for index in range(count):
                    item = locator.nth(index)
                    if not await self._is_visible(item):
                        continue
                    label = await self._read_label(item)
                    if "more" in label.lower():
                        return item
            except Exception:
                continue
        return None

    async def _collect_overflow_menu_labels(self, menu: Any) -> list[str]:
        if menu is None:
            return []
        labels: list[str] = []
        for item in await self._read_interactive_items(menu, max_items=50):
            label = item["label"].strip()
            if label and label not in labels:
                labels.append(label)
        return labels

    def _normalize_menu_label(self, label: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
        return cleaned.strip(" .,-•·–—")

    async def _summarize_action_candidate(self, container: Any) -> dict[str, Any]:
        try:
            buttons = container.locator("button, [role='button'], a[role='button']")
            count = await buttons.count()
        except Exception:
            count = 0
        visible_labels = [item["label"] for item in await self._read_interactive_items(container, max_items=10) if item["label"]]
        action_score = sum(1 for label in visible_labels if any(token in label.lower() for token in ("connect", "message", "follow", "more", "pending", "request sent", "withdraw")))
        return {
            "child_count": count,
            "visible_buttons": len(visible_labels),
            "visible_roles": count,
            "aria_labels": ",".join(visible_labels[:5]),
            "inner_text": " | ".join(visible_labels[:5]),
            "action_score": action_score,
            "reason": "visible action controls",
        }

    async def _find_profile_action_container(self, container: Any) -> Any | None:
        return container if container is not None else None

    async def _looks_like_action_container(self, container: Any) -> bool:
        return await self._is_visible(container) if container is not None else False

    async def _extract_profile_field(
        self,
        page: Any,
        profile_header: Any | None,
        selectors: Iterable[str],
        *,
        field_name: str,
        profile_url: str = "",
    ) -> tuple[str, str]:
        for container in (profile_header, page):
            if container is None:
                continue
            for selector in selectors:
                try:
                    locator = container.locator(selector).first
                    if await self._is_visible(locator):
                        text = str(await locator.inner_text(timeout=1000) or "").strip()
                        if text:
                            return self._clean_field_value(field_name, text), selector
                except Exception:
                    continue
        return "", ""

    async def _extract_visible_heading(self, page: Any, profile_header: Any | None) -> tuple[str, str]:
        return await self._extract_profile_field(page, profile_header, ["h1", "h2"], field_name="profile_name")

    async def _extract_current_title_from_header(self, page: Any, profile_header: Any | None) -> tuple[str, str]:
        return await self._extract_profile_field(page, profile_header, ["p"], field_name="current_title")

    async def _extract_company_from_profile(self, page: Any, profile_header: Any | None) -> tuple[str, str]:
        return await self._extract_profile_field(page, profile_header, ["a[href*='/company/']"], field_name="company")

    def _is_valid_company_text(self, text: str, profile_name: str, current_title: str) -> bool:
        return bool(self._clean_company_text(text, profile_name, current_title))

    def _clean_company_text(self, text: str, profile_name: str, current_title: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" .,-|")
        if cleaned in {profile_name.strip(), current_title.strip(), ""}:
            return ""
        return cleaned

    async def _extract_company_from_header(self, profile_header: Any, profile_name: str, current_title: str) -> tuple[str, str]:
        return "", ""

    async def _find_current_experience_container(self, page: Any) -> Any | None:
        return None

    async def _extract_company_from_experience_container(self, container: Any, profile_name: str, current_title: str) -> tuple[str, str]:
        return "", ""

    async def _extract_open_graph_field(self, page: Any, field_name: str) -> tuple[str, str]:
        selectors_by_field = {
            "profile_name": ["meta[property='og:title']", "meta[name='twitter:title']"],
            "current_title": ["meta[property='og:description']", "meta[name='description']"],
            "company": ["meta[property='og:description']", "meta[name='description']"],
        }
        for selector in selectors_by_field.get(field_name, []):
            try:
                locator = page.locator(selector).first
                content = str(await locator.get_attribute("content") or "").strip()
                if content:
                    parsed = self._parse_og_value(field_name, content)
                    if parsed:
                        return parsed, selector
            except Exception:
                continue
        return "", ""

    async def _extract_jsonld_field(self, page: Any, field_name: str) -> tuple[str, str]:
        try:
            scripts = page.locator("script[type='application/ld+json']")
            count = await scripts.count()
        except Exception:
            return "", ""
        for index in range(min(count, 10)):
            try:
                raw = str(await scripts.nth(index).inner_text(timeout=self.timeout_ms // 2) or "").strip()
                if not raw:
                    continue
                payload = json.loads(raw)
            except Exception:
                continue
            value = self._extract_from_jsonld(payload, field_name)
            if value:
                return value, f"jsonld[{index}]"
        return "", ""

    async def _extract_header_text_fallback(self, page: Any, profile_header: Any | None, field_name: str) -> tuple[str, str]:
        selectors = {
            "profile_name": ["h1", "h2"],
            "current_title": ["p"],
            "company": ["a[href*='/company/']"],
        }.get(field_name, [])
        for selector in selectors:
            try:
                container = profile_header or page
                locator = container.locator(selector).first
                if await self._is_visible(locator):
                    text = str(await locator.inner_text(timeout=800) or "").strip()
                    if text:
                        return self._clean_field_value(field_name, text), selector
            except Exception:
                continue
        return "", ""

    def _parse_og_value(self, field_name: str, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        if field_name == "profile_name":
            return text.split(" | ", 1)[0].strip()
        if field_name in {"current_title", "company"} and " at " in text:
            left, right = text.split(" at ", 1)
            return left.strip() if field_name == "current_title" else right.strip()
        return ""

    def _extract_from_jsonld(self, payload: Any, field_name: str) -> str:
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if field_name == "profile_name":
                for key in ("name", "alternateName"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            elif field_name == "current_title":
                for key in ("jobTitle", "description"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            elif field_name == "company":
                employer = node.get("worksFor")
                if isinstance(employer, dict):
                    value = employer.get("name")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return ""

    async def _find_first_visible_locator(self, page: Any, selectors: Iterable[str]) -> Any | None:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await self._is_visible(locator):
                    return locator
            except Exception:
                continue
        return None

    async def _collect_profile_signals(self, page: Any, profile_header: Any | None, action_toolbar: Any | None) -> list[str]:
        signals: list[str] = []
        if profile_header is not None and await self._is_visible(profile_header):
            signals.append("header")
        if action_toolbar is not None and await self._is_visible(action_toolbar):
            signals.append("toolbar")
        if await self._find_first_visible_locator(page, ["h1", "h2"]):
            signals.append("heading")
        if await self._find_first_visible_locator(page, ["a[href*='/messaging/compose/']"]):
            signals.append("message")
        if await self._find_first_visible_locator(page, ["a[href*='/company/']"]):
            signals.append("company")
        return signals

    async def _read_candidate_texts(self, container: Any, selectors: Iterable[str], *, max_items: int = 8) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for selector in selectors:
            try:
                locator = container.locator(selector)
                count = await locator.count()
                for index in range(min(count, max_items)):
                    item = locator.nth(index)
                    if not await self._is_visible(item):
                        continue
                    text = str(await item.inner_text(timeout=600) or "").strip()
                    if text:
                        results.append((selector, text))
            except Exception:
                continue
        return results

    async def _first_visible_match(self, containers: list[tuple[str, Any]], selectors: Iterable[str], signal_name: str) -> str:
        for container_name, container in containers:
            for selector in selectors:
                try:
                    locator = container.locator(selector).first
                    if await self._is_visible(locator):
                        return f"{signal_name}:{container_name}::{selector}"
                except Exception:
                    continue
        return ""

    async def _selector_name(self, locator: Any) -> str:
        try:
            value = await locator.evaluate("(el) => el.getAttribute('aria-label') || el.getAttribute('role') || el.tagName")
            return str(value or "").strip()
        except Exception:
            return ""

    async def _is_visible(self, locator: Any) -> bool:
        try:
            return bool(await locator.is_visible())
        except Exception:
            return False

    async def _is_toolbar_visible(self, locator: Any) -> bool:
        return await self._is_visible(locator)

    async def _read_label(self, locator: Any) -> str:
        for method_name in ("inner_text", "text_content"):
            try:
                value = await getattr(locator, method_name)(timeout=600)
                text = str(value or "").strip()
                if text:
                    return text
            except Exception:
                continue
        try:
            return str(await locator.get_attribute("aria-label") or "").strip()
        except Exception:
            return ""

    def _is_allowed_action_label(self, label: str) -> bool:
        normalized = label.strip().lower()
        return bool(normalized) and any(token in normalized for token in ("connect", "message", "follow", "more", "pending", "withdraw", "remove", "accept", "ignore", "unfollow", "inmail"))

    async def _timed_step(self, label: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        started = datetime.now(timezone.utc)
        result = await func(*args, **kwargs)
        self._timings.append((label, self._duration_ms(started)))
        return result

    def _log_timing_summary(self, profile_url: str) -> None:
        return

    def _looks_like_login_required(self, page_url: str, title: str, body_text: str) -> bool:
        text = f"{page_url} {title} {body_text}".lower()
        return any(token in text for token in ("sign in", "login", "log in", "join linkedin"))

    def _looks_like_restricted(self, page_url: str, title: str, body_text: str) -> bool:
        text = f"{page_url} {title} {body_text}".lower()
        return any(token in text for token in ("restricted", "unusual activity", "suspicious", "checkpoint"))

    def _looks_like_not_found(self, page_url: str, title: str, body_text: str) -> bool:
        text = f"{page_url} {title} {body_text}".lower()
        return any(token in text for token in ("profile not found", "page not found", "not found", "doesn't exist"))

    def _looks_like_private(self, page_url: str, title: str, body_text: str) -> bool:
        text = f"{page_url} {title} {body_text}".lower()
        return any(token in text for token in ("private profile", "linkedin member", "this profile is not available", "closed profile"))

    def _detect_connection_state(
        self,
        *,
        button_labels: list[str],
        body_text: str,
        page_url: str,
        profile_private: bool,
        login_required: bool,
        profile_not_found: bool,
        account_restricted: bool,
    ) -> LinkedInProfileConnectionState:
        labels = {label.lower() for label in button_labels}

        # --- instrumentation: stage 5 — log normalised label set ---
        logger.info("detect_state_labels labels=%s", labels)

        # --- instrumentation: stage 6 — log which branch is taken ---
        if login_required:
            logger.info("detect_state_branch branch=LOGIN_REQUIRED")
            self._last_connection_reason = "login-required signals detected"
            return LinkedInProfileConnectionState.LOGIN_REQUIRED
        if account_restricted:
            logger.info("detect_state_branch branch=SESSION_EXPIRED")
            self._last_connection_reason = "checkpoint or challenge signals detected"
            return LinkedInProfileConnectionState.SESSION_EXPIRED
        if profile_not_found:
            logger.info("detect_state_branch branch=PROFILE_NOT_FOUND")
            self._last_connection_reason = "not-found signals detected"
            return LinkedInProfileConnectionState.PROFILE_NOT_FOUND
        if profile_private:
            logger.info("detect_state_branch branch=PRIVATE_PROFILE")
            self._last_connection_reason = "private profile signals detected"
            return LinkedInProfileConnectionState.PRIVATE_PROFILE
        if any("pending" in label or "request sent" in label or "invitation sent" in label or "withdraw invitation" in label for label in labels):
            logger.info("detect_state_branch branch=REQUEST_PENDING")
            self._last_connection_reason = "request-pending toolbar label"
            return LinkedInProfileConnectionState.REQUEST_PENDING
        # MESSAGE_AVAILABLE is checked before CONNECT_AVAILABLE.
        # A 1st-degree connection shows a Message button; the Connect button only
        # appears for non-connections.  Checking message first prevents a stray
        # "connect" substring (e.g. from a suggestion widget or an aria-label like
        # "Invite to connect") from shadowing the correct MESSAGE_AVAILABLE state.
        if any("message" == label or label.startswith("message ") or "inmail" in label for label in labels):
            logger.info("detect_state_branch branch=MESSAGE_AVAILABLE")
            self._last_connection_reason = "message toolbar label"
            return LinkedInProfileConnectionState.MESSAGE_AVAILABLE
        if any("remove connection" in label or label == "remove" for label in labels):
            logger.info("detect_state_branch branch=CONNECTED via remove-connection label")
            self._last_connection_reason = "connected toolbar label"
            return LinkedInProfileConnectionState.CONNECTED
        # Tightened connect match: require the label to equal "connect" exactly or
        # start with "connect " (with a trailing space).  This excludes "reconnect"
        # and aria-labels such as "Invite John to connect" that contain the substring
        # but do not represent a primary Connect action button.
        if any(label == "connect" or label.startswith("connect ") for label in labels):
            logger.info("detect_state_branch branch=CONNECT_AVAILABLE")
            self._last_connection_reason = "connect toolbar label"
            return LinkedInProfileConnectionState.CONNECT_AVAILABLE
        if any("follow" in label for label in labels):
            logger.info("detect_state_branch branch=FOLLOW_AVAILABLE")
            self._last_connection_reason = "follow toolbar label"
            return LinkedInProfileConnectionState.FOLLOW_AVAILABLE
        if "connected" in body_text.lower() or "1st degree" in body_text.lower():
            logger.info("detect_state_branch branch=CONNECTED via body_text")
            self._last_connection_reason = "connected text without toolbar label"
            return LinkedInProfileConnectionState.CONNECTED

        # --- instrumentation: stage 7 — log UNKNOWN reason ---
        if not button_labels:
            _unknown_reason = "no_labels"
        elif not labels:
            _unknown_reason = "no_toolbar"
        else:
            _unknown_reason = "labels_didnt_match_any_rule"
        logger.warning(
            "detect_state_branch branch=UNKNOWN reason=%s labels=%s",
            _unknown_reason,
            labels,
        )
        self._last_connection_reason = "no confident toolbar state match"
        return LinkedInProfileConnectionState.UNKNOWN

    def _detect_available_actions(
        self,
        button_labels: list[str],
        connection_state: LinkedInProfileConnectionState,
        *,
        toolbar_selector: str,
        button_debug: dict[str, str] | None = None,
    ) -> tuple[list[LinkedInAvailableAction], str, float]:
        labels = {label.lower() for label in button_labels}
        if connection_state in {
            LinkedInProfileConnectionState.LOGIN_REQUIRED,
            LinkedInProfileConnectionState.ACCOUNT_RESTRICTED,
            LinkedInProfileConnectionState.PROFILE_NOT_FOUND,
            LinkedInProfileConnectionState.PRIVATE_PROFILE,
            LinkedInProfileConnectionState.SESSION_EXPIRED,
        }:
            return [LinkedInAvailableAction.NONE], f"blocked by state {connection_state.value}", 1.0
        actions: list[LinkedInAvailableAction] = []
        if any("connect" in label for label in labels):
            actions.append(LinkedInAvailableAction.CONNECT)
        if any("message" in label or "inmail" in label for label in labels):
            actions.append(LinkedInAvailableAction.MESSAGE)
        if any("follow" in label for label in labels):
            actions.append(LinkedInAvailableAction.FOLLOW)
        if any(label == "more" or label.startswith("more ") for label in labels):
            actions.append(LinkedInAvailableAction.MORE)
        if not actions:
            actions = [LinkedInAvailableAction.NONE]
        return actions, "resolved from labels", 0.5

    async def _dump_action_container_debug(self, page: Any, toolbar: Any | None, toolbar_selector: str, profile_url: str) -> None:
        return

    async def _dump_profile_toolbar_debug(self, page: Any, toolbar: Any | None, toolbar_selector: str, profile_url: str) -> None:
        """Capture and log every interactive element inside the primary action bar."""
        debug_dir = Path(__file__).resolve().parents[4] / "debug_logs" / "profile_toolbar"
        debug_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", profile_url.rstrip("/").split("/")[-1])[:40]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        prefix = debug_dir / f"{ts}_{slug}"

        toolbar_html = ""
        if toolbar is None:
            logger.info("toolbar_debug toolbar_html=none selector=%r profile_url=%s", toolbar_selector, profile_url)
        else:
            try:
                toolbar_html = await toolbar.evaluate("el => el.outerHTML")
                logger.info("toolbar_debug toolbar_html_length=%d selector=%r profile_url=%s", len(toolbar_html), toolbar_selector, profile_url)
            except Exception as exc:
                logger.warning("toolbar_debug toolbar_html_failed: %s", exc)

        html_path = str(prefix) + "_toolbar.html"
        try:
            Path(html_path).write_text(toolbar_html, encoding="utf-8")
            logger.info("toolbar_debug html_saved path=%s", html_path)
        except Exception as exc:
            logger.warning("toolbar_debug html_write_failed: %s", exc)

        container = toolbar if toolbar is not None else page.locator("body")
        elements: list[dict] = []
        for sel in _INTERACTIVE_SELECTORS:
            try:
                locs = container.locator(sel)
                count = await locs.count()
                for i in range(min(count, 30)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        attrs = await item.evaluate(
                            """el => ({
                                tag: el.tagName,
                                id: el.id || '',
                                class: el.className || '',
                                role: el.getAttribute('role') || '',
                                aria_label: el.getAttribute('aria-label') || '',
                                inner_text: (el.innerText || '').trim().slice(0, 120),
                                text_content: (el.textContent || '').trim().slice(0, 120),
                                disabled: el.disabled || false
                            })"""
                        )
                        attrs["selector"] = sel
                        elements.append(attrs)
                    except Exception:
                        continue
            except Exception:
                continue

        # deduplicate by (tag, inner_text, aria_label)
        seen: set[tuple] = set()
        unique_elements: list[dict] = []
        for el in elements:
            key = (el.get("tag"), el.get("inner_text"), el.get("aria_label"))
            if key not in seen:
                seen.add(key)
                unique_elements.append(el)

        logger.info(
            "toolbar_debug elements_found count=%d selector=%r profile_url=%s",
            len(unique_elements),
            toolbar_selector,
            profile_url,
        )
        for idx, el in enumerate(unique_elements):
            logger.info(
                "toolbar_element[%d] tag=%s inner_text=%r text_content=%r "
                "aria_label=%r role=%r class=%r id=%r disabled=%s",
                idx,
                el.get("tag"),
                el.get("inner_text"),
                el.get("text_content"),
                el.get("aria_label"),
                el.get("role"),
                el.get("class"),
                el.get("id"),
                el.get("disabled"),
            )

        json_path = str(prefix) + "_elements.json"
        try:
            Path(json_path).write_text(
                json.dumps(unique_elements, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("toolbar_debug elements_json_saved path=%s", json_path)
        except Exception as exc:
            logger.warning("toolbar_debug elements_json_write_failed: %s", exc)

    async def _dump_header_and_candidate_debug(self, page: Any, profile_header: Any, candidates: list[tuple[str, Any, str]], *, profile_url: str) -> None:
        return

    def _duration_ms(self, started_at: datetime) -> int:
        return int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

    async def _read_interactive_items(self, container: Any, *, max_items: int = 20) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for selector in _INTERACTIVE_SELECTORS:
            try:
                locator = container.locator(selector)
                count = await locator.count()
            except Exception:
                continue
            for index in range(min(count, max_items)):
                try:
                    item = locator.nth(index)
                    if not await self._is_visible(item):
                        continue
                    label = await self._read_label(item)
                    if label:
                        aria = str(await item.get_attribute("aria-label") or "").strip()
                        items.append({"label": label, "aria_label": aria, "accessible_name": aria})
                except Exception:
                    continue
        return items

    async def _overflow_state_snapshot(self, page: Any) -> dict[str, int]:
        return {
            "dialogs": await self._count_visible(page, ["[role='dialog']"]),
            "popups": await self._count_visible(page, ["[role='menu']", "[role='listbox']", "[role='dialog']"]),
            "menus": await self._count_visible(page, ["[role='menu']"]),
        }

    async def _count_visible(self, page: Any, selectors: list[str]) -> int:
        count = 0
        for selector in selectors:
            try:
                locator = page.locator(selector)
                total = await locator.count()
                for index in range(total):
                    if await locator.nth(index).is_visible():
                        count += 1
            except Exception:
                continue
        return count

    async def _wait_for_overflow_popup(self, page: Any, before_snapshot: dict[str, int], more_button: Any) -> Any | None:
        deadline = datetime.now(timezone.utc).timestamp() + max(3.0, self.timeout_ms / 1000.0)
        while datetime.now(timezone.utc).timestamp() < deadline:
            popup = await self._find_visible_popup(page)
            if popup is not None:
                after_snapshot = await self._overflow_state_snapshot(page)
                if after_snapshot != before_snapshot or await self._read_aria_expanded(more_button) == "true":
                    return popup
        return await self._find_visible_popup(page)

    async def _find_visible_popup(self, page: Any) -> Any | None:
        for selector in ["[role='dialog']", "[role='menu']", "[role='listbox']", "[aria-modal='true']"]:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for index in range(count):
                    item = locator.nth(index)
                    if await self._is_visible(item):
                        return item
            except Exception:
                continue
        return None

    async def _read_aria_expanded(self, locator: Any) -> str:
        try:
            return str(await locator.get_attribute("aria-expanded") or "").strip().lower()
        except Exception:
            return ""

    async def _wait_for_aria_expanded_change(self, locator: Any, before: str) -> None:
        deadline = datetime.now(timezone.utc).timestamp() + 2.5
        while datetime.now(timezone.utc).timestamp() < deadline:
            if await self._read_aria_expanded(locator) != before:
                return

    def _clean_field_value(self, field_name: str, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()
