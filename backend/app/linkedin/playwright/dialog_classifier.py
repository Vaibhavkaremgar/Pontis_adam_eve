"""dialog_classifier.py — Text-based LinkedIn dialog content classifier.

Single responsibility: given a visible dialog on the page, read its visible
text and classify what it is asking the user to do.

Rules:
  - Classification is based ONLY on visible text content.
  - Never uses CSS class names.
  - Never uses aria-label attribute values for classification.
  - Never dismisses the dialog.
  - Never clicks anything.

Returns one of three outcomes:
  DIALOG_PREMIUM     — LinkedIn is asking the user to upgrade / pay.
  DIALOG_CONNECTION  — LinkedIn is asking the user to connect first.
  DIALOG_UNKNOWN     — Dialog is present but content is unrecognised.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification outcomes
# ---------------------------------------------------------------------------

DIALOG_PREMIUM    = "DIALOG_PREMIUM"
DIALOG_CONNECTION = "DIALOG_CONNECTION"
DIALOG_UNKNOWN    = "DIALOG_UNKNOWN"

# ---------------------------------------------------------------------------
# Text signal tables — visible text only, case-insensitive substring match
# ---------------------------------------------------------------------------

# Any of these phrases in the dialog text → premium upsell
_PREMIUM_SIGNALS: tuple[str, ...] = (
    "try premium",
    "upgrade",
    "premium",
    "inmail",
    "sales navigator",
    "recruiter lite",
    "recruiter",
    "free trial",
    "start your free",
    "unlock",
    "subscribe",
    "membership",
    "paid",
    "message anyone",
    "reach anyone",
    "get premium",
    "linkedin premium",
    "career",
    "business plus",
    "learning",
)

# Any of these phrases in the dialog text → connection required
_CONNECTION_SIGNALS: tuple[str, ...] = (
    "connect first",
    "you're not connected",
    "you are not connected",
    "send a connection request",
    "connect with",
    "not in your network",
    "outside your network",
    "send connection",
    "add to your network",
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DialogClassification:
    outcome: str = DIALOG_UNKNOWN
    matched_signal: str = ""
    dialog_text: str = ""
    diagnostics: dict = field(default_factory=dict)

    @property
    def is_premium(self) -> bool:
        return self.outcome == DIALOG_PREMIUM

    @property
    def is_connection(self) -> bool:
        return self.outcome == DIALOG_CONNECTION


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class DialogClassifier:
    """Classify a visible dialog by reading its text content.

    Never dismisses the dialog.  Never clicks.  Read-only.
    """

    async def classify(self, page: Any) -> DialogClassification:
        """Find the topmost visible dialog and classify it.

        Returns DialogClassification with outcome=DIALOG_UNKNOWN if no
        dialog is found or the text does not match any known signal.
        """
        dialog_text, dialog_html_snippet = await self._read_dialog(page)

        if not dialog_text:
            logger.info("dialog_classifier no_dialog_found")
            return DialogClassification(
                diagnostics={"reason": "no_visible_dialog"},
            )

        text_lower = dialog_text.lower()
        logger.info(
            "dialog_classifier text_sample=%r",
            dialog_text[:200],
        )

        # Connection check first — more specific, avoids false premium match
        for signal in _CONNECTION_SIGNALS:
            if signal in text_lower:
                logger.info(
                    "dialog_classifier outcome=%s signal=%r",
                    DIALOG_CONNECTION, signal,
                )
                return DialogClassification(
                    outcome=DIALOG_CONNECTION,
                    matched_signal=signal,
                    dialog_text=dialog_text,
                    diagnostics={"html_snippet": dialog_html_snippet},
                )

        for signal in _PREMIUM_SIGNALS:
            if signal in text_lower:
                logger.info(
                    "dialog_classifier outcome=%s signal=%r",
                    DIALOG_PREMIUM, signal,
                )
                return DialogClassification(
                    outcome=DIALOG_PREMIUM,
                    matched_signal=signal,
                    dialog_text=dialog_text,
                    diagnostics={"html_snippet": dialog_html_snippet},
                )

        logger.info("dialog_classifier outcome=%s", DIALOG_UNKNOWN)
        return DialogClassification(
            outcome=DIALOG_UNKNOWN,
            dialog_text=dialog_text,
            diagnostics={"html_snippet": dialog_html_snippet},
        )

    async def _read_dialog(self, page: Any) -> tuple[str, str]:
        """Return (visible_text, html_snippet) of the topmost visible dialog."""
        selectors = [
            "[role='dialog']",
            "[role='alertdialog']",
            "dialog",
        ]
        for sel in selectors:
            try:
                locs = page.locator(sel)
                count = await locs.count()
                for i in range(min(count, 5)):
                    item = locs.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        text = str(await item.inner_text(timeout=2000) or "").strip()
                        snippet = str(
                            await item.evaluate("el => el.outerHTML.slice(0, 600)") or ""
                        )
                        if text:
                            return text, snippet
                    except Exception:
                        continue
            except Exception:
                continue
        return "", ""
