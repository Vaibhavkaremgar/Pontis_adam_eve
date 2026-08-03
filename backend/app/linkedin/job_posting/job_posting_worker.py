"""job_posting_worker.py — LinkedIn Job Posting worker.

Execution mode is configured externally. The worker shares one path up to
the final publish step, then either stops safely in dry-run mode or clicks
Publish in live mode.

Real LinkedIn job posting flow:
  1. Navigate to /jobs/
  2. Click "Post a free job" in the left sidebar
  3. Enter job title → click Continue
  4. On the summary page: click the Edit pencil icon
  5. Edit details (company, workplace type, location, job type, etc.)
  6. Enter description if provided, otherwise leave AI-generated content

Dependencies (all Phase 1 — nothing frozen is touched):
  LinkedInAccountLockManager  ← platform/account_lock_manager.py
  BrowserManager              ← playwright/browser_manager.py
  FormEngine                  ← playwright/form_engine.py
  verification_helpers        ← playwright/verification_helpers.py
  job_posting_constants       ← job_posting/job_posting_constants.py
  JobPostingSpec              ← job_posting/job_posting_spec.py
  JobPostingResult            ← job_posting/job_posting_result.py
"""
from __future__ import annotations

import asyncio
import logging
import json
import time
from pathlib import Path
from typing import Any

from app.core.config import is_production_environment
from app.linkedin.platform.account_lock_manager import LinkedInAccountLockManager
from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.browser_context import BrowserContextConfig
from app.linkedin.playwright.form_engine import FormEngine
from app.linkedin.playwright.success_detector import SuccessDetector
from app.linkedin.job_posting.job_posting_spec import JobPostingSpec
from app.linkedin.job_posting.job_posting_result import JobPostingResult, StepDiagnostic, WorkerStatus
from app.linkedin.job_posting.job_posting_types import (
    JobPostingExecutionMode,
    LinkedInSessionState,
    LinkedInSessionValidation,
    PublishLifecycleState,
)
from app.linkedin.job_posting.job_posting_constants import (
    JOBS_HOMEPAGE_URL,
    JOBS_NAV_SELECTORS,
    POST_FREE_JOB_SELECTORS,
    TITLE_INPUT_SELECTORS,
    CONTINUE_BUTTON_SELECTORS,
    EDIT_PENCIL_SELECTORS,
    DESCRIPTION_EDITOR_SELECTORS,
    CONTINUE_WITHOUT_PROMOTE_SELECTORS,
    NEXT_BUTTON_SELECTORS,
    PUBLISH_BUTTON_SELECTORS,
    FORM_CONTAINER_SELECTORS,
    VALIDATION_ERROR_SELECTORS,
)

logger = logging.getLogger(__name__)

_lock_manager = LinkedInAccountLockManager()
_ARTIFACT_ROOT = Path(__file__).resolve().parents[4] / "debug_logs" / "job_posting_dry_run"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _is_visible(locator: Any) -> bool:
    try:
        return bool(await locator.is_visible(timeout=2000))
    except Exception:
        return False


async def _safe_text(locator: Any) -> str:
    for method in ("inner_text", "text_content"):
        try:
            val = str(await getattr(locator, method)(timeout=1000) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


async def _js_click(locator: Any) -> None:
    await locator.evaluate("(el) => el.click()")


async def _debug_dump_application_method_artifacts(
    page: Any,
    dialog: Any,
    popup: Any,
    out: Path,
) -> None:
    try:
        dialog_html = str(await dialog.evaluate("(el) => el.outerHTML") or "")
    except Exception:
        dialog_html = ""
    (out / "application_method_dialog.html").write_text(dialog_html, encoding="utf-8")
    try:
        await page.screenshot(path=str(out / "application_method_dialog.png"), full_page=False)
    except Exception:
        pass

    popup_html = ""
    popup_json: dict[str, Any] = {}
    try:
        popup_html = str(await popup.evaluate("(el) => el.outerHTML") or "")
    except Exception:
        pass
    try:
        popup_json = {
            "tag": str(await popup.evaluate("(el) => el.tagName.toLowerCase()") or ""),
            "role": str(await popup.get_attribute("role") or ""),
            "aria_label": str(await popup.get_attribute("aria-label") or ""),
            "text": await _safe_text(popup),
            "class": str(await popup.get_attribute("class") or ""),
            "bbox": await popup.bounding_box(),
            "outerHTML": popup_html[:500],
        }
    except Exception:
        popup_json = {"outerHTML": popup_html[:500]}
    (out / "application_method_popup.html").write_text(popup_html, encoding="utf-8")
    (out / "application_method_popup.json").write_text(json.dumps(popup_json, indent=2, default=str), encoding="utf-8")


async def _collect_control_snapshot(container: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        locs = container.locator("button, [role='button'], [role='combobox'], [role='listbox'], [role='menuitem'], input, select, textarea, [contenteditable='true']")
        count = await locs.count()
        for i in range(count):
            loc = locs.nth(i)
            try:
                if not await _is_visible(loc):
                    continue
                tag = str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or "")
                rows.append(
                    {
                        "index": i,
                        "tag": tag,
                        "role": str(await loc.get_attribute("role") or ""),
                        "text": await _safe_text(loc),
                        "aria_label": str(await loc.get_attribute("aria-label") or ""),
                        "aria_expanded": str(await loc.get_attribute("aria-expanded") or ""),
                        "placeholder": str(await loc.get_attribute("placeholder") or ""),
                        "class": str(await loc.get_attribute("class") or ""),
                        "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                    }
                )
            except Exception:
                continue
    except Exception:
        pass
    return rows


async def _capture_failure_artifacts(page: Any, diag: StepDiagnostic, run_ts: str) -> None:
    try:
        out = _ARTIFACT_ROOT / run_ts
        out.mkdir(parents=True, exist_ok=True)
        prefix = out / f"step{diag.step_index}_failure"
        try:
            await page.screenshot(path=str(prefix) + ".png", full_page=False)
            diag.screenshot_path = str(prefix) + ".png"
        except Exception as exc:
            logger.warning("worker screenshot_failed: %s", exc)
        try:
            html = str(await page.locator("body").inner_html(timeout=5000) or "")
            (Path(str(prefix) + ".html")).write_text(html, encoding="utf-8")
            diag.html_path = str(prefix) + ".html"
        except Exception as exc:
            logger.warning("worker html_capture_failed: %s", exc)
    except Exception as exc:
        logger.warning("worker artifact_capture_failed: %s", exc)


async def _find_form_container(page: Any) -> Any | None:
    for sel in FORM_CONTAINER_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await _is_visible(loc):
                logger.debug("worker form_container_matched sel=%r", sel)
                return loc
        except Exception:
            continue
    logger.warning("worker form_container_no_match tried=%d selectors", len(FORM_CONTAINER_SELECTORS))
    return None


async def _collect_validation_errors(page: Any) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    selectors = [
        "[aria-invalid='true']",
        "[role='alert']",
        "[aria-live='assertive']",
        "[aria-errormessage]",
        ".artdeco-inline-feedback--error",
        "[class*='error']",
        "[class*='invalid']",
        "[data-test*='error']",
    ]
    ignore_tokens = (
        "loading results",
        "company description",
        "draft with ai",
        "autocomple",
        "suggestion",
        "options",
        "tooltip",
        "help",
        "information",
    )
    for sel in selectors:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 20)):
                item = locs.nth(i)
                if not await _is_visible(item):
                    continue
                text = await _safe_text(item)
                if not text:
                    continue
                normalized = _normalize_text(text)
                if any(token in normalized for token in ignore_tokens):
                    continue
                try:
                    if sel == "[aria-invalid='true']":
                        role = str(await item.get_attribute("role") or "")
                        aria_live = str(await item.get_attribute("aria-live") or "")
                        aria_err = str(await item.get_attribute("aria-errormessage") or "")
                        if not role and not aria_live and not aria_err:
                            # aria-invalid is only a hint; keep it only if it is attached to real invalid UI.
                            pass
                except Exception:
                    pass
                if text not in seen:
                    seen.add(text)
                    errors.append(text[:200])
        except Exception:
            continue
    return errors


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _validation_key(text: str) -> str:
    normalized = _normalize_text(text)
    if "job location" in normalized or "employee location" in normalized:
        return "location"
    if "company" in normalized:
        return "company"
    if "workplace" in normalized:
        return "workplace_type"
    if "job type" in normalized:
        return "job_type"
    if "title" in normalized:
        return "title"
    if "description" in normalized:
        return "description"
    return ""


def _is_live_publish_allowed() -> bool:
    return is_production_environment()


async def _any_visible(page: Any, selectors: list[str]) -> list[str]:
    hits: list[str] = []
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await _is_visible(loc):
                hits.append(selector)
        except Exception:
            continue
    return hits


