import asyncio
import logging
from typing import Any

import httpx
import redis.asyncio as aioredis

from config import Settings
from drain import process_claimed_messages
from telegram import TelegramRateLimiter

logger = logging.getLogger(__name__)


async def reaper_loop(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    limiter: TelegramRateLimiter,
    stop_event: asyncio.Event,
) -> None:
    consumer = settings.consumer_name
    start_id = "0-0"

    logger.info("Reaper started", extra={"event": "reaper_started", "consumer": consumer})

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.reaper_interval_seconds)
            break
        except TimeoutError:
            pass

        try:
            claimed = await redis_client.xautoclaim(
                settings.redis_stream,
                settings.redis_group,
                consumer,
                min_idle_time=settings.reaper_idle_ms,
                start_id=start_id,
                count=settings.reaper_batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Reaper failed", extra={"event": "reaper_error", "error": str(exc)})
            continue

        start_id, messages = _parse_xautoclaim_result(claimed)
        if not messages:
            if start_id in ("0-0", b"0-0"):
                start_id = "0-0"
            continue

        logger.info(
            "Reaper claimed pending messages",
            extra={"event": "reaper_claimed", "count": len(messages), "next_start_id": _decode(start_id)},
        )
        await process_claimed_messages(redis_client, http_client, settings, limiter, messages, stop_event)

    logger.info("Reaper stopped", extra={"event": "reaper_stopped", "consumer": consumer})


def _parse_xautoclaim_result(result: Any) -> tuple[Any, list[tuple[Any, dict]]]:
    if not result:
        return "0-0", []
    if len(result) >= 2:
        return result[0], result[1]
    return "0-0", []


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)
