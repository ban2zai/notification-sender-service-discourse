import unittest

from templates import build_idempotency_key, build_topic_url, escape_md, render_notification_message


class TemplateTests(unittest.TestCase):
    def test_escape_markdown_v1_specials(self):
        self.assertEqual(escape_md("a*b_c`d[e"), "a\\*b\\_c\\`d\\[e")

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

    def test_render_notification_message(self):
        message, url = render_notification_message(
            "https://forum.example.ru",
            {
                "notification_type": 2,
                "user_id": 123,
                "topic_id": 456,
                "post_number": 7,
                "data": {"topic_title": "A_B", "original_username": "u*ser"},
            },
        )

        self.assertEqual(url, "https://forum.example.ru/t/456/7")
        self.assertIn("u\\*ser", message)
        self.assertIn("A\\_B", message)

    def test_idempotency_key_shape(self):
        self.assertEqual(
            build_idempotency_key(
                {"notification_type": 2, "topic_id": 456, "post_number": 7, "user_id": 123}
            ),
            "2_456_7_123",
        )


if __name__ == "__main__":
    unittest.main()

