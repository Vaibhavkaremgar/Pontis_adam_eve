"""file_upload_engine.py — Generic Playwright file upload helper.

Supports:
  - input[type=file] (visible and hidden)
  - Drag & drop
  - Multiple files
  - Post-upload verification

No worker integration. No LinkedIn-specific logic.

Usage:
    engine = FileUploadEngine(input_locator, container=container_loc)
    await engine.upload("/path/to/resume.pdf")
    ok = await engine.verify("resume.pdf")
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileUploadEngine:
    """Generic file upload engine scoped to a container.

    Args:
        locator:    Playwright Locator for the file input element.
        container:  Parent container Locator (used for verification).
        timeout_ms: Default operation timeout.
    """

    def __init__(
        self,
        locator: Any,
        *,
        container: Any | None = None,
        timeout_ms: int = 15_000,
    ) -> None:
        self._loc = locator
        self._container = container
        self._t = timeout_ms

    # ── Public API ────────────────────────────────────────────────────────────

    async def upload(self, file_path: str | Path) -> None:
        """Upload a single file.

        Tries input[type=file] set_input_files first.
        Falls back to making a hidden input visible, then set_input_files.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Upload file not found: {path}")

        logger.info("file_upload uploading path=%s", path)
        await self._upload_via_input([path])

    async def upload_multiple(self, file_paths: list[str | Path]) -> None:
        """Upload multiple files at once."""
        paths = [Path(p) for p in file_paths]
        for p in paths:
            if not p.exists():
                raise FileNotFoundError(f"Upload file not found: {p}")
        logger.info("file_upload uploading count=%d", len(paths))
        await self._upload_via_input(paths)

    async def upload_drag_drop(
        self,
        file_path: str | Path,
        drop_zone_selector: str,
    ) -> None:
        """Upload via drag & drop onto a drop zone inside the container.

        Uses the Playwright page.drag_and_drop approach via JS DataTransfer.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Upload file not found: {path}")

        container = self._container
        if container is None:
            raise RuntimeError("file_upload drag_drop requires container to be set")

        drop_zone = container.locator(drop_zone_selector).first
        await drop_zone.scroll_into_view_if_needed(timeout=self._t)

        # Playwright's set_input_files on a hidden input is more reliable than
        # synthetic DataTransfer events across browsers.  Try input first.
        try:
            await self._upload_via_input([path])
            logger.info("file_upload drag_drop resolved via input path=%s", path)
            return
        except Exception:
            pass

        # Synthetic drag-drop via JS DataTransfer
        try:
            await drop_zone.evaluate(
                """(el, fileName) => {
                    const dt = new DataTransfer();
                    const file = new File([''], fileName, { type: 'application/octet-stream' });
                    dt.items.add(file);
                    el.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true }));
                    el.dispatchEvent(new DragEvent('dragover',  { dataTransfer: dt, bubbles: true }));
                    el.dispatchEvent(new DragEvent('drop',      { dataTransfer: dt, bubbles: true }));
                }""",
                path.name,
            )
            logger.info("file_upload drag_drop js_datatransfer path=%s", path)
        except Exception as exc:
            logger.warning("file_upload drag_drop failed: %s", exc)
            raise

    async def verify(self, filename: str, *, timeout_ms: int = 5000) -> bool:
        """Return True if the uploaded filename is reflected in the UI."""
        from app.linkedin.playwright.verification_helpers import verify_upload
        container = self._container
        if container is None:
            # Fall back to checking the input value directly
            try:
                val = str(await self._loc.input_value(timeout=timeout_ms) or "")
                return filename in val
            except Exception:
                return False
        return await verify_upload(
            container,
            selector="input[type='file']",
            filename=filename,
            timeout_ms=timeout_ms,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _upload_via_input(self, paths: list[Path]) -> None:
        """set_input_files on the locator, making it visible first if needed."""
        try:
            # Try directly — works for visible inputs
            await self._loc.set_input_files(
                [str(p) for p in paths],
                timeout=self._t,
            )
            logger.debug("file_upload set_input_files ok count=%d", len(paths))
            return
        except Exception as exc:
            logger.debug("file_upload set_input_files direct failed: %s — trying unhide", exc)

        # Make hidden input temporarily visible
        try:
            await self._loc.evaluate(
                "el => { el.style.display = 'block'; el.style.visibility = 'visible'; "
                "el.style.opacity = '1'; el.style.width = '1px'; el.style.height = '1px'; }"
            )
            await asyncio.sleep(0.1)
            await self._loc.set_input_files(
                [str(p) for p in paths],
                timeout=self._t,
            )
            logger.debug("file_upload set_input_files unhidden ok count=%d", len(paths))
        except Exception as exc:
            logger.warning("file_upload set_input_files unhidden failed: %s", exc)
            raise