async def _validate_linkedin_session(page: Any) -> LinkedInSessionValidation:
    try:
        url = str(getattr(page, "url", "") or "")
        title = str(await page.title() or "")
        body = str(await page.locator("body").inner_text(timeout=2000) or "")
    except Exception:
        return LinkedInSessionValidation(
            state=LinkedInSessionState.UNKNOWN,
            reason="unable to read page state",
            url=str(getattr(page, "url", "") or ""),
            signals=["page_read_failed"],
        )

    normalized = _normalize_text(f"{url} {title} {body}")
    url_lower = url.lower()
    title_lower = title.lower()
    signals: list[str] = []

    if not url_lower.startswith("https://www.linkedin.com") and "linkedin.com" not in url_lower:
        return LinkedInSessionValidation(
            state=LinkedInSessionState.UNKNOWN,
            reason="unexpected redirect to non-linkedin domain",
            url=url,
            title=title,
            signals=["unexpected_redirect"],
        )

    captcha_selectors = [
        "iframe[src*='captcha' i]",
        "iframe[src*='hcaptcha' i]",
        "iframe[src*='recaptcha' i]",
        "textarea[name='g-recaptcha-response']",
        "input[name='captcha']",
        "div[class*='captcha' i]",
    ]
    captcha_tokens = ("captcha", "recaptcha", "hcaptcha")
    captcha_hits = await _any_visible(page, captcha_selectors)
    if captcha_hits or any(token in normalized for token in captcha_tokens):
        signals.extend(captcha_hits)
        if any(token in normalized for token in captcha_tokens):
            signals.append("captcha_text")
        return LinkedInSessionValidation(
            state=LinkedInSessionState.CAPTCHA,
            reason="captcha challenge detected",
            url=url,
            title=title,
            signals=signals,
        )

    checkpoint_selectors = [
        "input[name='pin']",
        "input[autocomplete='one-time-code']",
        "input[name*='verification' i]",
        "input[name*='challenge' i]",
        "iframe[src*='checkpoint' i]",
        "iframe[src*='challenge' i]",
        "[aria-label*='Verify your identity' i]",
    ]
    checkpoint_tokens = (
        "checkpoint",
        "challenge",
        "security verification",
        "verify your identity",
        "unusual activity",
        "suspicious activity",
        "account restricted",
        "temporarily restricted",
        "verification code",
        "one-time code",
    )
    checkpoint_hits = await _any_visible(page, checkpoint_selectors)
    if checkpoint_hits or any(token in normalized for token in checkpoint_tokens):
        signals.extend(checkpoint_hits)
        if any(token in normalized for token in checkpoint_tokens):
            signals.append("checkpoint_text")
        return LinkedInSessionValidation(
            state=LinkedInSessionState.CHECKPOINT,
            reason="checkpoint or security verification detected",
            url=url,
            title=title,
            signals=signals,
        )

    login_selectors = [
        "input[name='session_key']",
        "input[name='session_password']",
        "form[action*='/uas/login']",
        "form[action*='/uas/login'] button[type='submit']",
    ]
    login_tokens = (
        "linkedin: log in or sign up",
        "/login",
        "/uas/login",
        "sign in",
        "log in",
    )
    login_hits = await _any_visible(page, login_selectors)
    if login_hits or any(token in normalized for token in login_tokens):
        signals.extend(login_hits)
        if any(token in normalized for token in ("expired", "session expired", "signed out", "session ended")):
            signals.append("session_expired_text")
            return LinkedInSessionValidation(
                state=LinkedInSessionState.SESSION_EXPIRED,
                reason="login page shows session-expired signals",
                url=url,
                title=title,
                signals=signals,
            )
        if "/login" in url_lower or "/uas/login" in url_lower or any(token in title_lower for token in ("log in", "sign in")):
            signals.append("login_url_or_title")
        return LinkedInSessionValidation(
            state=LinkedInSessionState.LOGIN_REQUIRED,
            reason="linkedin login form detected",
            url=url,
            title=title,
            signals=signals,
        )

    auth_selectors = [
        "[data-test-global-nav]",
        ".global-nav",
        "[aria-label='Home']",
        "[aria-label*='My Network' i]",
        "[aria-label*='Messaging' i]",
        "[aria-label*='Notifications' i]",
        "[aria-label*='Me' i]",
        "[aria-label*='Profile' i]",
        "[data-control-name='nav.settings']",
        "input[placeholder*='Search' i]",
        "input[aria-label*='Search' i]",
        "[aria-label*='Search' i]",
        "[role='main'] main",
        ".feed-identity-module",
    ]
    auth_hits = await _any_visible(page, auth_selectors)
    if len(auth_hits) >= 2 or any(selector in auth_hits for selector in ("[data-test-global-nav]", ".global-nav", "input[placeholder*='Search' i]", "input[aria-label*='Search' i]", "[aria-label*='Me' i]", "[aria-label*='Profile' i]")):
        signals.extend(auth_hits)
        return LinkedInSessionValidation(
            state=LinkedInSessionState.AUTHENTICATED,
            reason="linkedin authenticated shell detected",
            url=url,
            title=title,
            signals=signals,
        )

    if any(token in normalized for token in ("expired", "signed out", "session expired", "session ended")):
        signals.append("session_expired_text")
        return LinkedInSessionValidation(
            state=LinkedInSessionState.SESSION_EXPIRED,
            reason="session expired signals detected",
            url=url,
            title=title,
            signals=signals,
        )

    return LinkedInSessionValidation(
        state=LinkedInSessionState.UNKNOWN,
        reason="no confident linkedin session signals matched",
        url=url,
        title=title,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Main worker class
# ---------------------------------------------------------------------------

class JobPostingWorker:
    """LinkedIn Job Posting worker.

    Flow:
        1. /jobs/ → click "Post a free job" sidebar link
        2. Enter job title → Continue
        3. Summary page → click Edit pencil
        4. Edit details to match spec
        5. Fill description (or leave AI-generated)
    """

    def __init__(
        self,
        account_id: str,
        spec: JobPostingSpec,
        *,
        config: BrowserContextConfig | None = None,
        lock_timeout: float = 60.0,
    ) -> None:
        if not isinstance(spec, JobPostingSpec):
            raise TypeError("spec must be a JobPostingSpec instance")
        self._account_id = account_id
        self._spec = spec
        self._execution_mode = JobPostingExecutionMode.normalize(spec.execution_mode)
        self._config = config or BrowserContextConfig()
        self._lock_timeout = lock_timeout
        self._browser_manager = BrowserManager(account_id=account_id, config=self._config)

    async def run(self) -> JobPostingResult:
        result = JobPostingResult(
            execution_mode=self._execution_mode,
            dry_run=self._execution_mode != JobPostingExecutionMode.LIVE,
            publish_state=PublishLifecycleState.PUBLISH_REQUESTED,
            publish_clicked=False,
            published=False,
            publish_confirmed=False,
        )
        started_ms = time.monotonic()
        self._run_ts = time.strftime("%Y%m%dT%H%M%S")

        if self._execution_mode == JobPostingExecutionMode.LIVE and not _is_live_publish_allowed():
            result.status = WorkerStatus.FAILED
            result.errors.append("live_publish_disabled_outside_production")
            logger.warning(
                "worker live_publish_blocked account_id=%s mode=%s environment=%s",
                self._account_id,
                self._execution_mode.value,
                is_production_environment(),
            )
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            return result

        missing = self._spec.missing_required()
        if missing:
            result.status = WorkerStatus.SPEC_INVALID
            result.errors.append(f"missing required spec fields: {missing}")
            logger.error("worker spec_invalid missing=%s", missing)
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            return result

        try:
            await _lock_manager.lock(
                self._account_id,
                timeout=self._lock_timeout,
                owner="JobPostingWorker",
            )
        except TimeoutError as exc:
            result.status = WorkerStatus.LOCK_TIMEOUT
            result.errors.append(str(exc))
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            return result

        page = None
        try:
            try:
                context = await self._browser_manager.get_browser()
                page = await context.new_page()
                if hasattr(page, "set_default_timeout"):
                    page.set_default_timeout(30_000)
                if hasattr(page, "set_default_navigation_timeout"):
                    page.set_default_navigation_timeout(15_000)
            except Exception as exc:
                result.status = WorkerStatus.BROWSER_ERROR
                result.errors.append(f"browser_start_failed: {exc}")
                result.duration_ms = int((time.monotonic() - started_ms) * 1000)
                return result

            # Navigate to Jobs homepage
            try:
                await page.goto(JOBS_HOMEPAGE_URL, wait_until="domcontentloaded", timeout=15_000)
                await page.wait_for_selector(
                    "#workspace, [role='main'], main",
                    state="visible",
                    timeout=10_000,
                )
            except Exception as exc:
                result.status = WorkerStatus.BROWSER_ERROR
                result.errors.append(f"navigation_failed: {exc}")
                result.duration_ms = int((time.monotonic() - started_ms) * 1000)
                return result

            session_validation = await _validate_linkedin_session(page)
            result.session_state = session_validation.state
            result.session_reason = session_validation.reason
            result.session_signals = list(session_validation.signals)
            if session_validation.state != LinkedInSessionState.AUTHENTICATED:
                result.status = {
                    LinkedInSessionState.LOGIN_REQUIRED: WorkerStatus.LOGIN_REQUIRED,
                    LinkedInSessionState.SESSION_EXPIRED: WorkerStatus.SESSION_EXPIRED,
                    LinkedInSessionState.CHECKPOINT: WorkerStatus.CHECKPOINT,
                    LinkedInSessionState.CAPTCHA: WorkerStatus.CAPTCHA,
                    LinkedInSessionState.UNKNOWN: WorkerStatus.UNKNOWN_SESSION,
                }.get(session_validation.state, WorkerStatus.UNKNOWN_SESSION)
                result.errors.append(
                    f"session_blocked state={session_validation.state.value} reason={session_validation.reason} signals={session_validation.signals}"
                )
                logger.warning(
                    "worker session_validation_blocked account_id=%s state=%s reason=%s signals=%s url=%s",
                    self._account_id,
                    session_validation.state.value,
                    session_validation.reason,
                    session_validation.signals,
                    session_validation.url,
                )
                result.duration_ms = int((time.monotonic() - started_ms) * 1000)
                return result

            await self._run_flow(page, result)

        except Exception as exc:
            logger.exception("worker unexpected_error account_id=%s", self._account_id)
            result.status = WorkerStatus.FAILED
            result.errors.append(str(exc))

        finally:
            result.duration_ms = int((time.monotonic() - started_ms) * 1000)
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            try:
                await self._browser_manager.stop()
            except Exception:
                logger.debug("worker browser_stop_failed", exc_info=True)
            await _lock_manager.unlock(self._account_id)

        return result

    # ── Real LinkedIn job posting flow ────────────────────────────────────────

    async def _run_flow(self, page: Any, result: JobPostingResult) -> None:
        """
        Phase 1 — Jobs nav tab (if not already on /jobs/)
        Phase 2 — Click 'Post a free job' in sidebar
        Phase 3 — Enter job title → Continue
        Phase 4 — Click Edit pencil on summary page → edit details
        Phase 5 — Fill description or leave AI-generated
        """
        spec = self._spec

        # ── Phase 1: ensure we're on the Jobs page ────────────────────────────
        diag0 = StepDiagnostic(step_index=0, step_label="jobs_nav", url=str(page.url))
        t0 = time.monotonic()
        current_url = str(page.url)
        if "/jobs" not in current_url:
            clicked = await self._click_first_visible(page, JOBS_NAV_SELECTORS)
            if clicked:
                await page.wait_for_selector(
                    "#workspace, [role='main'], main",
                    state="visible",
                    timeout=10_000,
                )
                diag0.navigation_succeeded = True
                diag0.fields_filled.append("jobs_nav_click")
                logger.info("worker phase=jobs_nav clicked")
            else:
                diag0.notes = "jobs_nav_click_failed"
                logger.warning("worker phase=jobs_nav click failed — continuing anyway")
        else:
            diag0.notes = "already_on_jobs_page"
            diag0.navigation_succeeded = True
        diag0.elapsed_ms = int((time.monotonic() - t0) * 1000)
        result.diagnostics.append(diag0)

        # ── Phase 2: click 'Post a free job' sidebar link ─────────────────────
        diag1 = StepDiagnostic(step_index=1, step_label="post_free_job", url=str(page.url))
        t1 = time.monotonic()
        post_clicked = await self._click_first_visible(page, POST_FREE_JOB_SELECTORS)
        if not post_clicked:
            result.status = WorkerStatus.PARTIAL
            result.errors.append("could not find 'Post a free job' sidebar link")
            diag1.notes = "post_free_job_not_found"
            diag1.elapsed_ms = int((time.monotonic() - t1) * 1000)
            await _capture_failure_artifacts(page, diag1, self._run_ts)
            result.diagnostics.append(diag1)
            return
        # Wait for the title input to appear — SPA needs time to mount
        try:
            await page.wait_for_selector(
                "input[type='text'], input[aria-label*='title' i], input[placeholder*='title' i]",
                state="visible",
                timeout=12_000,
            )
        except Exception:
            pass
        diag1.navigation_succeeded = True
        diag1.fields_filled.append("post_free_job_click")
        diag1.elapsed_ms = int((time.monotonic() - t1) * 1000)
        result.diagnostics.append(diag1)
        logger.info("worker phase=post_free_job clicked url=%s", page.url)

        # ── Phase 3: enter job title → Continue ───────────────────────────────
        diag2 = StepDiagnostic(step_index=2, step_label="title_entry", url=str(page.url))
        t2 = time.monotonic()
        title_ok = await self._enter_title_and_continue_validated(page, spec.title, diag2)
        diag2.elapsed_ms = int((time.monotonic() - t2) * 1000)
        result.diagnostics.append(diag2)
        if not title_ok:
            result.status = WorkerStatus.PARTIAL
            result.errors.append("title entry or Continue click failed")
            await _capture_failure_artifacts(page, diag2, self._run_ts)
            return
        await page.wait_for_selector(
            ", ".join(EDIT_PENCIL_SELECTORS),
            state="visible",
            timeout=10_000,
        )
        result.completed_steps.append("title_entry")
        logger.info("worker phase=title_entry done url=%s", page.url)

        # ── Phase 4: click Edit pencil → edit details ─────────────────────────
        diag3 = StepDiagnostic(step_index=3, step_label="edit_details", url=str(page.url))
        t3 = time.monotonic()
        edit_ok = await self._edit_job_details(page, spec, diag3)
        diag3.elapsed_ms = int((time.monotonic() - t3) * 1000)
        result.diagnostics.append(diag3)
        if edit_ok:
            result.completed_steps.append("edit_details")
        else:
            result.warnings.append("edit_details phase had issues — continuing")
        logger.info("worker phase=edit_details done fields_filled=%s", diag3.fields_filled)

        # ── Phase 5: description ──────────────────────────────────────────────
        diag4 = StepDiagnostic(step_index=4, step_label="description", url=str(page.url))
        t4 = time.monotonic()
        await self._handle_description(page, spec, diag4)
        diag4.elapsed_ms = int((time.monotonic() - t4) * 1000)
        result.diagnostics.append(diag4)
        result.completed_steps.append("description")
        logger.info("worker phase=description done fields_filled=%s", diag4.fields_filled)

        result.review_reached = True
        result.status = WorkerStatus.OK
        result.current_step = 4
        result.current_step_label = "summary"
        diag5 = StepDiagnostic(step_index=5, step_label="manage_applicants", url=str(page.url))
        t5 = time.monotonic()
        manage_ok = await self._handle_manage_applicants(page, spec, diag5)
        diag5.elapsed_ms = int((time.monotonic() - t5) * 1000)
        result.diagnostics.append(diag5)
        if manage_ok:
            result.completed_steps.append("manage_applicants")
            result.current_step = 5
            result.current_step_label = "manage_applicants"
            logger.info("worker phase=manage_applicants completed")
            continue_clicked = False
            for sel in CONTINUE_BUTTON_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if not await _is_visible(loc):
                        continue
                    label = (await _safe_text(loc)).lower()
                    if any(t in label for t in ("post", "publish", "submit")):
                        continue
                    await loc.click(timeout=5000)
                    continue_clicked = True
                    logger.info("continue_after_manage_clicked sel=%r", sel)
                    break
                except Exception:
                    continue
            if not continue_clicked:
                raise RuntimeError("continue button not found after manage applicants save")
            await page.wait_for_load_state("networkidle")
        else:
            logger.warning("worker phase=manage_applicants returned false; stopping before continue")
            return
        # Phase 7: final publish step
        diag6 = StepDiagnostic(step_index=6, step_label="post_job_detected", url=str(page.url))
        t6 = time.monotonic()
        post_loc, discovery = await self._discover_publish_button(page)
        diag6.verification_passed = post_loc is not None
        if post_loc is None:
            diag6.notes = json.dumps(discovery, sort_keys=True, default=str)
            diag6.elapsed_ms = int((time.monotonic() - t6) * 1000)
            result.diagnostics.append(diag6)
            result.status = WorkerStatus.PARTIAL
            result.errors.append(f"publish_button_discovery_failed:{discovery.get('reason', 'unknown')}")
            return
        if self._execution_mode == JobPostingExecutionMode.DRY_RUN:
            diag6.notes = json.dumps(discovery, sort_keys=True, default=str)
            logger.info("worker publish_button_detected selector=%r -- stopping (dry_run)", discovery.get("selector"))
            result.dry_run = True
            result.publish_state = PublishLifecycleState.PUBLISH_REQUESTED
            result.publish_clicked = False
            result.published = False
            result.publish_confirmed = False
            result.status = WorkerStatus.OK
            result.review_reached = True
            result.current_step = 6
            result.current_step_label = "post_job_detected"
            result.completed_steps.append("post_job_detected")
            diag6.elapsed_ms = int((time.monotonic() - t6) * 1000)
            result.diagnostics.append(diag6)
            return

        diag6.notes = json.dumps(discovery, sort_keys=True, default=str)
        try:
            await post_loc.click(timeout=5000)
            result.publish_clicked = True
            result.publish_state = PublishLifecycleState.PUBLISH_CLICKED
            result.dry_run = False
            result.completed_steps.append("publish_clicked")
            result.current_step_label = "waiting_for_confirmation"
            result.completed_steps.append("waiting_for_confirmation")
            result.publish_state = PublishLifecycleState.WAITING_FOR_CONFIRMATION
            await page.wait_for_load_state("networkidle")
            detector = SuccessDetector()
            confirmed, signal = await detector.detect(page.locator("body"), timeout_ms=12000, page=page)
            diag6.verification_passed = confirmed
            diag6.notes = f"publish_clicked confirmation={signal}"
            result.publish_confirmed = confirmed
            if confirmed:
                result.published = True
                result.publish_state = PublishLifecycleState.PUBLISH_CONFIRMED
                result.status = WorkerStatus.OK
                result.review_reached = True
                result.current_step = 6
                result.current_step_label = "post_job_published"
                result.completed_steps.append("post_job_published")
                logger.info("worker publish_confirmed signal=%s selector=%r", signal, discovery.get("selector"))
            else:
                result.published = False
                result.publish_state = PublishLifecycleState.PUBLISH_FAILED
                result.status = WorkerStatus.FAILED
                result.errors.append("publish_confirmation_not_detected")
                result.completed_steps.append("publish_failed")
                result.current_step_label = "post_job_publish_failed"
                logger.warning("worker publish_confirmation_missing selector=%r", discovery.get("selector"))
        except Exception as exc:
            result.published = False
            result.publish_confirmed = False
            result.publish_state = PublishLifecycleState.PUBLISH_FAILED
            result.completed_steps.append("publish_failed")
            result.status = WorkerStatus.FAILED
            result.errors.append(f"publish_failed: {exc}")
            logger.warning("worker publish_failed selector=%r error=%s", discovery.get("selector"), exc, exc_info=exc)
        diag6.elapsed_ms = int((time.monotonic() - t6) * 1000)
        result.diagnostics.append(diag6)
        result.current_step = 6
        if not result.publish_confirmed:
            result.current_step_label = "post_job_detected"
    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _click_first_visible(self, page: Any, selectors: list[str]) -> bool:
        """Click the first visible element matching any selector."""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if not await _is_visible(loc):
                    continue
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000)
                return True
            except Exception:
                continue
        return False

    async def _discover_publish_button(self, page: Any) -> tuple[Any | None, dict[str, Any]]:
        url = str(getattr(page, "url", "") or "")
        url_lower = url.lower()
        summary: dict[str, Any] = {
            "reason": "",
            "url": url,
            "category": "",
            "selector": "",
            "matched_count": 0,
            "visible_count": 0,
            "enabled": False,
            "interactable": False,
            "attempts": [],
        }

        async def _evaluate_candidate(kind: str, selector_or_label: str, loc: Any) -> tuple[bool, dict[str, Any]]:
            details: dict[str, Any] = {
                "kind": kind,
                "selector": selector_or_label,
                "visible": False,
                "enabled": False,
                "interactable": False,
                "error": "",
            }
            try:
                details["visible"] = await _is_visible(loc)
            except Exception as exc:
                details["error"] = str(exc)
                return False, details
            if not details["visible"]:
                return False, details
            try:
                details["enabled"] = bool(await loc.is_enabled())
            except Exception as exc:
                details["error"] = str(exc)
                return False, details
            if not details["enabled"]:
                return False, details
            try:
                await loc.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                await loc.click(timeout=2000, trial=True)
                details["interactable"] = True
                return True, details
            except Exception as exc:
                err = str(exc)
                details["error"] = err
                return False, details

        async def _check_group(kind: str, entries: list[tuple[str, Any]]) -> tuple[Any | None, dict[str, Any]]:
            for selector_or_label, locator_factory in entries:
                candidate = locator_factory()
                try:
                    count = await candidate.count()
                except Exception as exc:
                    summary["attempts"].append(
                        {
                            "kind": kind,
                            "selector": selector_or_label,
                            "error": str(exc),
                        }
                    )
                    continue
                visible_indexes: list[int] = []
                for idx in range(count):
                    try:
                        item = candidate.nth(idx)
                        if await _is_visible(item):
                            visible_indexes.append(idx)
                    except Exception:
                        continue
                summary["attempts"].append(
                    {
                        "kind": kind,
                        "selector": selector_or_label,
                        "matched_count": count,
                        "visible_count": len(visible_indexes),
                    }
                )
                if len(visible_indexes) > 1:
                    return None, {
                        **summary,
                        "reason": "multiple_matching_buttons",
                        "category": kind,
                        "selector": selector_or_label,
                        "matched_count": count,
                        "visible_count": len(visible_indexes),
                    }
                if len(visible_indexes) == 1:
                    item = candidate.nth(visible_indexes[0])
                    verified, details = await _evaluate_candidate(kind, selector_or_label, item)
                    if verified:
                        details["page_url"] = url
                        return item, {
                            **summary,
                            "reason": "verified",
                            "category": kind,
                            "selector": selector_or_label,
                            "matched_count": count,
                            "visible_count": 1,
                            "enabled": True,
                            "interactable": True,
                            "details": details,
                        }
                    reason = "disabled_publish_button" if not details.get("enabled") else "hidden_publish_button"
                    if details.get("enabled") and "pointer" in str(details.get("error") or "").lower():
                        reason = "covered_publish_button"
                    return None, {
                        **summary,
                        "reason": reason,
                        "category": kind,
                        "selector": selector_or_label,
                        "matched_count": count,
                        "visible_count": 1,
                        "enabled": bool(details.get("enabled")),
                        "interactable": bool(details.get("interactable")),
                        "error": details.get("error", ""),
                    }
            return None, summary

        role_entries = [
            ("Post job", lambda: page.get_by_role("button", name="Post job")),
            ("Publish", lambda: page.get_by_role("button", name="Publish")),
        ]
        locator, details = await _check_group("role", role_entries)
        if locator is not None:
            return locator, details
        if details.get("reason") in {"multiple_matching_buttons", "disabled_publish_button", "covered_publish_button"}:
            return None, details

        data_selectors = [sel for sel in PUBLISH_BUTTON_SELECTORS if "data-control-name" in sel]
        aria_selectors = [sel for sel in PUBLISH_BUTTON_SELECTORS if "aria-label" in sel]
        text_selectors = [sel for sel in PUBLISH_BUTTON_SELECTORS if "has-text('Post job')" in sel or "has-text('Publish')" in sel]
        fallback_selectors = [sel for sel in PUBLISH_BUTTON_SELECTORS if sel not in data_selectors and sel not in aria_selectors and sel not in text_selectors]

        selector_groups = [
            ("data_attribute", data_selectors),
            ("aria_label", aria_selectors),
            ("button_text", text_selectors),
            ("fallback", fallback_selectors),
        ]

        for kind, selectors in selector_groups:
            if not selectors:
                continue
            locator, details = await _check_group(kind, [(sel, lambda sel=sel: page.locator(sel)) for sel in selectors])
            if locator is not None:
                return locator, details
            if details.get("reason") in {"multiple_matching_buttons", "disabled_publish_button", "covered_publish_button"}:
                return None, details

        attempts = list(summary.get("attempts") or [])
        matched_attempts = [item for item in attempts if int(item.get("matched_count") or 0) > 0]
        visible_attempts = [item for item in attempts if int(item.get("visible_count") or 0) > 0]
        if matched_attempts and not visible_attempts:
            reason = "hidden_publish_button"
        else:
            reason = "wrong_page" if not any(token in url_lower for token in ("/job-posting/", "/jobs/", "/talent/")) else "no_publish_button_found"
        summary.update(
            {
                "reason": reason,
                "category": "search",
                "selector": "",
                "matched_count": sum(int(item.get("matched_count") or 0) for item in attempts if isinstance(item, dict)),
                "visible_count": sum(int(item.get("visible_count") or 0) for item in attempts if isinstance(item, dict)),
                "page_context_match": any(token in url_lower for token in ("/job-posting/", "/jobs/", "/talent/")),
            }
        )
        return None, summary

    async def _resolve_validation_owner(self, page: Any, message: str) -> str:
        normalized = _validation_key(message)
        if normalized:
            return normalized
        try:
            loc = page.get_by_text(message, exact=False).first
            if await _is_visible(loc):
                for attr in ("aria-describedby", "aria-errormessage", "aria-invalid"):
                    try:
                        val = await loc.get_attribute(attr)
                        if val:
                            return _validation_key(str(val))
                    except Exception:
                        continue
        except Exception:
            pass
        return ""

    async def _collect_validation_messages(self, page: Any) -> list[str]:
        messages = await _collect_validation_errors(page)
        logger.debug("worker validation_found: %s", messages)
        return messages

    async def _repair_validation_messages(
        self,
        page: Any,
        messages: list[str],
        spec: JobPostingSpec,
        field_status: dict[str, bool] | None = None,
    ) -> dict[str, bool]:
        repaired: dict[str, bool] = {}
        for message in messages:
            field = await self._resolve_validation_owner(page, message)
            logger.debug("worker validation_message=%r resolved_field=%r", message, field)
            if field_status is not None and field in field_status and field_status.get(field):
                logger.debug("worker validation_skip_verified field=%r", field)
                continue
            if field == "location":
                repaired[field] = await self._repair_location_field(page, spec.location)
            elif field == "company":
                repaired[field] = await self._repair_company_field(page, spec.company)
            elif field == "workplace_type":
                repaired[field] = await self._repair_workplace_type_field(page, spec.workplace_type)
            elif field == "job_type":
                repaired[field] = await self._repair_job_type_field(page, spec.job_type)
            elif field == "title":
                repaired[field] = await self._repair_title_field(page, spec.title)
        return repaired

    async def _repair_title_field(self, page: Any, value: str) -> bool:
        return await self._fill_text_control(page, "Job title", TITLE_INPUT_SELECTORS, value, typeahead=False, exact_text=True)

    async def _repair_company_field(self, page: Any, value: str) -> bool:
        return await self._fill_text_control(page, "Company", ["[aria-label*='Company' i], input[id*='company' i]"], value, typeahead=True, exact_text=True)

    async def _repair_workplace_type_field(self, page: Any, value: str) -> bool:
        return await self._fill_dropdown_control(page, "Workplace type", "[aria-label*='Workplace type' i]", value)

    async def _repair_job_type_field(self, page: Any, value: str) -> bool:
        return await self._fill_dropdown_control(page, "Job type", "[aria-label*='Job type' i]", value)

    async def _repair_location_field(self, page: Any, value: str) -> bool:
        return await self._probe_employee_location_autocomplete(page, value)

    async def _probe_employee_location_autocomplete(self, page: Any, value: str) -> bool:
        container = await _find_form_container(page)
        if container is None:
            logger.warning("worker location_probe_no_container")
            return False

        selector_candidates = [
            "[aria-label*='Employee location' i]",
            "[aria-label*='Job location' i]",
            "input[placeholder*='Country or state' i]",
            "input[placeholder*='location' i]",
            "input[role='combobox']",
            "input[role='textbox']",
            "[role='combobox']",
            "[role='textbox']",
            "[contenteditable='true']",
        ]

        loc = None
        matched_selector = ""
        for sel in selector_candidates:
            try:
                candidate = container.locator(sel).first
                if not await _is_visible(candidate):
                    continue
                role = str(await candidate.get_attribute("role") or "")
                tag = str(await candidate.evaluate("(el) => el.tagName.toLowerCase()") or "")
                aria_label = str(await candidate.get_attribute("aria-label") or "")
                placeholder = str(await candidate.get_attribute("placeholder") or "")
                if tag == "input" or role in {"combobox", "textbox"} or "contenteditable" in str(await candidate.get_attribute("contenteditable") or "").lower():
                    loc = candidate
                    matched_selector = sel
                    logger.debug(
                        "worker location_control tag=%s role=%s id=%s name=%s placeholder=%s aria_label=%s outerHTML=%s bbox=%s",
                        tag,
                        role,
                        await candidate.get_attribute("id"),
                        await candidate.get_attribute("name"),
                        placeholder,
                        aria_label,
                        await candidate.evaluate("(el) => el.outerHTML"),
                        await candidate.bounding_box(),
                    )
                    break
            except Exception:
                continue

        if loc is None:
            logger.warning("worker location_probe_no_editable_control")
            return False

        try:
            before_value = ""
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    before_value = str(await getattr(loc, method)(timeout=1000) or "")
                    if before_value.strip():
                        break
                except Exception:
                    continue
            logger.debug("worker location_before_typing value=%r selector=%r", before_value, matched_selector)

            await loc.click(timeout=3000)
            try:
                await loc.press("Control+a")
                await loc.press("Delete")
            except Exception:
                pass
            try:
                await loc.fill("")
            except Exception:
                pass

            typed = ""
            for ch in value:
                await loc.type(ch, delay=70)
                typed += ch
            logger.debug("worker location_typed value=%r", typed)

            try:
                await page.wait_for_selector(
                    "[role='listbox'], [aria-label*='suggest' i], [role='option'], div[role='listbox']",
                    state="visible",
                    timeout=2000,
                )
            except Exception:
                pass

            popup = None
            popup_count = 0
            for popup_sel in ("[role='listbox']", "[aria-label*='suggest' i]", "[role='option']", "ul", "div[role='listbox']"):
                try:
                    locs = page.locator(popup_sel)
                    count = await locs.count()
                    visible_count = 0
                    for i in range(min(count, 10)):
                        item = locs.nth(i)
                        if await _is_visible(item):
                            visible_count += 1
                    if visible_count:
                        popup = popup_sel
                        popup_count = visible_count
                        break
                except Exception:
                    continue
            logger.debug("worker location_suggestions_found=%d popup=%r", popup_count, popup)

            suggestion_texts: list[str] = []
            if popup_count:
                for sel in ("[role='option']", "[role='option'] span", "li", "div"):
                    try:
                        opts = page.locator(sel)
                        count = await opts.count()
                        for i in range(min(count, 20)):
                            opt = opts.nth(i)
                            if not await _is_visible(opt):
                                continue
                            text = (await _safe_text(opt)).strip()
                            if text and text not in suggestion_texts:
                                suggestion_texts.append(text)
                    except Exception:
                        continue
            logger.debug("worker location_suggestions_texts=%s", suggestion_texts)

            selected_index = -1
            try:
                await loc.press("ArrowDown")
                selected_index = 0
                await loc.press("Enter")
            except Exception as exc:
                logger.warning("worker location_keyboard_select_failed err=%s", exc)

            after_value = ""
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    after_value = str(await getattr(loc, method)(timeout=1000) or "")
                    if after_value.strip():
                        break
                except Exception:
                    continue

            aria_active = ""
            try:
                aria_active = str(await loc.get_attribute("aria-activedescendant") or "")
            except Exception:
                pass
            logger.debug(
                "worker location_after_enter value=%r aria_activedescendant=%r selected_index=%d",
                after_value,
                aria_active,
                selected_index,
            )

            ok = False
            normalized_value = _normalize_text(value)
            if normalized_value and normalized_value in _normalize_text(after_value):
                ok = True
            if not ok and not aria_active:
                ok = True if popup_count else False

            if not ok:
                try:
                    logger.debug("worker location_retry_once selector=%r", matched_selector)
                    await loc.click(timeout=3000)
                    await loc.press("Control+a")
                    await loc.press("Delete")
                    for ch in value:
                        await loc.type(ch, delay=70)
                    await page.wait_for_selector(
                        "[role='listbox'], [aria-label*='suggest' i], [role='option'], div[role='listbox']",
                        state="visible",
                        timeout=2000,
                    )
                    await loc.press("ArrowDown")
                    await loc.press("Enter")
                    retry_value = ""
                    for method in ("input_value", "text_content", "inner_text"):
                        try:
                            retry_value = str(await getattr(loc, method)(timeout=1000) or "")
                            if retry_value.strip():
                                break
                        except Exception:
                            continue
                    logger.debug("worker location_retry_value=%r", retry_value)
                    ok = normalized_value in _normalize_text(retry_value)
                except Exception as exc:
                    logger.warning("worker location_retry_failed err=%s", exc)

            if not ok:
                try:
                    out = _ARTIFACT_ROOT / self._run_ts
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "location_input.html").write_text(str(await loc.evaluate("(el) => el.outerHTML")), encoding="utf-8")
                    popup_html = ""
                    try:
                        popup_html = str(await page.locator("[role='listbox']").first.evaluate("(el) => el.outerHTML") or "")
                    except Exception:
                        pass
                    (out / "location_popup.html").write_text(popup_html, encoding="utf-8")
                    logger.debug("worker location_debug_artifacts_saved dir=%s", out)
                except Exception as exc:
                    logger.warning("worker location_debug_artifacts_failed err=%s", exc)

            logger.debug("worker location_probe_result ok=%s selector=%r", ok, matched_selector)
            return ok
        except Exception as exc:
            logger.warning("worker location_probe_failed err=%s selector=%r", exc, matched_selector)
            return False

    async def _fill_text_control(
        self,
        page: Any,
        label: str,
        selectors: list[str],
        value: str,
        *,
        typeahead: bool = False,
        exact_text: bool = False,
    ) -> bool:
        container = await _find_form_container(page)
        if container is None:
            return False
        loc = None
        for sel in selectors:
            try:
                candidate = page.get_by_label(label, exact=False).first
                if await _is_visible(candidate):
                    loc = candidate
                    break
            except Exception:
                pass
            try:
                candidate = container.locator(sel).first
                if await _is_visible(candidate):
                    loc = candidate
                    break
            except Exception:
                continue
        if loc is None:
            return False
        try:
            await loc.click(timeout=3000)
            for _ in range(3):
                try:
                    await loc.press("Control+a")
                    await loc.press("Backspace")
                except Exception:
                    pass
                try:
                    await loc.fill("")
                except Exception:
                    pass
            await loc.type(value, delay=50)
            if typeahead:
                try:
                    await page.wait_for_selector(
                        "[role='option'], li[role='option'], .typeahead-result",
                        state="visible",
                        timeout=2000,
                    )
                except Exception:
                    pass
                for opt_sel in (
                    f"[role='option'][aria-selected='true']:has-text('{value}')",
                    f"[role='option']:has-text('{value}')",
                    "li[role='option']",
                    "[role='option']",
                ):
                    try:
                        opt = page.locator(opt_sel).first
                        if await _is_visible(opt):
                            await opt.click(timeout=3000)
                            break
                    except Exception:
                        continue
            current = ""
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    current = str(await getattr(loc, method)(timeout=1000) or "")
                    if current.strip():
                        break
                except Exception:
                    continue
            ok = _normalize_text(value) in _normalize_text(current) if exact_text else bool(current)
            logger.debug("worker verification field=%r result=%s current=%r", label, ok, current)
            return ok
        except Exception as exc:
            logger.warning("worker field_repair_failed field=%r err=%s", label, exc)
            return False

    async def _fill_dropdown_control(self, page: Any, label: str, selector: str, value: str) -> bool:
        container = await _find_form_container(page)
        if container is None:
            return False
        try:
            loc = page.get_by_label(label, exact=False).first
            if not await _is_visible(loc):
                loc = container.locator(selector).first
            if not await _is_visible(loc):
                return False
            from app.linkedin.playwright.dropdown_engine import DropdownEngine
            engine = DropdownEngine(loc, container=container, timeout_ms=8000)
            await engine.select(value)
            current = ""
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    current = str(await getattr(loc, method)(timeout=1000) or "")
                    if current.strip():
                        break
                except Exception:
                    continue
            ok = _normalize_text(value) in _normalize_text(current) or not current
            logger.debug("worker verification field=%r result=%s current=%r", label, ok, current)
            return ok
        except Exception as exc:
            logger.warning("worker dropdown_repair_failed field=%r err=%s", label, exc)
            return False

    async def _click_continue_with_validation(self, page: Any, diag: StepDiagnostic, *, phase: str) -> list[str] | None:
        for sel in CONTINUE_BUTTON_SELECTORS:
            try:
                loc = page.locator(sel).first
                if not await _is_visible(loc):
                    continue
                label = (await _safe_text(loc)).lower()
                if any(t in label for t in ("post", "publish", "submit")):
                    continue
                await loc.click(timeout=5000)
                diag.navigation_succeeded = True
                logger.info("worker continue_clicked phase=%s sel=%r", phase, sel)
                return await self._collect_validation_messages(page)
            except Exception:
                continue
        logger.warning("worker continue_button_not_found phase=%s", phase)
        return None

    async def _find_dialog_footer_primary_button(self, dialog: Any) -> Any | None:
        footer = None
        for sel in ("footer", "[role='contentinfo']", "[data-test*='footer' i]", ".artdeco-modal__footer", ".modal-footer"):
            try:
                loc = dialog.locator(sel).first
                if await _is_visible(loc):
                    footer = loc
                    break
            except Exception:
                continue
        footer = footer or dialog

        preferred = (
            "button:has-text('Save')",
            "button:has-text('Done')",
            "button:has-text('Continue')",
            "button:has-text('Update')",
            "button:has-text('Apply')",
            "button[type=submit]",
        )
        for sel in preferred:
            try:
                loc = footer.locator(sel).first
                if await _is_visible(loc):
                    return loc
            except Exception:
                continue

        try:
            buttons = footer.locator("button")
            count = await buttons.count()
            enabled: list[Any] = []
            for i in range(count):
                loc = buttons.nth(i)
                try:
                    if await _is_visible(loc) and not await loc.is_disabled():
                        text = _normalize_text(await _safe_text(loc))
                        if text and text not in {"cancel", "back"}:
                            enabled.append(loc)
                except Exception:
                    continue
            if enabled:
                return enabled[0]
        except Exception:
            pass
        return None

    async def _find_manage_applicants_section(self, page: Any) -> Any | None:
        try:
            row = page.get_by_text("Manage applicants", exact=True).first
            if await _is_visible(row):
                section = row.locator(
                    "xpath=ancestor::article[1] | ancestor::section[1] | ancestor::div[1]"
                ).first
                for _ in range(4):
                    try:
                        if not await _is_visible(section):
                            break
                        section_text = _normalize_text(await _safe_text(section))
                        section_html = _normalize_text(str(await section.evaluate("(el) => el.outerHTML") or ""))
                        if "manage applicants" in section_text or "manage applicants" in section_html:
                            return section
                        section = section.locator("xpath=parent::*").first
                    except Exception:
                        break
        except Exception:
            pass
        return None

    async def _dump_manage_applicants_diagnostics(self, section: Any, out_name: str = "manage_applicants_container.html") -> None:
        try:
            out = _ARTIFACT_ROOT / self._run_ts
            out.mkdir(parents=True, exist_ok=True)
            html = str(await section.evaluate("(el) => el.outerHTML") or "")
            (out / out_name).write_text(html, encoding="utf-8")
            try:
                await section.screenshot(path=str(out / out_name.replace(".html", ".png")), full_page=False)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("worker manage_applicants_diag_dump_failed err=%s", exc)

    async def _write_json_artifact(self, filename: str, payload: Any) -> Path:
        out = _ARTIFACT_ROOT / self._run_ts
        out.mkdir(parents=True, exist_ok=True)
        path = out / filename
        try:
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.error("worker artifact_write_failed file=%s err=%s", filename, exc)
            raise RuntimeError(f"failed to write diagnostic artifact {filename}: {exc}") from exc
        if not path.exists():
            raise RuntimeError(f"failed to create diagnostic artifact {filename}")
        logger.info("Saved %s path=%s", filename, path)
        return path

    async def _inspect_review_page(self, page: Any, diag: StepDiagnostic) -> None:
        out = _ARTIFACT_ROOT / self._run_ts
        out.mkdir(parents=True, exist_ok=True)
        try:
            html = await page.content()
            html_path = out / "review_page.html"
            html_path.write_text(html, encoding="utf-8")
            if not html_path.exists():
                raise RuntimeError("failed to create review_page.html")
            logger.info("Saved review_page.html path=%s", html_path)
        except Exception as exc:
            logger.error("worker review_page_html_failed err=%s", exc)
            raise
        try:
            screenshot_path = out / "review_page.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            if not screenshot_path.exists():
                raise RuntimeError("failed to create review_page.png")
            logger.info("Saved review_page.png path=%s", screenshot_path)
        except Exception as exc:
            logger.error("worker review_page_screenshot_failed err=%s", exc)
            raise

        buttons: list[dict[str, Any]] = []
        try:
            locs = page.locator("button")
            count = await locs.count()
            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not await _is_visible(loc):
                        continue
                    buttons.append(
                        {
                            "index": i,
                            "tag": "button",
                            "text": await _safe_text(loc),
                            "aria_label": str(await loc.get_attribute("aria-label") or ""),
                            "title": str(await loc.get_attribute("title") or ""),
                            "role": str(await loc.get_attribute("role") or ""),
                            "class": str(await loc.get_attribute("class") or ""),
                            "outerHTML": str(await loc.evaluate("(el) => el.outerHTML").strip()[:500] if False else ""),
                            "bbox": await loc.bounding_box(),
                            "visible": await loc.is_visible(),
                            "enabled": await loc.is_enabled(),
                        }
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("worker review_buttons_failed err=%s", exc)
        # Trim outerHTML separately to avoid expression issues above.
        for item in buttons:
            try:
                idx = item["index"]
                loc = page.locator("button").nth(idx)
                item["outerHTML"] = (await loc.evaluate("(el) => el.outerHTML"))[:500]
            except Exception:
                item["outerHTML"] = ""
        await self._write_json_artifact("review_buttons.json", buttons)

        icons: list[dict[str, Any]] = []
        try:
            locs = page.locator("svg, li-icon, [role='button']")
            count = await locs.count()
            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not await _is_visible(loc):
                        continue
                    icons.append(
                        {
                            "index": i,
                            "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                            "text": await _safe_text(loc),
                            "aria_label": str(await loc.get_attribute("aria-label") or ""),
                            "title": str(await loc.get_attribute("title") or ""),
                            "role": str(await loc.get_attribute("role") or ""),
                            "class": str(await loc.get_attribute("class") or ""),
                            "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                            "bbox": await loc.bounding_box(),
                            "visible": await loc.is_visible(),
                            "enabled": await loc.is_enabled(),
                        }
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("worker review_icons_failed err=%s", exc)
        await self._write_json_artifact("review_icons.json", icons)

        headings: list[dict[str, Any]] = []
        try:
            locs = page.locator("h1, h2, h3, [role='heading']")
            count = await locs.count()
            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not await _is_visible(loc):
                        continue
                    headings.append(
                        {
                            "index": i,
                            "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                            "text": await _safe_text(loc),
                            "aria_label": str(await loc.get_attribute("aria-label") or ""),
                            "title": str(await loc.get_attribute("title") or ""),
                            "role": str(await loc.get_attribute("role") or ""),
                            "class": str(await loc.get_attribute("class") or ""),
                            "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                            "bbox": await loc.bounding_box(),
                        }
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("worker review_headings_failed err=%s", exc)
        await self._write_json_artifact("review_headings.json", headings)

        manage_occurrences: list[dict[str, Any]] = []
        try:
            locs = page.get_by_text("Manage applicants", exact=True)
            count = await locs.count()
            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not await _is_visible(loc):
                        continue
                    parent_html = ""
                    ancestor_html = ""
                    sibling_html = ""
                    try:
                        parent = loc.locator("xpath=parent::*").first
                        parent_html = (await parent.evaluate("(el) => el.outerHTML"))[:1500]
                    except Exception:
                        pass
                    try:
                        ancestor = loc.locator("xpath=ancestor::*[1]").first
                        ancestor_html = (await ancestor.evaluate("(el) => el.outerHTML"))[:1500]
                    except Exception:
                        pass
                    try:
                        sibling = loc.locator("xpath=following-sibling::*[1]").first
                        sibling_html = (await sibling.evaluate("(el) => el.outerHTML"))[:1500]
                    except Exception:
                        pass
                    manage_occurrences.append(
                        {
                            "index": i,
                            "bbox": await loc.bounding_box(),
                            "parent_html": parent_html,
                            "nearest_ancestor_html": ancestor_html,
                            "sibling_html": sibling_html,
                        }
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("worker manage_applicants_occurrences_failed err=%s", exc)
        await self._write_json_artifact("manage_applicants_dom.json", manage_occurrences)

    async def _collect_clickable_descendants(self, section: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        selectors = ["button", "[role='button']", "svg", "li-icon", "a", "[tabindex]"]
        for sel in selectors:
            try:
                locs = section.locator(sel)
                count = await locs.count()
                for i in range(min(count, 40)):
                    loc = locs.nth(i)
                    try:
                        if not await _is_visible(loc):
                            continue
                        box = await loc.bounding_box()
                        items.append(
                            {
                                "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                                "aria_label": str(await loc.get_attribute("aria-label") or ""),
                                "title": str(await loc.get_attribute("title") or ""),
                                "text": await _safe_text(loc),
                                "class": str(await loc.get_attribute("class") or ""),
                                "bbox": box,
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                continue
        return items

    async def _score_manage_applicants_edit_candidates(self, section: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for sel in ("button", "[role='button']", "svg", "li-icon", "a", "[tabindex]"):
            try:
                locs = section.locator(sel)
                count = await locs.count()
                for i in range(min(count, 40)):
                    loc = locs.nth(i)
                    try:
                        if not await _is_visible(loc):
                            continue
                        tag = str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or "")
                        aria_label = str(await loc.get_attribute("aria-label") or "")
                        title = str(await loc.get_attribute("title") or "")
                        cls = str(await loc.get_attribute("class") or "")
                        text = await _safe_text(loc)
                        box = await loc.bounding_box()
                        score = 0
                        if "edit" in _normalize_text(aria_label):
                            score += 100
                        if "edit" in _normalize_text(title):
                            score += 90
                        if "pencil" in _normalize_text(cls) or "pencil" in _normalize_text(aria_label) or "pencil" in _normalize_text(title):
                            score += 80
                        if tag == "button":
                            score += 20
                        if tag in {"svg", "li-icon"}:
                            score += 10
                        if box:
                            score += int(max(0, 1000 - box["x"]))
                        candidates.append(
                            {
                                "loc": loc,
                                "score": score,
                                "tag": tag,
                                "aria_label": aria_label,
                                "title": title,
                                "text": text,
                                "class": cls,
                                "bbox": box,
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                continue
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    async def _handle_manage_applicants(self, page: Any, spec: JobPostingSpec, diag: StepDiagnostic) -> bool:
        logger.info("worker manage_applicants_diagnostic_start")
        markers = ("Manage applicants", "Website address", "On an external website", "Receive applicants")

        async def _container_html_snapshot(container: Any) -> str:
            try:
                return str(await container.evaluate("(el) => el.outerHTML.slice(0,2000)") or "")
            except Exception:
                return ""

        async def _visible_edit_candidates(container: Any) -> list[Any]:
            candidates: list[Any] = []
            button_count = 0
            try:
                button_count = await container.locator("button").count()
            except Exception as exc:
                logger.warning("worker manage_applicants_container_button_count_failed err=%s", exc)
                raise
            logger.info("worker manage_applicants_container_button_count=%d", button_count)
            for i in range(button_count):
                loc = container.locator("button").nth(i)
                try:
                    if not await _is_visible(loc):
                        continue
                    aria = str(await loc.get_attribute("aria-label") or "")
                    title = str(await loc.get_attribute("title") or "")
                    text = await _safe_text(loc)
                    cls = str(await loc.get_attribute("class") or "")
                    box = await loc.bounding_box()
                    logger.info(
                        "worker manage_applicants_container_button index=%d text=%r aria_label=%r title=%r role=%r class=%r bbox=%s",
                        i,
                        text,
                        aria,
                        title,
                        await loc.get_attribute("role"),
                        cls,
                        box,
                    )
                    descriptor = " ".join([aria, title, text, cls]).lower()
                    if any(word in descriptor for word in ("edit", "pencil")) or (box and box.get("width", 0) < 80 and box.get("height", 0) < 80):
                        candidates.append(loc)
                except Exception:
                    continue
            return candidates

        async def _page_contains_markers(container: Any) -> str:
            try:
                content = await container.evaluate("(el) => el.outerHTML")
            except Exception:
                content = ""
            lowered = content.lower()
            for marker in markers:
                if marker.lower() in lowered:
                    return marker
            return ""

        async def _close_any_open_dialog() -> None:
            for sel in ("button[aria-label*='close' i]", "button:has-text('Cancel')", "[role='button']:has-text('Cancel')"):
                try:
                    loc = page.locator(sel).first
                    if await _is_visible(loc):
                        await loc.click(timeout=2000)
                        try:
                            await page.locator("[role='dialog'], dialog, [aria-modal='true']").first.wait_for(
                                state="hidden",
                                timeout=2000,
                            )
                        except Exception:
                            pass
                        return
                except Exception:
                    continue
            try:
                await page.keyboard.press("Escape")
                try:
                    await page.locator("[role='dialog'], dialog, [aria-modal='true']").first.wait_for(
                        state="hidden",
                        timeout=2000,
                    )
                except Exception:
                    pass
            except Exception:
                pass

        async def _interactive_count(container: Any) -> int:
            count = 0
            for sel in ("button", "[role='button']", "a", "svg"):
                try:
                    count += await container.locator(sel).count()
                except Exception:
                    continue
            return count

        async def _dump_timeout_artifacts(container: Any) -> None:
            out = _ARTIFACT_ROOT / self._run_ts
            out.mkdir(parents=True, exist_ok=True)
            try:
                html = await container.evaluate("(el) => el.outerHTML")
            except Exception:
                html = ""
            try:
                inner_text = await container.inner_text()
            except Exception:
                inner_text = ""
            descendants: list[dict[str, Any]] = []
            for sel in ("*", "button", "[role='button']"):
                try:
                    locs = container.locator(sel)
                    count = await locs.count()
                    for i in range(count):
                        loc = locs.nth(i)
                        try:
                            if not await _is_visible(loc):
                                continue
                            descendants.append(
                                {
                                    "selector": sel,
                                    "index": i,
                                    "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                                    "text": await _safe_text(loc),
                                    "aria_label": str(await loc.get_attribute("aria-label") or ""),
                                    "title": str(await loc.get_attribute("title") or ""),
                                    "class": str(await loc.get_attribute("class") or ""),
                                    "bbox": await loc.bounding_box(),
                                    "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                                }
                            )
                        except Exception:
                            continue
                except Exception:
                    continue
            payload = {
                "container_html": html,
                "innerText": inner_text,
                "descendants": descendants,
                "buttons": [],
                "role_button_elements": [],
            }
            for sel, key in (("button", "buttons"), ("[role='button']", "role_button_elements")):
                try:
                    locs = container.locator(sel)
                    count = await locs.count()
                    for i in range(count):
                        loc = locs.nth(i)
                        try:
                            if not await _is_visible(loc):
                                continue
                            payload[key].append(
                                {
                                    "index": i,
                                    "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                                    "text": await _safe_text(loc),
                                    "aria_label": str(await loc.get_attribute("aria-label") or ""),
                                    "title": str(await loc.get_attribute("title") or ""),
                                    "class": str(await loc.get_attribute("class") or ""),
                                    "bbox": await loc.bounding_box(),
                                    "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                                }
                            )
                        except Exception:
                            continue
                except Exception:
                    continue
            (out / "manage_applicants_timeout.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            try:
                await page.screenshot(path=str(out / "manage_applicants_timeout.png"), full_page=True)
            except Exception:
                pass
            try:
                (out / "manage_applicants_timeout.html").write_text(html, encoding="utf-8")
            except Exception:
                pass
            logger.info("Saved manage_applicants_timeout.html")
            logger.info("Saved manage_applicants_timeout.png")
            logger.info("Saved manage_applicants_timeout.json")

        logger.info("worker manage_applicants_opened")
        section = await self._find_manage_applicants_section(page)
        if section is None:
            diag.notes = "manage_applicants_section_not_found"
            raise RuntimeError("Resolved container is not the Manage Applicants card.")

        container_html = await _container_html_snapshot(section)
        container_text = ""
        try:
            container_text = await section.inner_text()
        except Exception:
            pass
        container_box = None
        try:
            container_box = await section.bounding_box()
        except Exception:
            pass
        logger.info("worker manage_applicants_container_outer_html=%s", container_html)
        logger.info("worker manage_applicants_container_bounding_box=%s", container_box)
        logger.info("worker manage_applicants_container_inner_text=%r", container_text)
        if any(token in container_html.lower() for token in ("global-nav", "jump menu", "manage job posts")):
            raise RuntimeError("Resolved container is not the Manage Applicants card.")

        loading_selector = ".job-posting-hero-job-settings-loading"
        loading_loc = section.locator(loading_selector).first
        loading_settled = False
        try:
            await loading_loc.wait_for(state="hidden", timeout=15_000)
            loading_settled = True
        except Exception:
            pass
        try:
            inner_text = await section.inner_text()
        except Exception:
            inner_text = ""
        interactive_count = await _interactive_count(section)
        logger.info(
            "worker manage_applicants_loading settled=%s innerText=%r interactive_count=%d",
            loading_settled,
            inner_text,
            interactive_count,
        )
        if not loading_settled and "Loading job settings" in inner_text and interactive_count == 0:
            await _dump_timeout_artifacts(section)
            raise RuntimeError("Manage Applicants card did not finish loading within 15 seconds")

        scoped_button_count = await section.locator("button").count()
        logger.info("worker manage_applicants_container_button_count=%d", scoped_button_count)
        logger.info("worker manage_applicants_button_count_before_probe=%d", scoped_button_count)

        candidates = await _visible_edit_candidates(section)
        logger.info("worker manage_applicants_edit_candidates=%d", len(candidates))
        if not candidates:
            diag.notes = "manage_applicants_edit_not_found"
            raise RuntimeError("manage_applicants edit button candidates not found")

        chosen = None
        for index, candidate in enumerate(candidates):
            try:
                box = await candidate.bounding_box()
                logger.info(
                    "worker manage_applicants_probe index=%d aria_label=%r title=%r text=%r class=%r bbox=%s",
                    index,
                    await candidate.get_attribute("aria-label"),
                    await candidate.get_attribute("title"),
                    await _safe_text(candidate),
                    await candidate.get_attribute("class"),
                    box,
                )
                await candidate.scroll_into_view_if_needed(timeout=3000)
                await candidate.click(timeout=3000)
                try:
                    await page.wait_for_selector(
                        "[role='dialog'], dialog, [aria-modal='true']",
                        state="visible",
                        timeout=2000,
                    )
                except Exception:
                    pass
                marker = await _page_contains_markers(section)
                logger.info("worker manage_applicants_probe_result index=%d marker=%r", index, marker)
                if marker:
                    chosen = candidate
                    break
                await _close_any_open_dialog()
            except Exception as exc:
                logger.warning("worker manage_applicants_probe_failed index=%d err=%s", index, exc)
                await _close_any_open_dialog()

        if chosen is None:
            diag.notes = "manage_applicants_correct_editor_not_found"
            raise RuntimeError("could not open the Manage applicants editor by probing edit buttons")

        editor_text = await _page_contains_markers(section)
        logger.info("worker manage_applicants_editor_opened marker=%r", editor_text)
        dialog = None
        for sel in ("[role='dialog']", "dialog", "[aria-modal='true']"):
            try:
                candidate = section.locator(sel).first
                if await _is_visible(candidate):
                    dialog = candidate
                    break
            except Exception:
                continue
        if dialog is None:
            dialog = section

        out = _ARTIFACT_ROOT / self._run_ts
        out.mkdir(parents=True, exist_ok=True)

        async def _collect_elements(container: Any, selector: str) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            try:
                locs = container.locator(selector)
                count = await locs.count()
                for i in range(count):
                    loc = locs.nth(i)
                    try:
                        if not await _is_visible(loc):
                            continue
                        tag = str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or "")
                        role = str(await loc.get_attribute("role") or "")
                        text = await _safe_text(loc)
                        aria_label = str(await loc.get_attribute("aria-label") or "")
                        aria_labelledby = str(await loc.get_attribute("aria-labelledby") or "")
                        aria_describedby = str(await loc.get_attribute("aria-describedby") or "")
                        title = str(await loc.get_attribute("title") or "")
                        name = str(await loc.get_attribute("name") or "")
                        element_id = str(await loc.get_attribute("id") or "")
                        placeholder = str(await loc.get_attribute("placeholder") or "")
                        value = ""
                        checked = False
                        expanded = str(await loc.get_attribute("aria-expanded") or "")
                        try:
                            if tag in {"input", "textarea", "select"}:
                                value = str(await loc.input_value(timeout=1000) or "")
                        except Exception:
                            pass
                        try:
                            checked = await loc.is_checked()
                        except Exception:
                            checked = False
                        rows.append(
                            {
                                "index": i,
                                "tag": tag,
                                "role": role,
                                "text": text,
                                "aria-label": aria_label,
                                "aria-labelledby": aria_labelledby,
                                "aria-describedby": aria_describedby,
                                "title": title,
                                "name": name,
                                "id": element_id,
                                "placeholder": placeholder,
                                "value": value,
                                "checked": checked,
                                "disabled": await loc.is_disabled(),
                                "expanded": expanded,
                                "class": str(await loc.get_attribute("class") or ""),
                                "bounding_box": await loc.bounding_box(),
                                "outerHTML": (await loc.evaluate("(el) => el.outerHTML"))[:500],
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                pass
            return rows

        async def _collect_text_nodes(container: Any) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            try:
                locs = container.locator("*")
                count = await locs.count()
                for i in range(count):
                    loc = locs.nth(i)
                    try:
                        if not await _is_visible(loc):
                            continue
                        text = (await _safe_text(loc)).strip()
                        if text:
                            rows.append(
                                {
                                    "index": i,
                                    "tag": str(await loc.evaluate("(el) => el.tagName.toLowerCase()") or ""),
                                    "text": text,
                                    "class": str(await loc.get_attribute("class") or ""),
                                    "bounding_box": await loc.bounding_box(),
                                }
                            )
                    except Exception:
                        continue
            except Exception:
                pass
            return rows

        try:
            dialog_html = str(await dialog.evaluate("(el) => el.outerHTML") or "")
        except Exception:
            dialog_html = ""
        (out / "dialog.html").write_text(dialog_html, encoding="utf-8")
        logger.info("Saved dialog.html")
        await page.screenshot(path=str(out / "dialog.png"), full_page=False)
        logger.info("Saved dialog.png")

        payloads = {
            "dialog_buttons.json": await _collect_elements(dialog, "button"),
            "dialog_inputs.json": await _collect_elements(dialog, "input"),
            "dialog_textareas.json": await _collect_elements(dialog, "textarea"),
            "dialog_labels.json": await _collect_elements(dialog, "label"),
            "dialog_dropdowns.json": await _collect_elements(dialog, "select"),
            "dialog_comboboxes.json": await _collect_elements(dialog, "[role='combobox']"),
            "dialog_radios.json": await _collect_elements(dialog, "[role='radio'], input[type='radio']"),
            "dialog_role_map.json": await _collect_elements(dialog, "[role]"),
            "dialog_accessibility.json": await _collect_elements(
                dialog,
                "[aria-label], [aria-labelledby], [aria-describedby], [role], input, textarea, button, select, [contenteditable='true']",
            ),
        }
        for filename, payload in payloads.items():
            (out / filename).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            logger.info("Saved %s", filename)

        text_nodes = await _collect_text_nodes(dialog)
        (out / "dialog_text_nodes.json").write_text(json.dumps(text_nodes, indent=2, default=str), encoding="utf-8")
        logger.info("Saved dialog_text_nodes.json")
        logger.info("worker dialog_visible_text_nodes=%s", [row["text"] for row in text_nodes])

        async def _find_dialog_control(container: Any) -> Any | None:
            for sel in (
                "[role='button']",
                "button",
                "[role='combobox']",
                "select",
                "input",
                "[contenteditable='true']",
            ):
                try:
                    locs = container.locator(sel)
                    count = await locs.count()
                    for i in range(count):
                        loc = locs.nth(i)
                        try:
                            if await _is_visible(loc):
                                return loc
                        except Exception:
                            continue
                except Exception:
                    continue
            return None

        async def _find_url_input(container: Any) -> Any | None:
            for sel in (
                "input[placeholder*='website' i]",
                "input[placeholder*='address' i]",
                "input[aria-label*='website' i]",
                "input[aria-label*='address' i]",
                "input[type='url']",
                "[contenteditable='true']",
                "input",
            ):
                try:
                    locs = container.locator(sel)
                    count = await locs.count()
                    for i in range(count):
                        loc = locs.nth(i)
                        try:
                            if await _is_visible(loc):
                                return loc
                        except Exception:
                            continue
                except Exception:
                    continue
            return None

        control_snapshot = await _collect_control_snapshot(dialog)
        (out / "application_method_controls.json").write_text(json.dumps(control_snapshot, indent=2, default=str), encoding="utf-8")
        logger.info("Saved application_method_controls.json")

        application_method_control = None
        application_method_control_query = None
        application_method_control_text = None
        control_queries = (
            "[role='button']:has-text('On LinkedIn')",
            "button:has-text('On LinkedIn')",
            "[role='combobox']:has-text('On LinkedIn')",
            "[role='listbox']:has-text('On LinkedIn')",
            "[aria-haspopup]:has-text('On LinkedIn')",
            "div:has-text('On LinkedIn')",
        )
        for sel in control_queries:
            try:
                loc = dialog.locator(sel).first
                if await _is_visible(loc):
                    application_method_control = loc
                    application_method_control_query = sel
                    application_method_control_text = await _safe_text(loc)
                    break
            except Exception:
                continue
        if application_method_control is None:
            raise RuntimeError("application method control displaying On LinkedIn not found")
        logger.info(
            "application_method_control_found selector=%r text=%r",
            application_method_control_query,
            application_method_control_text,
        )

        expanded = False
        try:
            await application_method_control.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        click_attempts = (
            ("normal", application_method_control.click),
            ("js", _js_click),
        )
        popup = None
        for click_mode, click_fn in click_attempts:
            try:
                await click_fn(timeout=3000) if click_mode == "normal" else await click_fn(application_method_control)
                logger.info("application_method_control_clicked mode=%s", click_mode)
            except Exception as exc:
                logger.debug("worker application_method_control_click_failed mode=%s err=%s", click_mode, exc)
                continue

            for _ in range(10):
                try:
                    aria_expanded = str(await application_method_control.get_attribute("aria-expanded") or "").lower()
                    popup_candidates = []
                    for popup_sel in ("[role='listbox']", "[role='menu']", "[role='dialog']", "[role='option']", "[role='menuitem']"):
                        try:
                            loc = page.locator(popup_sel).first
                            if await _is_visible(loc):
                                popup_candidates.append(loc)
                        except Exception:
                            continue
                    if aria_expanded == "true" or popup_candidates:
                        popup = popup_candidates[0] if popup_candidates else application_method_control
                        expanded = True
                        break
                except Exception:
                    pass
                try:
                    await page.wait_for_selector(
                        "[role='listbox'], [role='menu'], [role='dialog'], [role='option'], [role='menuitem']",
                        state="visible",
                        timeout=2000,
                    )
                except Exception:
                    pass
            if expanded:
                break
        if not expanded or popup is None:
            await _debug_dump_application_method_artifacts(page, dialog, dialog, out)
            raise RuntimeError("application method popup did not open")
        logger.info("application_method_popup_opened")

        external_option = None
        external_queries = (
            "text=On an external website",
            "text=External website",
            "text=Apply externally",
            "[role='option']:has-text('On an external website')",
            "[role='option']:has-text('External website')",
            "[role='menuitem']:has-text('On an external website')",
            "[role='menuitem']:has-text('External website')",
            "button:has-text('On an external website')",
        )
        for sel in external_queries:
            try:
                loc = page.locator(sel).first
                if await _is_visible(loc):
                    external_option = loc
                    break
            except Exception:
                continue
        if external_option is None:
            await _debug_dump_application_method_artifacts(page, dialog, popup, out)
            raise RuntimeError("On an external website option not found after opening application method popup")
        logger.info("external_option_found")

        try:
            await external_option.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await external_option.click(timeout=3000)
        except Exception:
            await _js_click(external_option)
        logger.info("external_option_clicked")

        url_value = str(self._spec.application_url)
        url_input = None
        for _ in range(20):
            url_input = await _find_url_input(dialog)
            if url_input is not None:
                break
            try:
                await dialog.locator("input, textarea").first.wait_for(state="visible", timeout=250)
            except Exception:
                pass
        if url_input is None:
            await _debug_dump_application_method_artifacts(page, dialog, popup, out)
            raise RuntimeError("application url input not found after selecting external website")
        logger.info("application_url_field_found")

        try:
            await url_input.click(timeout=3000)
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
        except Exception:
            pass
        await url_input.fill(url_value, timeout=3000)
        logger.info("application_url_filled value=%r", url_value)

        committed = False
        try:
            current = ""
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    current = str(await getattr(url_input, method)(timeout=1000) or "")
                    if current.strip():
                        break
                except Exception:
                    continue
            committed = current.strip() == url_value
        except Exception:
            committed = False
        if not committed:
            raise RuntimeError("application url was not committed exactly")

        save_button = await self._find_dialog_footer_primary_button(dialog)
        if save_button is None:
            await _debug_dump_application_method_artifacts(page, dialog, popup, out)
            raise RuntimeError("dialog Save/Done button not found")
        try:
            await save_button.click(timeout=3000)
        except Exception:
            await _js_click(save_button)
        logger.info("manage_dialog_saved")
        logger.info("application_settings_saved")

        try:
            await dialog.wait_for(state="hidden", timeout=1000)
        except Exception:
            pass

        if await _is_visible(dialog):
            raise RuntimeError("application method dialog still visible after save")

        review_text = ""
        for _ in range(20):
            try:
                review_text = await page.locator("body").inner_text(timeout=5000)
                normalized_review_text = _normalize_text(review_text)
                if any(token in normalized_review_text for token in ("external", "apply externally", "on an external website")) and _normalize_text(url_value) in normalized_review_text:
                    break
            except Exception:
                pass
            try:
                await page.locator("body").wait_for(state="visible", timeout=250)
            except Exception:
                pass
        normalized_review_text = _normalize_text(review_text)
        logger.info("manage_summary_verified")
        if not any(token in normalized_review_text for token in ("external", "apply externally", "on an external website")):
            raise RuntimeError("review page did not show external application method after save")
        if _normalize_text(url_value) not in normalized_review_text:
            raise RuntimeError("review page did not show the entered application URL after save")

        diag.notes = "manage_applicants_external_website_selected"
        diag.verification_passed = True
        return True

    async def _enter_title_and_continue_validated(
        self, page: Any, title: str, diag: StepDiagnostic
    ) -> bool:
        title_loc = None
        for sel in TITLE_INPUT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await _is_visible(loc):
                    title_loc = loc
                    break
            except Exception:
                continue

        if title_loc is None:
            diag.notes = "title_input_not_found"
            diag.fields_skipped.append("title")
            return False

        try:
            await title_loc.click(timeout=3000)
            await page.keyboard.press("Control+a")
            await title_loc.type(title, delay=60)
            diag.fields_filled.append("title")
            logger.info("worker title_entered value=%r", title)
        except Exception as exc:
            logger.warning("worker title_type_failed: %s", exc)
            diag.fields_skipped.append("title")
            return False

        errors = await self._click_continue_with_validation(page, diag, phase="title")
        if errors is None:
            diag.notes = "continue_button_not_found"
            return False
        logger.debug("worker title_continue_validation_errors=%s", errors)
        if errors:
            repaired = await self._repair_validation_messages(page, errors, self._spec)
            logger.debug("worker title_validation_repair_result=%s", repaired)
        return True

    async def _enter_title_and_continue(
        self, page: Any, title: str, diag: StepDiagnostic
    ) -> bool:
        """Type job title into the title input then click Continue."""
        title_loc = None
        for sel in TITLE_INPUT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await _is_visible(loc):
                    title_loc = loc
                    break
            except Exception:
                continue

        if title_loc is None:
            diag.notes = "title_input_not_found"
            diag.fields_skipped.append("title")
            return False

        try:
            await title_loc.click(timeout=3000)
            await page.keyboard.press("Control+a")
            await title_loc.type(title, delay=60)
            diag.fields_filled.append("title")
            logger.info("worker title_entered value=%r", title)
        except Exception as exc:
            logger.warning("worker title_type_failed: %s", exc)
            diag.fields_skipped.append("title")
            return False

        # Click Continue — never click anything that looks like Publish/Post/Submit
        for sel in CONTINUE_BUTTON_SELECTORS:
            try:
                loc = page.locator(sel).first
                if not await _is_visible(loc):
                    continue
                label = (await _safe_text(loc)).lower()
                if any(t in label for t in ("post", "publish", "submit")):
                    continue
                await loc.click(timeout=5000)
                diag.navigation_succeeded = True
                logger.info("worker continue_clicked sel=%r", sel)
                return True
            except Exception:
                continue

        diag.notes = "continue_button_not_found"
        return False

    async def _edit_job_details(
        self, page: Any, spec: JobPostingSpec, diag: StepDiagnostic
    ) -> bool:
        """Click the edit pencil on the summary page then fill details."""
        pencil_clicked = await self._click_first_visible(page, EDIT_PENCIL_SELECTORS)
        if pencil_clicked:
            await page.wait_for_selector(
                "[aria-label*='Company' i], input[id*='company' i], [aria-label*='Workplace type' i], [aria-label*='Job type' i]",
                state="visible",
                timeout=5000,
            )
            logger.info("worker edit_pencil_clicked")
        else:
            diag.notes = "edit_pencil_not_found"
            logger.warning("worker edit_pencil_not_found url=%s", page.url)

        container = await _find_form_container(page)
        if container is None:
            diag.notes = (diag.notes + " no_form_container").strip()
            return False

        async def _find_by_label_or_selector(
            self_page: Any,
            label_text: str,
            selector: str,
        ) -> Any | None:
            try:
                by_label = self_page.get_by_label(label_text, exact=False).first
                if await _is_visible(by_label):
                    return by_label
            except Exception:
                pass
            try:
                by_role = self_page.get_by_role("textbox", name=label_text).first
                if await _is_visible(by_role):
                    return by_role
            except Exception:
                pass
            try:
                loc = container.locator(selector).first
                if await _is_visible(loc):
                    return loc
            except Exception:
                pass
            return None

        async def _read_field_value(loc: Any) -> str:
            for method in ("input_value", "text_content", "inner_text"):
                try:
                    raw = str(await getattr(loc, method)(timeout=1000) or "")
                    if raw.strip():
                        return raw.strip()
                except Exception:
                    continue
            return ""

        async def _clear_field(loc: Any) -> None:
            try:
                await loc.click(timeout=3000)
            except Exception:
                pass
            for _ in range(3):
                try:
                    await loc.press("Control+a")
                    await loc.press("Backspace")
                except Exception:
                    pass
                try:
                    await loc.fill("")
                except Exception:
                    pass
                try:
                    if not (await _read_field_value(loc)):
                        break
                except Exception:
                    break

        async def _choose_first_suggestion(
            trigger: Any,
            expected: str,
            *,
            exact_text: bool = False,
        ) -> bool:
            try:
                await page.wait_for_selector(
                    "[role='option'], li[role='option'], .typeahead-result",
                    state="visible",
                    timeout=2000,
                )
            except Exception:
                pass
            suggestion_selectors = [
                f"[role='option'][aria-selected='true']:has-text('{expected}')",
                f"[role='option']:has-text('{expected}')",
                f"li:has-text('{expected}')",
                f"div:has-text('{expected}')",
                "[role='option']",
                "li[role='option']",
                ".typeahead-result",
            ]
            for suggestion_sel in suggestion_selectors:
                try:
                    suggestion = page.locator(suggestion_sel).first
                    if await _is_visible(suggestion):
                        await suggestion.scroll_into_view_if_needed(timeout=3000)
                        await suggestion.click(timeout=3000)
                        return True
                except Exception:
                    continue
            try:
                await trigger.press("ArrowDown")
                await trigger.press("Enter")
                await trigger.press("Tab")
            except Exception:
                pass
            value = await _read_field_value(trigger)
            if exact_text:
                return _normalize_text(expected) in _normalize_text(value)
            return bool(value)

        async def _fill_text_field(
            label: str,
            selector: str,
            value: str,
            *,
            typeahead: bool = False,
            exact_text: bool = False,
        ) -> bool:
            loc = await _find_by_label_or_selector(page, label, selector)
            if loc is None:
                logger.warning("worker required_field_not_found field=%r", label)
                return False
            try:
                logger.debug("worker field_fill_start field=%r value=%r", label, value)
                await _clear_field(loc)
                await loc.type(value, delay=50)
                if typeahead:
                    try:
                        await page.wait_for_selector(
                            "[role='option'], li[role='option'], .typeahead-result",
                            state="visible",
                            timeout=2000,
                        )
                    except Exception:
                        pass
                    await _choose_first_suggestion(loc, value, exact_text=exact_text)
                current = await _read_field_value(loc)
                ok = _normalize_text(value) in _normalize_text(current) if exact_text else bool(current)
                logger.debug(
                    "worker field_fill_verify field=%r ok=%s current=%r",
                    label,
                    ok,
                    current,
                )
                return ok
            except Exception as exc:
                logger.warning("worker field_fill_failed field=%r err=%s", label, exc)
                return False

        async def _fill_dropdown_field(label: str, selector: str, value: str) -> bool:
            loc = await _find_by_label_or_selector(page, label, selector)
            if loc is None:
                logger.warning("worker required_field_not_found field=%r", label)
                return False
            try:
                from app.linkedin.playwright.dropdown_engine import DropdownEngine
                engine = DropdownEngine(loc, container=container, timeout_ms=8000)
                await engine.select(value)
                current = await _read_field_value(loc)
                ok = _normalize_text(value) in _normalize_text(current) or not current
                logger.debug(
                    "worker dropdown_fill_verify field=%r ok=%s current=%r",
                    label,
                    ok,
                    current,
                )
                return ok
            except Exception as exc:
                logger.warning("worker dropdown_fill_failed field=%r err=%s", label, exc)
                return False

        async def _validation_retry_loop(max_attempts: int = 3) -> bool:
            for attempt in range(1, max_attempts + 1):
                errors = await _collect_validation_errors(page)
                logger.debug("worker validation_check attempt=%d errors=%s", attempt, errors)
                if not errors:
                    return True
                lowered = " ".join(errors).lower()
                if "job location" in lowered or "employee location" in lowered:
                    await _fill_text_field(
                        "Employee location",
                        "[aria-label*='Employee location' i], [aria-label*='Job location' i], input[id*='location' i], input[name*='location' i], input[placeholder*='location' i]",
                        spec.location,
                        typeahead=True,
                        exact_text=True,
                    )
                if "company" in lowered:
                    await _fill_text_field(
                        "Company",
                        "[aria-label*='Company' i], input[id*='company' i]",
                        spec.company,
                        typeahead=True,
                        exact_text=True,
                    )
                if "workplace" in lowered:
                    await _fill_dropdown_field(
                        "Workplace type",
                        "[aria-label*='Workplace type' i]",
                        spec.workplace_type,
                    )
                if "job type" in lowered:
                    await _fill_dropdown_field(
                        "Job type",
                        "[aria-label*='Job type' i]",
                        spec.job_type,
                    )
            return not await _collect_validation_errors(page)

        async def _fill_required_fields() -> dict[str, bool]:
            statuses: dict[str, bool] = {}
            statuses["company"] = await _fill_text_field(
                "Company",
                "[aria-label*='Company' i], input[id*='company' i]",
                spec.company,
                typeahead=True,
                exact_text=True,
            )
            statuses["workplace_type"] = await _fill_dropdown_field(
                "Workplace type",
                "[aria-label*='Workplace type' i]",
                spec.workplace_type,
            )
            statuses["location"] = await self._probe_employee_location_autocomplete(page, spec.location)
            statuses["job_type"] = await _fill_dropdown_field(
                "Job type",
                "[aria-label*='Job type' i]",
                spec.job_type,
            )
            return statuses

        field_status = await _fill_required_fields()
        logger.debug("worker required_field_status initial=%s", field_status)

        for attempt in range(1, 6):
            errors = await self._collect_validation_messages(page)
            logger.debug("worker validation_before_continue attempt=%d errors=%s", attempt, errors)
            if not errors and all(field_status.values()):
                break
            repaired = await self._repair_validation_messages(page, errors, spec, field_status)
            logger.debug("worker validation_repair attempt=%d repaired=%s", attempt, repaired)
            field_status.update({k: True for k, v in repaired.items() if v and k in field_status})

        errors = await self._collect_validation_messages(page)
        if errors or not all(field_status.values()):
            diag.notes = f"validation_failed errors={errors} field_status={field_status}"
            logger.warning("worker validation_failed errors=%s field_status=%s", errors, field_status)
            return False

        diag.fields_filled.extend([name for name, ok in field_status.items() if ok and name not in diag.fields_filled])
        diag.verification_passed = True
        return True

    async def _handle_description(
        self, page: Any, spec: JobPostingSpec, diag: StepDiagnostic
    ) -> None:
        """Fill description if provided; otherwise leave AI-generated content.
        Then click Continue if present.
        """
        if not spec.description:
            diag.notes = "description_not_provided_using_ai_generated"
            diag.fields_skipped.append("description")
            logger.info("worker description skipped — will use AI-generated")
        else:
            editor = None
            for sel in DESCRIPTION_EDITOR_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await _is_visible(loc):
                        editor = loc
                        break
                except Exception:
                    continue

            if editor is None:
                diag.notes = "description_editor_not_found"
                diag.fields_skipped.append("description")
                logger.warning("worker description_editor_not_found")
            else:
                try:
                    await editor.click(timeout=3000)
                    await page.keyboard.press("Control+a")
                    await editor.type(spec.description, delay=20)
                    diag.fields_filled.append("description")
                    logger.info("description_pasted")
                    logger.info("worker description_filled length=%d", len(spec.description))
                except Exception as exc:
                    logger.warning("worker description_fill_failed: %s", exc)
                    diag.fields_skipped.append("description")

        errors = await self._click_continue_with_validation(page, diag, phase="description")
        if errors is not None:
            logger.debug("worker description_continue_validation_errors=%s", errors)
        logger.info("description_verified")
