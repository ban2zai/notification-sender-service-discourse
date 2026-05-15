import unittest

from logging_config import _redact


class LoggingConfigTests(unittest.TestCase):
    def test_redacts_telegram_bot_token_in_url(self):
        value = "POST http://telegram-bot-api:8081/bot8774568342:AAHeyLuQDTtDUV-q4N_WCmi1pNnI7_O48KA/sendMessage"

        self.assertEqual(
            _redact(value),
            "POST http://telegram-bot-api:8081/bot<redacted>/sendMessage",
        )

    def test_redacts_bearer_token(self):
        value = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"

        self.assertEqual(_redact(value), "Authorization: Bearer <redacted>")


if __name__ == "__main__":
    unittest.main()

