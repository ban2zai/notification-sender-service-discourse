from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from config import Settings

logger = logging.getLogger(__name__)


class TelegramRateLimiter:
    def __init__(self, global_rate_per_second: float, chat_min_interval_seconds: float) -> None:
        self._global_interval = 1.0 / max(global_rate_per_second, 0.1)
        self._chat_min_interval = chat_min_interval_seconds
        self._global_lock = asyncio.Lock()
        self._chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_global_at = 0.0
        self._next_chat_at: dict[int, float] = {}

    async def wait(self, chat_id: int) -> None:
        async with self._global_lock:
            now = time.monotonic()
            delay = max(0.0, self._next_global_at - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_global_at = time.monotonic() + self._global_interval

        async with self._chat_locks[chat_id]:
            now = time.monotonic()
            delay = max(0.0, self._next_chat_at.get(chat_id, 0.0) - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_chat_at[chat_id] = time.monotonic() + self._chat_min_interval


async def send_telegram_message(
    http_client: httpx.AsyncClient,
    settings: Settings,
    chat_id: int,
    text: str,
) -> tuple[bool, int | None, str | None]:
    url = f"{settings.telegram_api_url}/bot{settings.bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    try:
        response = await http_client.post(url, json=payload, timeout=settings.telegram_timeout_seconds)
    except Exception as exc:
        logger.warning(
            "Telegram request failed",
            extra={"event": "send_failed", "chat_id": chat_id, "error": str(exc)},
        )
        return False, None, str(exc)

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "Telegram returned non-JSON response",
            extra={
                "event": "send_failed",
                "chat_id": chat_id,
                "http_status": response.status_code,
                "body_preview": response.text[:300],
            },
        )
        return False, None, "non_json_response"

    if response.status_code == 200 and data.get("ok"):
        logger.debug("Telegram message sent", extra={"event": "send_ok", "chat_id": chat_id})
        return True, None, None

    retry_after = data.get("parameters", {}).get("retry_after")
    description = data.get("description")
    event = "telegram_429" if response.status_code == 429 or data.get("error_code") == 429 else "send_failed"
    logger.warning(
        "Telegram rejected message",
        extra={
            "event": event,
            "chat_id": chat_id,
            "http_status": response.status_code,
            "error_code": data.get("error_code"),
            "description": description,
            "retry_after": retry_after,
        },
    )
    return False, retry_after, description
