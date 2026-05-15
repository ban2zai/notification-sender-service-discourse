import asyncio
import logging
from contextlib import suppress
from typing import Any

import httpx
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from config import Settings
from telegram import TelegramRateLimiter, send_telegram_message

logger = logging.getLogger(__name__)


async def ensure_group(redis_client: aioredis.Redis, settings: Settings) -> None:
    try:
        await redis_client.xgroup_create(
            settings.redis_stream,
            settings.redis_group,
            id="0",
            mkstream=True,
        )
        logger.info("Redis consumer group created", extra={"event": "consumer_group_created"})
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.debug("Redis consumer group already exists", extra={"event": "consumer_group_exists"})
            return
        raise


async def drain_loop(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    limiter: TelegramRateLimiter,
    stop_event: asyncio.Event,
) -> None:
    await ensure_group(redis_client, settings)
    consumer = settings.consumer_name
    logger.info("Drain started", extra={"event": "drain_started", "consumer": consumer})

    while not stop_event.is_set():
        try:
            results = await redis_client.xreadgroup(
                settings.redis_group,
                consumer,
                {settings.redis_stream: ">"},
                count=settings.drain_batch_size,
                block=settings.drain_block_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Drain read failed", extra={"event": "drain_read_failed", "error": str(exc)})
            await asyncio.sleep(2)
            continue

        if not results:
            continue

        for _, messages in results:
            await _process_messages(redis_client, http_client, settings, limiter, messages, stop_event)

    logger.info("Drain stopped", extra={"event": "drain_stopped", "consumer": consumer})


async def process_claimed_messages(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    limiter: TelegramRateLimiter,
    messages: list[tuple[Any, dict]],
    stop_event: asyncio.Event,
) -> None:
    await _process_messages(redis_client, http_client, settings, limiter, messages, stop_event)


async def _process_messages(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    limiter: TelegramRateLimiter,
    messages: list[tuple[Any, dict]],
    stop_event: asyncio.Event,
) -> None:
    for message_id, fields in messages:
        if stop_event.is_set():
            logger.info(
                "Drain shutdown requested before next message",
                extra={"event": "drain_batch_interrupted", "message_id": _decode(message_id)},
            )
            return

        await _process_one(redis_client, http_client, settings, limiter, message_id, fields)


async def _process_one(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    limiter: TelegramRateLimiter,
    message_id: Any,
    fields: dict,
) -> None:
    decoded_message_id = _decode(message_id)
    chat_id = _to_int(fields.get(b"chat_id") or fields.get("chat_id"))
    text = _decode(fields.get(b"message_text") or fields.get("message_text"))
    idempotency_key = _decode(fields.get(b"idempotency_key") or fields.get("idempotency_key"))

    if chat_id is None or not text:
        logger.warning(
            "Invalid stream message, acking",
            extra={"event": "invalid_stream_message", "message_id": decoded_message_id},
        )
        await redis_client.xack(settings.redis_stream, settings.redis_group, message_id)
        return

    delivery_count = await _delivery_count(redis_client, settings, decoded_message_id)
    attempt = max(delivery_count, 1)

    if attempt > settings.max_attempts:
        logger.error(
            "Message moved to dead letter",
            extra={
                "event": "dead_letter",
                "message_id": decoded_message_id,
                "chat_id": chat_id,
                "attempt": attempt,
                "max_attempts": settings.max_attempts,
                "idempotency_key": idempotency_key,
            },
        )
        await redis_client.xack(settings.redis_stream, settings.redis_group, message_id)
        return

    await limiter.wait(chat_id)
    ok, retry_after, error = await send_telegram_message(http_client, settings, chat_id, text)

    if ok:
        await redis_client.xack(settings.redis_stream, settings.redis_group, message_id)
        logger.info(
            "Message acknowledged after Telegram send",
            extra={
                "event": "xack_ok",
                "message_id": decoded_message_id,
                "chat_id": chat_id,
                "attempt": attempt,
                "idempotency_key": idempotency_key,
            },
        )
        return

    logger.warning(
        "Message left pending for retry",
        extra={
            "event": "retry_pending",
            "message_id": decoded_message_id,
            "chat_id": chat_id,
            "attempt": attempt,
            "max_attempts": settings.max_attempts,
            "retry_after": retry_after,
            "error": error,
            "idempotency_key": idempotency_key,
        },
    )

    if retry_after:
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(float(retry_after))


async def _delivery_count(redis_client: aioredis.Redis, settings: Settings, message_id: str) -> int:
    try:
        pending = await redis_client.xpending_range(
            settings.redis_stream,
            settings.redis_group,
            min=message_id,
            max=message_id,
            count=1,
        )
    except Exception as exc:
        logger.warning(
            "Could not read delivery count",
            extra={"event": "pending_lookup_failed", "message_id": message_id, "error": str(exc)},
        )
        return 1

    if not pending:
        return 1

    item = pending[0]
    if isinstance(item, dict):
        return int(item.get("times_delivered") or item.get("delivery_count") or 1)
    if len(item) >= 4:
        return int(item[3])
    return 1


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _to_int(value: Any) -> int | None:
    try:
        return int(_decode(value))
    except (TypeError, ValueError):
        return None
