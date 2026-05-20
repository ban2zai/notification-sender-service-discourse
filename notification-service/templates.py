from html import escape
from typing import Any

from discourse import build_excerpt


NO_POST_URL_TYPES = {6, 17}


def build_topic_url(base_url: str, notification_type: int, topic_id: object, post_number: object) -> str:
    base_topic_url = f"{base_url.rstrip('/')}/t/{topic_id or ''}"
    if notification_type in NO_POST_URL_TYPES:
        return base_topic_url
    return f"{base_topic_url}/{post_number or ''}"


def render_notification_message(
    base_url: str,
    notification: dict[str, Any],
    event_kind: str,
    enriched: dict[str, Any] | None,
    excerpt_max_chars: int,
) -> tuple[str, str]:
    enriched = enriched or {}
    data = notification.get("data") or {}
    topic = enriched.get("topic") or {}
    post = enriched.get("post") or {}

    notification_type = int(notification.get("notification_type") or 0)
    post_number = post.get("post_number") or notification.get("post_number", "")
    url = build_topic_url(
        base_url=base_url,
        notification_type=notification_type,
        topic_id=notification.get("topic_id", ""),
        post_number=post_number,
    )

    title = _html(topic.get("title") or data.get("topic_title") or notification.get("fancy_title") or "тема")
    actor = _actor_html(base_url, enriched, data)
    category = _html(enriched.get("category") or "не указана")
    tags = _format_tags(topic.get("tags") or [])
    excerpt = _html(build_excerpt(post, excerpt_max_chars)) if event_kind != "private_message" else ""
    safe_url = _html(url)

    if event_kind == "new_topic":
        return _with_optional_excerpt(
            f"Новая тема: <b>«{title}»</b>\n"
            f"Категория: <b>{category}</b>\n"
            f"Теги: <b>{tags}</b>",
            excerpt,
            safe_url,
        ), url

    if event_kind == "new_post":
        return _with_optional_excerpt(
            f"<b>{actor}</b> ответил в теме <b>«{title}»</b>",
            excerpt,
            safe_url,
        ), url

    if event_kind == "tz_approval":
        action = str(data.get("action") or "").strip()
        action_text = {
            "approved": "одобрил ТЗ в теме",
            "unapproved": "снял одобрение с ТЗ в теме",
        }.get(action, "изменил одобрение ТЗ в теме")
        return f"<b>{actor}</b> {action_text} <b>«{title}»</b>\n{safe_url}", url

    headers = {
        "mention": f"<b>{actor}</b> упомянул вас в теме <b>«{title}»</b>",
        "reply": f"<b>{actor}</b> ответил в теме <b>«{title}»</b>",
        "quote": f"<b>{actor}</b> процитировал вас в теме <b>«{title}»</b>",
        "edit": f"<b>{actor}</b> отредактировал пост в теме <b>«{title}»</b>",
        "private_message": f"<b>{actor}</b> написал личное сообщение: <b>«{title}»</b>",
        "group_mention": f"<b>{actor}</b> упомянул группу в теме <b>«{title}»</b>",
    }
    header = headers.get(event_kind, f"Уведомление в теме <b>«{title}»</b>")
    return _with_optional_excerpt(header, excerpt, safe_url), url


def render_fallback_message(base_url: str, notification: dict[str, Any], event_kind: str) -> tuple[str, str]:
    data = notification.get("data") or {}
    notification_type = int(notification.get("notification_type") or 0)
    url = build_topic_url(
        base_url=base_url,
        notification_type=notification_type,
        topic_id=notification.get("topic_id", ""),
        post_number=notification.get("post_number", ""),
    )
    title = _html(data.get("topic_title") or notification.get("fancy_title") or "тема")
    label = {
        "new_topic": "Новая тема",
        "new_post": "Новый пост",
        "mention": "Упоминание",
        "reply": "Ответ",
        "quote": "Цитата",
        "edit": "Редактирование",
        "private_message": "Личное сообщение",
        "group_mention": "Упоминание группы",
        "tz_approval": "Одобрение ТЗ",
    }.get(event_kind, "Уведомление")
    return f"{label}: <b>«{title}»</b>\n{_html(url)}", url


def _with_optional_excerpt(header: str, excerpt: str, url: str) -> str:
    if excerpt:
        return f"{header}\n\n<blockquote>{excerpt}</blockquote>\n{url}"
    return f"{header}\n{url}"


def _format_tags(tags: list[Any]) -> str:
    if not tags:
        return "нет"

    labels = []
    for tag in tags:
        if isinstance(tag, dict):
            label = tag.get("name") or tag.get("slug") or ""
        else:
            label = str(tag or "")
        if label:
            labels.append(_html(label))

    return ", ".join(labels) if labels else "нет"


def _html(value: object) -> str:
    return escape(str(value or ""), quote=False)


def _html_attr(value: object) -> str:
    return escape(str(value or ""), quote=True)


def _actor_html(base_url: str, enriched: dict[str, Any], data: dict[str, Any]) -> str:
    username = _actor_username(enriched, data)
    if username == "anonuser":
        return "[anonuser]"
    if not username.startswith("@"):
        return _html(username)

    clean_username = username[1:]
    href = f"{base_url.rstrip('/')}/u/{clean_username}/summary"
    return f'<a href="{_html_attr(href)}">{_html(username)}</a>'


def _actor_username(enriched: dict[str, Any], data: dict[str, Any]) -> str:
    if enriched.get("is_anonymous"):
        return "anonuser"
    if enriched.get("actor_username"):
        return str(enriched["actor_username"])
    if enriched.get("actor_lookup_failed"):
        return "anonuser"
    if data.get("display_username") and str(data.get("display_username")) != "system":
        username = str(data.get("display_username") or "").strip()
        return username if username.startswith("@") else f"@{username}"
    username = str(data.get("original_username") or "").strip()
    if username:
        return username if username.startswith("@") else f"@{username}"
    return str(data.get("display_username") or "кто-то")
