from __future__ import annotations

import json
import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from config import Settings
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_QUOTE_OPEN_RE = re.compile(r"\[quote(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\]]*))?\s*\]", re.IGNORECASE)
_QUOTE_CLOSE_RE = re.compile(r"\[/quote\s*\]", re.IGNORECASE)
_HTML_TEXT_BREAK_TAGS = {
    "aside",
    "blockquote",
    "br",
    "div",
    "li",
    "ol",
    "p",
    "pre",
    "tr",
    "ul",
}


async def enrich_notification(
    redis_client: "aioredis.Redis",
    http_client: httpx.AsyncClient,
    settings: Settings,
    notification: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    topic_id = notification.get("topic_id")
    post_id = (notification.get("data") or {}).get("original_post_id")

    enriched: dict[str, Any] = {
        "topic": {},
        "post": {},
        "category": "",
        "actor_username": "",
        "actor_lookup_failed": False,
        "is_anonymous": False,
    }
    had_error = False

    if topic_id:
        topic, topic_error = await _get_cached_json(
            redis_client,
            http_client,
            settings,
            cache_key=f"discourse:topic:{topic_id}",
            ttl_seconds=settings.discourse_topic_cache_ttl_seconds,
            url=f"{settings.discourse_base_url}/t/{topic_id}.json",
        )
        enriched["topic"] = topic or {}
        had_error = had_error or topic_error

    post_error = False
    if post_id:
        post, post_error = await _get_cached_json(
            redis_client,
            http_client,
            settings,
            cache_key=f"discourse:post:{post_id}",
            ttl_seconds=settings.discourse_post_cache_ttl_seconds,
            url=f"{settings.discourse_base_url}/posts/{post_id}.json",
        )
        enriched["post"] = post or {}
        had_error = had_error or post_error
        enriched["actor_lookup_failed"] = post_error

    category_id = enriched["topic"].get("category_id")
    if category_id:
        categories, categories_error = await _get_categories(redis_client, http_client, settings)
        enriched["category"] = categories.get(str(category_id), "")
        had_error = had_error or categories_error

    topic_post = _find_topic_post(enriched["topic"], post_id, notification.get("post_number"))
    actor_source = enriched["post"] or topic_post or {}
    is_anonymous = _is_anonymous(notification.get("data") or {}, actor_source, topic_post)
    enriched["is_anonymous"] = is_anonymous
    enriched["actor_username"] = "anonuser" if is_anonymous else _display_username(actor_source)

    return enriched, had_error


def build_excerpt(post: dict[str, Any], max_chars: int) -> str:
    if post.get("raw"):
        text = sanitize_quote_attribution(str(post.get("raw")))
    else:
        text = sanitize_quote_attribution(_cooked_to_text_without_quote_attribution(post.get("cooked") or ""))
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def sanitize_quote_attribution(text: str) -> str:
    text = _QUOTE_OPEN_RE.sub(" ", str(text))
    return _QUOTE_CLOSE_RE.sub(" ", text)


def _find_topic_post(topic: dict[str, Any], post_id: Any, post_number: Any) -> dict[str, Any]:
    posts = (topic.get("post_stream") or {}).get("posts") or []
    for post in posts:
        if _values_equal(post.get("id"), post_id):
            return post
    for post in posts:
        if _values_equal(post.get("post_number"), post_number):
            return post
    return {}


def _is_anonymous(*sources: dict[str, Any]) -> bool:
    for source in sources:
        if _truthy(source.get("is_anonymous_post")) or _truthy(source.get("is_anonymous_topic")):
            return True
    return False


def _display_username(source: dict[str, Any]) -> str:
    username = str(source.get("username") or "").strip()
    if username:
        return username if username.startswith("@") else f"@{username}"
    return str(source.get("display_username") or source.get("name") or "")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


async def _get_categories(
    redis_client: "aioredis.Redis",
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> tuple[dict[str, str], bool]:
    data, had_error = await _get_cached_json(
        redis_client,
        http_client,
        settings,
        cache_key="discourse:categories",
        ttl_seconds=settings.discourse_categories_cache_ttl_seconds,
        url=f"{settings.discourse_base_url}/categories.json",
    )
    category_list = (data or {}).get("category_list", {}).get("categories", [])
    return {str(item.get("id")): item.get("name", "") for item in category_list}, had_error


async def _get_cached_json(
    redis_client: "aioredis.Redis",
    http_client: httpx.AsyncClient,
    settings: Settings,
    cache_key: str,
    ttl_seconds: int,
    url: str,
) -> tuple[dict[str, Any] | None, bool]:
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode()
            return json.loads(cached), False
    except Exception as exc:
        logger.warning("Discourse cache read failed", extra={"event": "discourse_cache_read_failed", "error": str(exc)})

    try:
        response = await http_client.get(
            url,
            headers={
                "Api-Key": settings.discourse_api_key,
                "Api-Username": settings.discourse_api_username,
            },
            timeout=settings.discourse_api_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("Discourse API request failed", extra={"event": "discourse_api_failed", "url": url, "error": str(exc)})
        return None, True

    try:
        await redis_client.set(cache_key, json.dumps(data, ensure_ascii=False), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("Discourse cache write failed", extra={"event": "discourse_cache_write_failed", "error": str(exc)})

    return data, False


def _cooked_to_text_without_quote_attribution(value: str) -> str:
    parser = _CookedQuoteTextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    return unescape("".join(parser.parts))


class _CookedQuoteTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._quote_aside_depth = 0
        self._aside_quote_stack: list[bool] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth += 1
            return

        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag == "aside":
            is_quote_aside = _has_class(attr_map.get("class", ""), "quote")
            self._aside_quote_stack.append(is_quote_aside)
            if is_quote_aside:
                self._quote_aside_depth += 1

        if self._quote_aside_depth and tag == "div" and _has_class(attr_map.get("class", ""), "title"):
            self._skip_depth = 1
            return

        if tag in _HTML_TEXT_BREAK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return

        if tag == "aside" and self._aside_quote_stack:
            if self._aside_quote_stack.pop():
                self._quote_aside_depth = max(0, self._quote_aside_depth - 1)

        if tag in _HTML_TEXT_BREAK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _has_class(value: str, class_name: str) -> bool:
    return class_name in {item.strip().lower() for item in value.split()}
