from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from config import Settings

logger = logging.getLogger(__name__)


class TelegramLinkCache:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http_client = http_client
        self._settings = settings
        self._links: dict[int, int] = {}
        self._last_success_at: float | None = None
        self._lock = asyncio.Lock()

    async def refresh_once(self) -> bool:
        url = f"{self._settings.supabase_url}/rest/v1/{self._settings.discourse_links_table}"
        headers = self._headers()
        params = {
            "is_active": "eq.true",
            "select": "discourse_user_id,chat_id",
        }

        try:
            response = await self._http_client.get(
                url,
                headers=headers,
                params=params,
                timeout=self._settings.supabase_timeout_seconds,
            )
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            logger.warning(
                "Supabase links cache refresh failed",
                extra={"event": "links_cache_refresh_failed", "error": str(exc)},
            )
            return False

        links: dict[int, int] = {}
        invalid_rows = 0
        for row in rows:
            try:
                user_id = int(row.get("discourse_user_id"))
                chat_id = int(row.get("chat_id"))
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            links[user_id] = chat_id

        async with self._lock:
            self._links = links
            self._last_success_at = time.monotonic()

        logger.info(
            "Supabase links cache refreshed",
            extra={
                "event": "links_cache_refresh_ok",
                "links_count": len(links),
                "invalid_rows": invalid_rows,
            },
        )
        return True

    async def refresh_loop(self, stop_event: asyncio.Event) -> None:
        if not self._settings.supabase_links_cache_enabled:
            logger.info("Supabase links cache disabled", extra={"event": "links_cache_disabled"})
            await stop_event.wait()
            return

        await self.refresh_once()
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._settings.supabase_links_cache_refresh_seconds,
                )
            except asyncio.TimeoutError:
                await self.refresh_once()

        logger.info("Supabase links cache stopped", extra={"event": "links_cache_stopped"})

    async def lookup(self, user_id: int) -> int | None:
        if not self._settings.supabase_links_cache_enabled:
            return await self._lookup_direct(user_id)

        chat_id = await self._lookup_cached(user_id)
        if chat_id is not None:
            return chat_id

        if self._settings.supabase_links_direct_lookup_on_miss:
            return await self._lookup_direct(user_id)

        return None

    async def remember(self, user_id: int, chat_id: int) -> None:
        if not self._settings.supabase_links_cache_enabled:
            return

        async with self._lock:
            if not self._cache_is_usable_locked():
                self._links = {}

            self._links[user_id] = chat_id
            self._last_success_at = time.monotonic()

    async def _lookup_cached(self, user_id: int) -> int | None:
        async with self._lock:
            if not self._cache_is_usable_locked():
                return None
            return self._links.get(user_id)

    def _cache_is_usable_locked(self) -> bool:
        if self._last_success_at is None:
            return False
        age = time.monotonic() - self._last_success_at
        return age <= self._settings.supabase_links_cache_stale_seconds

    async def _lookup_direct(self, user_id: int) -> int | None:
        url = f"{self._settings.supabase_url}/rest/v1/{self._settings.discourse_links_table}"
        params = {
            "discourse_user_id": f"eq.{user_id}",
            "is_active": "eq.true",
            "select": "chat_id",
            "limit": "1",
        }

        try:
            response = await self._http_client.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self._settings.supabase_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning(
                "Supabase direct lookup failed",
                extra={"event": "lookup_failed", "user_id": user_id, "error": str(exc)},
            )
            return None

        if not data:
            return None

        try:
            chat_id = int(data[0].get("chat_id"))
        except (TypeError, ValueError):
            logger.warning(
                "Supabase returned invalid chat_id",
                extra={
                    "event": "lookup_invalid_chat_id",
                    "user_id": user_id,
                    "chat_id": data[0].get("chat_id"),
                },
            )
            return None

        async with self._lock:
            if self._cache_is_usable_locked():
                self._links[user_id] = chat_id

        return chat_id

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._settings.supabase_key,
            "Authorization": f"Bearer {self._settings.supabase_key}",
        }
