import unittest

from logging_config import _redact


class LoggingConfigTests(unittest.TestCase):
    def test_redacts_telegram_bot_token_in_url(self):
        value = "POST http://telegram-bot-api:8081/bot123456:TEST_TOKEN_VALUE_WITH_SAFE_PLACEHOLDER/sendMessage"

        self.assertEqual(
            _redact(value),
            "POST http://telegram-bot-api:8081/bot<redacted>/sendMessage",
        )

    def test_redacts_bearer_token(self):
        value = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"

        self.assertEqual(_redact(value), "Authorization: Bearer <redacted>")


if __name__ == "__main__":
    unittest.main()
