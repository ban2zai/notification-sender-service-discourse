from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from account_link import create_link_token, finalize_account_link
from events import build_idempotency_key, classify_event
from link_cache import TelegramLinkCache
from security import is_valid_bearer_token, is_valid_discourse_signature

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config import Settings

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
    "event_kind",
    ARGV[4],
    "notification_type",
    ARGV[5],
    "idempotency_key",
    ARGV[6],
    "notification_json",
    ARGV[7]
)
return {1, message_id}
"""


def create_app(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    link_cache: TelegramLinkCache,
) -> FastAPI:
    app = FastAPI(title="Discourse Telegram notification service")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/telegram/link-token")
    async def telegram_link_token(request: Request) -> JSONResponse:
        if not _is_valid_account_link_auth(request, settings):
            return JSONResponse({"ok": False}, status_code=401)

        payload = await _read_json_body(request)
        if payload is None:
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

        status_code, response = await create_link_token(redis_client, settings, payload)
        return JSONResponse(response, status_code=status_code)

    @app.post("/telegram/account-link")
    async def telegram_account_link(request: Request) -> JSONResponse:
        if not _is_valid_account_link_auth(request, settings):
            return JSONResponse({"ok": False}, status_code=401)

        payload = await _read_json_body(request)
        if payload is None:
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

        status_code, response = await finalize_account_link(redis_client, http_client, settings, link_cache, payload)
        return JSONResponse(response, status_code=status_code)

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
            await _handle_notification(notification, redis_client, settings, link_cache)
        except Exception as exc:
            logger.exception(
                "Webhook processing failed after signature validation",
                extra={"event": "webhook_processing_error", "error": str(exc)},
            )

        return JSONResponse({"ok": True})

    return app


def _is_valid_account_link_auth(request: Request, settings: Settings) -> bool:
    return is_valid_bearer_token(
        request.headers.get("Authorization", ""),
        settings.account_link_api_token,
    )


async def _read_json_body(request: Request) -> dict[str, Any] | None:
    try:
        payload = await request.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


async def _handle_notification(
    notification: dict[str, Any],
    redis_client: aioredis.Redis,
    settings: Settings,
    link_cache: TelegramLinkCache,
) -> None:
    event_kind = classify_event(notification)
    if not event_kind:
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
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        logger.warning(
            "Notification has invalid user_id",
            extra={"event": "notification_invalid_user_id", "user_id": user_id},
        )
        return

    chat_id = await link_cache.lookup(user_id_int)
    if chat_id is None:
        logger.debug("No active Telegram link found", extra={"event": "lookup_miss", "user_id": user_id})
        return

    idempotency_key = build_idempotency_key(notification, event_kind)
    redis_key = f"idem:{idempotency_key}"
    notification_json = json.dumps(notification, ensure_ascii=False, separators=(",", ":"))

    try:
        added, message_id = await redis_client.eval(
            ENQUEUE_LUA,
            2,
            redis_key,
            settings.redis_stream,
            str(settings.idempotency_ttl_seconds),
            str(settings.stream_maxlen),
            str(chat_id),
            event_kind,
            str(notification.get("notification_type", "")),
            idempotency_key,
            notification_json,
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
            "event_kind": event_kind,
        },
    )

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
