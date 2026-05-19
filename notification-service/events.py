from typing import Any


NEW_TOPIC_TYPES = {9, 17, 36}
NEW_POST_TYPES = {9, 36}
SUPPORTED_NOTIFICATION_TYPES = {1, 2, 3, 4, 6, 9, 15, 17, 36}


def classify_event(notification: dict[str, Any]) -> str | None:
    notification_type = _to_int(notification.get("notification_type"))
    post_number = _to_int(notification.get("post_number"))

    if notification_type not in SUPPORTED_NOTIFICATION_TYPES:
        return None

    if notification_type in NEW_TOPIC_TYPES and post_number == 1:
        return "new_topic"
    if notification_type in NEW_POST_TYPES and post_number and post_number > 1:
        return "new_post"

    return {
        1: "mention",
        2: "reply",
        3: "quote",
        4: "edit",
        6: "private_message",
        15: "group_mention",
        17: "new_topic",
        36: "new_topic",
    }.get(notification_type)


def build_idempotency_key(notification: dict[str, Any], event_kind: str) -> str:
    topic_id = notification.get("topic_id", "")
    post_number = notification.get("post_number", "")
    user_id = notification.get("user_id", "")

    if event_kind == "new_topic":
        return f"new_topic:{topic_id}:{user_id}"
    if event_kind == "new_post":
        return f"new_post:{topic_id}:{post_number}:{user_id}"

    notification_id = notification.get("id")
    if notification_id:
        return f"{event_kind}:{notification_id}:{user_id}"

    return f"{event_kind}:{topic_id}:{post_number}:{user_id}"


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
