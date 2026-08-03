"""account_lock_manager.py — Reusable per-account browser session lock.

Guarantees that only ONE browser session can use a LinkedIn account at a time.
Not wired into any existing worker — available for future features only.

Usage (async context manager):
    async with lock_manager.lock(account_id):
        ...

Usage (manual):
    acquired = await lock_manager.try_lock(account_id, timeout=30)
    if acquired:
        try:
            ...
        finally:
            await lock_manager.unlock(account_id)
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_STALE_AFTER_S: float = 300.0   # 5 minutes
_DEFAULT_TIMEOUT_S: float = 60.0


@dataclass
class _LockEntry:
    acquired_at: float = field(default_factory=time.monotonic)
    owner: str = ""


class LinkedInAccountLockManager:
    """In-process async lock manager — one lock slot per account_id.

    Thread-safety: asyncio single-threaded model only.
    For multi-process deployments replace _locks with a Redis-backed store.
    """

    def __init__(
        self,
        *,
        stale_after_s: float = _DEFAULT_STALE_AFTER_S,
    ) -> None:
        self._stale_after_s = stale_after_s
        # account_id → asyncio.Lock
        self._mutexes: dict[str, asyncio.Lock] = {}
        # account_id → _LockEntry (set while held)
        self._entries: dict[str, _LockEntry] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def lock(
        self,
        account_id: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        owner: str = "",
    ) -> None:
        """Acquire the lock for account_id, waiting up to *timeout* seconds.

        Raises TimeoutError if the lock cannot be acquired within *timeout*.
        Recovers stale locks automatically before attempting acquisition.
        """
        self._recover_stale(account_id)
        mutex = self._get_mutex(account_id)
        logger.debug("account_lock acquiring account_id=%s owner=%r", account_id, owner)
        try:
            await asyncio.wait_for(mutex.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "account_lock timeout account_id=%s timeout_s=%.1f owner=%r",
                account_id, timeout, owner,
            )
            raise TimeoutError(
                f"Could not acquire lock for account_id={account_id!r} "
                f"within {timeout}s"
            )
        self._entries[account_id] = _LockEntry(owner=owner)
        logger.info("account_lock acquired account_id=%s owner=%r", account_id, owner)

    async def unlock(self, account_id: str) -> None:
        """Release the lock for account_id. Safe to call even if not held."""
        mutex = self._mutexes.get(account_id)
        if mutex is None or not mutex.locked():
            logger.debug("account_lock unlock_noop account_id=%s (not held)", account_id)
            return
        self._entries.pop(account_id, None)
        mutex.release()
        logger.info("account_lock released account_id=%s", account_id)

    async def try_lock(
        self,
        account_id: str,
        *,
        timeout: float = 0.0,
        owner: str = "",
    ) -> bool:
        """Try to acquire the lock.  Returns True on success, False on failure.

        timeout=0 (default) → non-blocking single attempt.
        timeout>0 → wait up to *timeout* seconds before giving up.
        """
        self._recover_stale(account_id)
        mutex = self._get_mutex(account_id)
        if timeout == 0.0:
            acquired = mutex.locked() is False and mutex._value > 0  # type: ignore[attr-defined]
            # Use acquire() with a zero-length wait_for to be safe
            try:
                await asyncio.wait_for(mutex.acquire(), timeout=0.001)
                acquired = True
            except (asyncio.TimeoutError, Exception):
                acquired = False
        else:
            try:
                await asyncio.wait_for(mutex.acquire(), timeout=timeout)
                acquired = True
            except asyncio.TimeoutError:
                acquired = False

        if acquired:
            self._entries[account_id] = _LockEntry(owner=owner)
            logger.info("account_lock try_lock=acquired account_id=%s owner=%r", account_id, owner)
        else:
            logger.debug("account_lock try_lock=failed account_id=%s", account_id)
        return acquired

    def is_locked(self, account_id: str) -> bool:
        """Return True if the lock is currently held."""
        mutex = self._mutexes.get(account_id)
        return mutex is not None and mutex.locked()

    @asynccontextmanager
    async def acquire(
        self,
        account_id: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        owner: str = "",
    ) -> AsyncIterator[None]:
        """Async context manager — acquires on enter, releases on exit.

        Example:
            async with lock_manager.acquire(account_id, timeout=30):
                # exclusive access guaranteed here
                ...
        """
        await self.lock(account_id, timeout=timeout, owner=owner)
        try:
            yield
        finally:
            await self.unlock(account_id)

    # ── Stale lock recovery ───────────────────────────────────────────────────

    def _recover_stale(self, account_id: str) -> None:
        """Force-release a lock that has been held longer than stale_after_s."""
        entry = self._entries.get(account_id)
        if entry is None:
            return
        age = time.monotonic() - entry.acquired_at
        if age >= self._stale_after_s:
            logger.warning(
                "account_lock stale_recovery account_id=%s age_s=%.1f owner=%r",
                account_id, age, entry.owner,
            )
            mutex = self._mutexes.get(account_id)
            if mutex and mutex.locked():
                mutex.release()
            self._entries.pop(account_id, None)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_mutex(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._mutexes:
            self._mutexes[account_id] = asyncio.Lock()
        return self._mutexes[account_id]
