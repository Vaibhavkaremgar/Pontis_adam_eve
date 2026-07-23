from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from app.linkedin.workers.messaging_worker import LinkedInMessagingWorker
from scripts.linkedin_dev_account import get_development_account_id
from scripts.linkedin_cleanup import close_chat_overlays


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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


async def _run(account_id: str, profile_url: str, message_text: str) -> int:
    print("Starting LinkedIn messaging worker")
    print(f"Account: {account_id}")
    print(f"Profile URL: {profile_url}")
    if message_text:
        print(f"Message: {message_text}")
    print("Closing stale chat overlays...")
    await close_chat_overlays(account_id)
    worker = LinkedInMessagingWorker(account_id=account_id)
    result = await worker.run(profile_url, message_text)
    print("MessagingResult:")
    print(json.dumps(_serialize(result), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual validation harness for LinkedInMessagingWorker")
    parser.add_argument("--account", default="", help="LinkedIn development account id")
    parser.add_argument("--url", required=True, help="LinkedIn profile URL")
    parser.add_argument("--message", default="", help="Message text to send (omit for instrumentation-only mode)")
    args = parser.parse_args()
    if args.account:
        os.environ["LINKEDIN_DEV_ACCOUNT_ID"] = args.account
        account_id = args.account
    else:
        account_id = get_development_account_id()
    return asyncio.run(_run(account_id, args.url, args.message))


if __name__ == "__main__":
    raise SystemExit(main())
