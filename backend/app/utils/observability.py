from __future__ import annotations

import json
import logging
from typing import Any


def emit_trace(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {"event": event, **{key: value for key, value in fields.items() if value is not None}}
    try:
        logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    except Exception:
        logger.info("%s %s", event, " ".join(f"{key}={value}" for key, value in payload.items() if key != "event"))
