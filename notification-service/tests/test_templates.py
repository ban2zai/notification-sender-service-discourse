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

    def test_classifies_tz_approval(self):
        self.assertEqual(classify_event({"notification_type": 167, "post_number": 10}), "tz_approval")

    def test_new_topic_idempotency_dedupes_notification_types(self):
        keys = {
            build_idempotency_key(
                {"notification_type": notification_type, "topic_id": 456, "post_number": 1, "user_id": 123},
                "new_topic",
            )
            for notification_type in (9, 17, 36)
        }

        self.assertEqual(keys, {"new_topic:456:123"})

    def test_reply_idempotency_uses_notification_id(self):
        first = build_idempotency_key(
            {"id": 5101, "notification_type": 2, "topic_id": 534, "post_number": 2, "user_id": 1},
            "reply",
        )
        second = build_idempotency_key(
            {"id": 5102, "notification_type": 2, "topic_id": 534, "post_number": 2, "user_id": 1},
            "reply",
        )

        self.assertEqual(first, "reply:5101:1")
        self.assertEqual(second, "reply:5102:1")
        self.assertNotEqual(first, second)

    def test_reply_idempotency_falls_back_without_notification_id(self):
        key = build_idempotency_key(
            {"notification_type": 2, "topic_id": 534, "post_number": 2, "user_id": 1},
            "reply",
        )

        self.assertEqual(key, "reply:534:2:1")

    def test_tz_approval_idempotency_uses_notification_id(self):
        key = build_idempotency_key(
            {"id": 5269, "notification_type": 167, "topic_id": 535, "post_number": 10, "user_id": 537},
            "tz_approval",
        )

        self.assertEqual(key, "tz_approval:5269:537")


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
        self.assertIn("<b>«A &amp; B»</b>", message)
        self.assertIn("<b>Обсуждения</b>", message)
        self.assertIn("ЗГУ, Программирование-БГУ", message)
        self.assertIn("<blockquote>Текст первого поста</blockquote>", message)
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

        self.assertNotIn("<blockquote>", message)
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

        self.assertIn("<b>[anonuser]</b>", message)
        self.assertNotIn("real_user", message)

    def test_render_excerpt_removes_double_quoted_quote_author(self):
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
                "post": {"raw": '[quote="real_user, post:1, topic:123"]текст цитаты[/quote]ответ'},
                "category": "",
                "is_anonymous": True,
                "actor_username": "anonuser",
            },
            400,
        )

        self.assertNotIn("real_user", message)
        self.assertNotIn("[quote", message)
        self.assertNotIn("[/quote]", message)
        self.assertNotIn("текст цитаты", message)
        self.assertIn("<blockquote>ответ</blockquote>", message)

    def test_render_excerpt_removes_single_quoted_quote_author(self):
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
                "post": {"raw": "[quote='real_user, post:1, topic:123']текст цитаты[/quote]ответ"},
                "category": "",
                "is_anonymous": True,
                "actor_username": "anonuser",
            },
            400,
        )

        self.assertNotIn("real_user", message)
        self.assertNotIn("[quote", message)
        self.assertNotIn("[/quote]", message)
        self.assertNotIn("текст цитаты", message)
        self.assertIn("<blockquote>ответ</blockquote>", message)

    def test_render_actor_username_with_at_sign(self):
        message, _ = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 2,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 2,
                "data": {"topic_title": "Reply", "original_username": "fallback_user"},
            },
            "reply",
            {
                "topic": {"title": "Reply"},
                "post": {"raw": "text"},
                "category": "",
                "actor_username": "@calayx",
            },
            400,
        )

        self.assertIn('<b><a href="https://forum.example.ru/u/calayx/summary">@calayx</a></b>', message)
        self.assertNotIn("fallback_user", message)

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

        self.assertIn("<b>[anonuser]</b>", message)
        self.assertNotIn("real_user", message)

    def test_render_new_post_like_reply_without_category_tags(self):
        message, url = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 36,
                "user_id": 123,
                "topic_id": 531,
                "post_number": 5,
                "data": {"topic_title": "Анонимуз", "original_username": "fallback_user"},
            },
            "new_post",
            {
                "topic": {"title": "Анонимуз", "tags": [{"name": "БГУ"}]},
                "post": {"raw": "Ответ 1"},
                "category": "Обсуждения",
                "actor_username": "@calayx",
            },
            400,
        )

        self.assertEqual(url, "https://forum.example.ru/t/531/5")
        self.assertIn('<b><a href="https://forum.example.ru/u/calayx/summary">@calayx</a></b>', message)
        self.assertIn("ответил в теме", message)
        self.assertIn("<b>«Анонимуз»</b>", message)
        self.assertIn("<blockquote>Ответ 1</blockquote>", message)
        self.assertNotIn("Категория", message)
        self.assertNotIn("Теги", message)

    def test_render_reply_url_uses_enriched_post_number(self):
        message, url = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 2,
                "user_id": 123,
                "topic_id": 534,
                "post_number": 10,
                "data": {"topic_title": "Form", "original_username": "fallback_user"},
            },
            "reply",
            {
                "topic": {"title": "Form"},
                "post": {"raw": "Ответ 6", "post_number": 11},
                "category": "",
                "actor_username": "@AleskerovTI",
            },
            400,
        )

        self.assertEqual(url, "https://forum.example.ru/t/534/11")
        self.assertIn("\nhttps://forum.example.ru/t/534/11", message)
        self.assertNotIn("https://forum.example.ru/t/534/10", message)

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
        self.assertIn("<b>«Fallback»</b>", message)
        self.assertIn("\nhttps://forum.example.ru/t/456/1", message)

    def test_render_tz_approval_approved(self):
        message, url = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 167,
                "user_id": 537,
                "topic_id": 535,
                "post_number": 11,
                "data": {
                    "action": "approved",
                    "display_username": "calayx",
                    "topic_title": "ТЗ",
                },
            },
            "tz_approval",
            {"topic": {}, "post": {}, "category": ""},
            400,
        )

        self.assertEqual(url, "https://forum.example.ru/t/535/11")
        self.assertIn('<b><a href="https://forum.example.ru/u/calayx/summary">@calayx</a></b>', message)
        self.assertIn("одобрил ТЗ в теме", message)
        self.assertIn("<b>«ТЗ»</b>", message)
        self.assertIn("\nhttps://forum.example.ru/t/535/11", message)

    def test_render_tz_approval_unapproved(self):
        message, _ = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 167,
                "user_id": 537,
                "topic_id": 535,
                "post_number": 10,
                "data": {
                    "action": "unapproved",
                    "display_username": "calayx",
                    "topic_title": "ТЗ",
                },
            },
            "tz_approval",
            {"topic": {}, "post": {}, "category": ""},
            400,
        )

        self.assertIn("снял одобрение с ТЗ в теме", message)

    def test_excerpt_cleanup_and_truncation(self):
        self.assertEqual(build_excerpt({"raw": "a\n\n b\tc"}, 100), "a b c")
        self.assertEqual(build_excerpt({"raw": "123456789"}, 6), "12345…")

    def test_excerpt_without_quote_stays_unchanged(self):
        self.assertEqual(build_excerpt({"raw": "обычный текст без цитаты"}, 100), "обычный текст без цитаты")

    def test_excerpt_truncates_after_quote_sanitization(self):
        raw = '[quote="real_user, post:1, topic:123"]123456789[/quote]abcdef'

        self.assertEqual(build_excerpt({"raw": raw}, 4), "abc…")

    def test_excerpt_removes_multiple_quote_headers(self):
        raw = '[quote="first_user, post:1, topic:123"]первая[/quote] середина [quote=second_user]вторая[/quote]'

        self.assertEqual(build_excerpt({"raw": raw}, 100), "середина")

    def test_excerpt_removes_nested_quote_blocks(self):
        raw = '[quote="first_user"]первая [quote="second_user"]вторая[/quote][/quote] ответ'

        self.assertEqual(build_excerpt({"raw": raw}, 100), "ответ")

    def test_excerpt_keeps_regular_mentions_and_profile_links_outside_quote(self):
        raw = "Привет @real_user https://forum.example.ru/u/real_user/summary"

        self.assertEqual(build_excerpt({"raw": raw}, 100), raw)

    def test_excerpt_removes_cooked_quote_title_author(self):
        cooked = (
            '<aside class="quote" data-post="1" data-topic="123">'
            '<div class="title"><div class="quote-controls"></div>'
            '<a href="/u/real_user">real_user</a>:</div>'
            "<blockquote><p>текст цитаты</p></blockquote>"
            "</aside>"
            "<p>ответ</p>"
        )

        self.assertEqual(build_excerpt({"cooked": cooked}, 100), "ответ")


if __name__ == "__main__":
    unittest.main()
