NOTIFICATION_MAP = {
    1: {
        "enabled": True,
        "template": '👤 *{username}* упомянул тебя в теме *"{title}"*\n{url}',
    },
    2: {
        "enabled": True,
        "template": '💬 *{username}* ответил на твой пост в теме *"{title}"*\n{url}',
    },
    4: {
        "enabled": True,
        "template": '✏️ *{username}* отредактировал пост в теме *"{title}"*\n{url}',
    },
    6: {
        "enabled": True,
        "template": '✉️ *{username}* написал личное сообщение: *"{title}"*\n{url}',
    },
    9: {
        "enabled": True,
        "template": '📌 Новый пост в теме *"{title}"*\n{url}',
    },
    17: {
        "enabled": True,
        "template": '🆕 Новая тема по твоему тегу: *"{title}"*\n{url}',
    },
    36: {
        "enabled": True,
        "template": '🆕 Новая тема или пост в отслеживаемом разделе/теге: *"{title}"*\n{url}',
    },
}

NO_POST_URL_TYPES = {6, 17}


def escape_md(text: str) -> str:
    return (
        str(text or "")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def build_topic_url(base_url: str, notification_type: int, topic_id: object, post_number: object) -> str:
    base_topic_url = f"{base_url.rstrip('/')}/t/{topic_id or ''}"
    if notification_type in NO_POST_URL_TYPES:
        return base_topic_url
    return f"{base_topic_url}/{post_number or ''}"


def render_notification_message(base_url: str, notification: dict) -> tuple[str | None, str | None]:
    notification_type = notification.get("notification_type")
    template_config = NOTIFICATION_MAP.get(notification_type)
    if not template_config or not template_config.get("enabled"):
        return None, None

    data = notification.get("data") or {}
    url = build_topic_url(
        base_url=base_url,
        notification_type=notification_type,
        topic_id=notification.get("topic_id", ""),
        post_number=notification.get("post_number", ""),
    )
    username = escape_md(data.get("original_username", "кто-то"))
    title = escape_md(data.get("topic_title", "тема"))

    return (
        template_config["template"]
        .replace("{username}", username)
        .replace("{title}", title)
        .replace("{url}", url),
        url,
    )


def build_idempotency_key(notification: dict) -> str:
    return "{notif_type}_{topic_id}_{post_num}_{user_id}".format(
        notif_type=notification.get("notification_type", ""),
        topic_id=notification.get("topic_id", ""),
        post_num=notification.get("post_number", ""),
        user_id=notification.get("user_id", ""),
    )
