import unittest

from discourse import build_excerpt
from events import build_idempotency_key, classify_event
from templates import build_topic_url, render_fallback_message, render_notification_message


class EventTests(unittest.TestCase):
    def test_classifies_new_topic_for_9_17_36(self):
        for notification_type in (9, 17, 36):
            self.assertEqual(
                classify_event({"notification_type": notification_type, "post_number": 1}),
                "new_topic",
            )

    def test_classifies_new_post(self):
        self.assertEqual(classify_event({"notification_type": 36, "post_number": 3}), "new_post")

    def test_new_topic_idempotency_dedupes_notification_types(self):
        keys = {
            build_idempotency_key(
                {"notification_type": notification_type, "topic_id": 456, "post_number": 1, "user_id": 123},
                "new_topic",
            )
            for notification_type in (9, 17, 36)
        }

        self.assertEqual(keys, {"new_topic:456:123"})


class TemplateTests(unittest.TestCase):
    def test_post_url_contains_post_number_for_regular_notifications(self):
        self.assertEqual(
            build_topic_url("https://forum.example.ru", 2, 456, 7),
            "https://forum.example.ru/t/456/7",
        )

    def test_no_post_url_for_private_message(self):
        self.assertEqual(
            build_topic_url("https://forum.example.ru", 6, 456, 7),
            "https://forum.example.ru/t/456",
        )

    def test_render_new_topic_message_with_enrichment(self):
        message, url = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 36,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 1,
                "data": {"topic_title": "A & B", "original_username": "user"},
            },
            "new_topic",
            {
                "topic": {
                    "title": "A & B",
                    "tags": [
                        {"id": 1, "name": "ЗГУ", "slug": "zgu"},
                        {"id": 2, "name": "Программирование-БГУ", "slug": "programmirovanie-bgu"},
                    ],
                },
                "post": {"raw": "Текст первого поста"},
                "category": "Обсуждения",
            },
            400,
        )

        self.assertEqual(url, "https://forum.example.ru/t/456/1")
        self.assertIn("Новая тема", message)
        self.assertIn("<b>A &amp; B</b>", message)
        self.assertIn("<b>Обсуждения</b>", message)
        self.assertIn("ЗГУ, Программирование-БГУ", message)
        self.assertIn("<pre>Текст первого поста</pre>", message)
        self.assertIn("\nhttps://forum.example.ru/t/456/1", message)

    def test_render_private_message_without_excerpt(self):
        message, _ = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 6,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 1,
                "data": {"topic_title": "PM", "original_username": "user"},
            },
            "private_message",
            {"topic": {"title": "PM"}, "post": {"raw": "secret"}, "category": ""},
            400,
        )

        self.assertNotIn("<pre>", message)
        self.assertNotIn("secret", message)

    def test_render_anonymous_actor(self):
        message, _ = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 2,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 2,
                "data": {"topic_title": "Anon", "original_username": "real_user"},
            },
            "reply",
            {
                "topic": {"title": "Anon"},
                "post": {"raw": "text"},
                "category": "",
                "is_anonymous": True,
                "actor_username": "anonuser",
            },
            400,
        )

        self.assertIn("<b>anonuser</b>", message)
        self.assertNotIn("real_user", message)

    def test_render_unknown_actor_after_lookup_failure_safely(self):
        message, _ = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 2,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 2,
                "data": {"topic_title": "Unknown", "original_username": "real_user"},
            },
            "reply",
            {
                "topic": {"title": "Unknown"},
                "post": {},
                "category": "",
                "actor_lookup_failed": True,
            },
            400,
        )

        self.assertIn("<b>anonuser</b>", message)
        self.assertNotIn("real_user", message)

    def test_render_fallback_message(self):
        message, url = render_fallback_message(
            "https://forum.example.ru",
            {
                "notification_type": 36,
                "topic_id": 456,
                "post_number": 1,
                "data": {"topic_title": "Fallback"},
            },
            "new_topic",
        )

        self.assertEqual(url, "https://forum.example.ru/t/456/1")
        self.assertIn("Fallback", message)
        self.assertIn("\nhttps://forum.example.ru/t/456/1", message)

    def test_excerpt_cleanup_and_truncation(self):
        self.assertEqual(build_excerpt({"raw": "a\n\n b\tc"}, 100), "a b c")
        self.assertEqual(build_excerpt({"raw": "123456789"}, 6), "12345…")


if __name__ == "__main__":
    unittest.main()
