import json
import logging
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import Settings
from security import is_valid_discourse_signature
from templates import build_idempotency_key, render_notification_message

logger = logging.getLogger(__name__)


ENQUEUE_LUA = """
local added = redis.call("SET", KEYS[1], "1", "NX", "EX", ARGV[1])
if not added then
    return {0, ""}
end
local message_id = redis.call(
    "XADD",
    KEYS[2],
    "MAXLEN",
    "~",
    ARGV[2],
    "*",
    "chat_id",
    ARGV[3],
    "message_text",
    ARGV[4],
    "idempotency_key",
    ARGV[5]
)
return {1, message_id}
"""


def create_app(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="Discourse Telegram notification service")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()
        signature = request.headers.get("X-Discourse-Event-Signature", "")

        if not is_valid_discourse_signature(raw_body, signature, settings.webhook_secret):
            logger.warning(
                "Invalid webhook signature",
                extra={"event": "signature_invalid", "has_signature": bool(signature)},
            )
            return JSONResponse({"ok": False}, status_code=401)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid webhook JSON", extra={"event": "invalid_json", "error": str(exc)})
            return JSONResponse({"ok": True})

        notification = payload.get("notification") or {}
        logger.debug(
            "Webhook received",
            extra={
                "event": "webhook_received",
                "notification_type": notification.get("notification_type"),
                "user_id": notification.get("user_id"),
                "topic_id": notification.get("topic_id"),
                "post_number": notification.get("post_number"),
                **_notification_debug_fields(notification, settings),
            },
        )

        try:
            await _handle_notification(notification, redis_client, http_client, settings)
        except Exception as exc:
            logger.exception(
                "Webhook processing failed after signature validation",
                extra={"event": "webhook_processing_error", "error": str(exc)},
            )

        return JSONResponse({"ok": True})

    return app


async def _handle_notification(
    notification: dict[str, Any],
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    message_text, url = render_notification_message(settings.discourse_base_url, notification)
    if not message_text:
        logger.debug(
            "Notification type skipped",
            extra={
                "event": "notification_skipped",
                "notification_type": notification.get("notification_type"),
                **_notification_debug_fields(notification, settings),
            },
        )
        return

    user_id = notification.get("user_id")
    if user_id is None:
        logger.warning("Notification has no user_id", extra={"event": "notification_missing_user_id"})
        return

    chat_id = await _lookup_chat_id(http_client, settings, user_id)
    if chat_id is None:
        logger.debug("No active Telegram link found", extra={"event": "lookup_miss", "user_id": user_id})
        return

    idempotency_key = build_idempotency_key(notification)
    redis_key = f"idem:{idempotency_key}"

    try:
        added, message_id = await redis_client.eval(
            ENQUEUE_LUA,
            2,
            redis_key,
            settings.redis_stream,
            str(settings.idempotency_ttl_seconds),
            str(settings.stream_maxlen),
            str(chat_id),
            message_text,
            idempotency_key,
        )
    except Exception as exc:
        logger.exception(
            "Redis enqueue failed",
            extra={
                "event": "enqueue_failed",
                "user_id": user_id,
                "chat_id": chat_id,
                "idempotency_key": idempotency_key,
                "error": str(exc),
            },
        )
        return

    if int(added) == 0:
        logger.debug(
            "Duplicate notification skipped",
            extra={"event": "duplicate_skipped", "idempotency_key": idempotency_key},
        )
        return

    logger.info(
        "Notification queued",
        extra={
            "event": "queued",
            "message_id": _decode_redis_value(message_id),
            "user_id": user_id,
            "chat_id": chat_id,
            "idempotency_key": idempotency_key,
            "url": url,
        },
    )


async def _lookup_chat_id(http_client: httpx.AsyncClient, settings: Settings, user_id: int) -> int | None:
    url = f"{settings.supabase_url}/rest/v1/{settings.discourse_links_table}"
    headers = {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
    }
    params = {
        "discourse_user_id": f"eq.{user_id}",
        "is_active": "eq.true",
        "select": "chat_id",
        "limit": "1",
    }

    try:
        response = await http_client.get(
            url,
            headers=headers,
            params=params,
            timeout=settings.supabase_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("Supabase lookup failed", extra={"event": "lookup_failed", "user_id": user_id, "error": str(exc)})
        return None

    if not data:
        return None

    chat_id = data[0].get("chat_id")
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        logger.warning(
            "Supabase returned invalid chat_id",
            extra={"event": "lookup_invalid_chat_id", "user_id": user_id, "chat_id": chat_id},
        )
        return None


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _notification_debug_fields(notification: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if not settings.log_notification_data:
        return {}

    data = notification.get("data") or {}
    return {
        "data_keys": sorted(data.keys()),
        "notification_data": data,
    }
