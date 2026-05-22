from __future__ import annotations

import html
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from telegram import send_telegram_message

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config import Settings
    from link_cache import TelegramLinkCache

logger = logging.getLogger(__name__)

TOKEN_KEY_PREFIX = "telegram_link_token:"


async def create_link_token(
    redis_client: aioredis.Redis,
    settings: Settings,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    chat_id = _parse_chat_id(payload.get("chat_id"))
    if chat_id is None:
        return 400, {"ok": False, "error": "invalid_chat_id"}

    token = secrets.token_urlsafe(32)
    redis_payload = json.dumps({"chat_id": chat_id}, ensure_ascii=False, separators=(",", ":"))
    await redis_client.set(
        _token_key(token),
        redis_payload,
        ex=settings.account_link_token_ttl_seconds,
    )

    url = f"{settings.discourse_base_url}/link-telegram?token={quote(token)}"
    return 200, {"ok": True, "url": url, "expires_in": settings.account_link_token_ttl_seconds}


async def finalize_account_link(
    redis_client: aioredis.Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
    link_cache: TelegramLinkCache,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    validated = _validate_account_link_payload(payload)
    if not validated["ok"]:
        return 400, {"ok": False, "error": validated["error"]}

    token = validated["token"]
    token_data = await _read_token(redis_client, token)
    if token_data is None:
        return 410, {"ok": False, "error": "link_expired"}

    try:
        chat_id = _parse_chat_id(token_data.get("chat_id"))
        if chat_id is None:
            return 410, {"ok": False, "error": "link_expired"}

        discourse_user_id = validated["discourse_user_id"]
        discourse_username = validated["discourse_username"]
        email = validated["email"]
        linked_at = validated["linked_at"]

        existing_chat_id = await _find_chat_id_for_discourse_user(http_client, settings, discourse_user_id)
        existing_discourse_id = await _find_discourse_user_for_chat_id(http_client, settings, chat_id)

        if existing_chat_id is not None and existing_chat_id != chat_id:
            return 409, {"ok": False, "error": "discourse_conflict"}

        if existing_discourse_id is not None and existing_discourse_id != discourse_user_id:
            return 409, {"ok": False, "error": "telegram_conflict"}

        if existing_chat_id == chat_id or existing_discourse_id == discourse_user_id:
            return 200, {"ok": True, "status": "already_linked"}

        await _upsert_telegram_user(http_client, settings, chat_id)
        await _upsert_account_link(
            http_client,
            settings,
            chat_id=chat_id,
            discourse_user_id=discourse_user_id,
            discourse_username=discourse_username,
            email=email,
            linked_at=linked_at,
        )
        await _notify_link_success(http_client, settings, chat_id, discourse_username)
        await link_cache.remember(discourse_user_id, chat_id)

        logger.info(
            "Telegram account linked",
            extra={
                "event": "account_linked",
                "chat_id": chat_id,
                "discourse_user_id": discourse_user_id,
            },
        )
        return 200, {"ok": True, "status": "linked"}
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 409:
            return 409, {"ok": False, "error": "link_conflict"}

        logger.warning(
            "Supabase account link request failed",
            extra={
                "event": "account_link_supabase_failed",
                "http_status": status_code,
                "body_preview": exc.response.text[:300],
            },
        )
        return 502, {"ok": False, "error": "supabase_error"}
    except Exception as exc:
        logger.exception(
            "Account link finalization failed",
            extra={"event": "account_link_failed", "error": str(exc)},
        )
        return 502, {"ok": False, "error": "account_link_failed"}
    finally:
        await redis_client.delete(_token_key(token))


async def _read_token(redis_client: aioredis.Redis, token: str) -> dict[str, Any] | None:
    raw_value = await redis_client.get(_token_key(token))
    if raw_value is None:
        return None

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode()

    try:
        data = json.loads(raw_value)
    except (TypeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    return data


async def _find_chat_id_for_discourse_user(
    http_client: httpx.AsyncClient,
    settings: Settings,
    discourse_user_id: int,
) -> int | None:
    rows = await _select_links(
        http_client,
        settings,
        {
            "discourse_user_id": f"eq.{discourse_user_id}",
            "is_active": "eq.true",
            "select": "chat_id",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return _parse_chat_id(rows[0].get("chat_id"))


async def _find_discourse_user_for_chat_id(
    http_client: httpx.AsyncClient,
    settings: Settings,
    chat_id: int,
) -> int | None:
    rows = await _select_links(
        http_client,
        settings,
        {
            "chat_id": f"eq.{chat_id}",
            "is_active": "eq.true",
            "select": "discourse_user_id",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return _parse_positive_int(rows[0].get("discourse_user_id"))


async def _select_links(
    http_client: httpx.AsyncClient,
    settings: Settings,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    response = await http_client.get(
        f"{settings.supabase_url}/rest/v1/{settings.discourse_links_table}",
        headers=_supabase_headers(settings),
        params=params,
        timeout=settings.supabase_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


async def _upsert_telegram_user(
    http_client: httpx.AsyncClient,
    settings: Settings,
    chat_id: int,
) -> None:
    response = await http_client.post(
        f"{settings.supabase_url}/rest/v1/{settings.telegram_users_table}",
        headers=_supabase_headers(settings, prefer="resolution=merge-duplicates"),
        json={"chat_id": chat_id},
        timeout=settings.supabase_timeout_seconds,
    )
    response.raise_for_status()


async def _upsert_account_link(
    http_client: httpx.AsyncClient,
    settings: Settings,
    *,
    chat_id: int,
    discourse_user_id: int,
    discourse_username: str,
    email: str,
    linked_at: str,
) -> None:
    response = await http_client.post(
        f"{settings.supabase_url}/rest/v1/{settings.discourse_links_table}",
        headers=_supabase_headers(settings, prefer="resolution=merge-duplicates"),
        json={
            "chat_id": chat_id,
            "discourse_user_id": discourse_user_id,
            "discourse_username": discourse_username,
            "email": email,
            "linked_at": linked_at,
            "is_active": True,
        },
        timeout=settings.supabase_timeout_seconds,
    )
    response.raise_for_status()


async def _notify_link_success(
    http_client: httpx.AsyncClient,
    settings: Settings,
    chat_id: int,
    discourse_username: str,
) -> None:
    text = (
        "✅ <b>Аккаунт успешно привязан!</b>\n\n"
        "Теперь уведомления с форума будут приходить сюда.\n"
        f"Привязан к аккаунту: <code>{html.escape(discourse_username)}</code>"
    )
    reply_markup = {
        "inline_keyboard": [[{"text": "⚙️ Открыть настройки", "callback_data": "/settings"}]],
    }
    ok, _, error = await send_telegram_message(http_client, settings, chat_id, text, reply_markup=reply_markup)
    if not ok:
        raise RuntimeError(f"Telegram success notification failed: {error}")


def _validate_account_link_payload(payload: dict[str, Any]) -> dict[str, Any]:
    token = payload.get("token")
    if not isinstance(token, str) or not token.strip():
        return {"ok": False, "error": "invalid_token"}

    discourse_user_id = _parse_positive_int(payload.get("discourse_user_id"))
    if discourse_user_id is None:
        return {"ok": False, "error": "invalid_discourse_user_id"}

    discourse_username = payload.get("discourse_username")
    if not isinstance(discourse_username, str) or not discourse_username.strip():
        return {"ok": False, "error": "invalid_discourse_username"}

    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        return {"ok": False, "error": "invalid_email"}

    linked_at = payload.get("linked_at")
    if not isinstance(linked_at, str) or not linked_at.strip():
        linked_at = datetime.now(UTC).isoformat()

    return {
        "ok": True,
        "token": token.strip(),
        "discourse_user_id": discourse_user_id,
        "discourse_username": discourse_username.strip(),
        "email": email.strip(),
        "linked_at": linked_at.strip(),
    }


def _parse_chat_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        chat_id = value
    elif isinstance(value, str) and value.strip():
        try:
            chat_id = int(value.strip())
        except ValueError:
            return None
    else:
        return None

    if chat_id == 0:
        return None
    return chat_id


def _parse_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if parsed <= 0:
        return None
    return parsed


def _supabase_headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _token_key(token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}{token}"
