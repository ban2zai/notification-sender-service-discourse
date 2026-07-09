import json
import logging
import unittest
from types import SimpleNamespace

import drain

logging.getLogger("discourse").disabled = True
logging.getLogger("drain").disabled = True


class FakeRedis:
    def __init__(self):
        self.acked = []

    async def get(self, key):
        return None

    async def xpending_range(self, stream, group, min, max, count):
        return [{"times_delivered": 1}]

    async def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))


class FakeHttp:
    async def get(self, *args, **kwargs):
        raise RuntimeError("api down")


class FakeLimiter:
    async def wait(self, chat_id):
        return None


class DrainTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return SimpleNamespace(
            redis_stream="tg_notifications",
            redis_group="drain",
            max_attempts=5,
            discourse_base_url="https://forum.example.com",
            discourse_api_key="replace-me",
            discourse_api_username="system",
            discourse_api_timeout_seconds=3,
            discourse_topic_cache_ttl_seconds=1800,
            discourse_post_cache_ttl_seconds=1800,
            discourse_categories_cache_ttl_seconds=43200,
            telegram_excerpt_max_chars=400,
        )

    async def test_process_one_falls_back_and_acks_after_send(self):
        sent = []

        async def fake_send(http_client, settings, chat_id, text):
            sent.append((chat_id, text))
            return True, None, None

        original_send = drain.send_telegram_message
        drain.send_telegram_message = fake_send
        try:
            redis = FakeRedis()
            notification = {
                "notification_type": 36,
                "topic_id": 456,
                "post_number": 1,
                "data": {"topic_title": "Fallback topic"},
            }
            await drain._process_one(
                redis,
                FakeHttp(),
                self.settings(),
                FakeLimiter(),
                b"1-0",
                {
                    b"chat_id": b"641388037",
                    b"event_kind": b"new_topic",
                    b"idempotency_key": b"new_topic:456:1",
                    b"notification_json": json.dumps(notification).encode(),
                },
            )
        finally:
            drain.send_telegram_message = original_send

        self.assertEqual(redis.acked, [("tg_notifications", "drain", b"1-0")])
        self.assertEqual(sent[0][0], 641388037)
        self.assertIn("Fallback topic", sent[0][1])

    async def test_process_one_sanitizes_ready_message_text_before_send(self):
        sent = []

        async def fake_send(http_client, settings, chat_id, text):
            sent.append((chat_id, text))
            return True, None, None

        original_send = drain.send_telegram_message
        drain.send_telegram_message = fake_send
        try:
            redis = FakeRedis()
            await drain._process_one(
                redis,
                FakeHttp(),
                self.settings(),
                FakeLimiter(),
                b"2-0",
                {
                    b"chat_id": b"641388037",
                    b"event_kind": b"reply",
                    b"idempotency_key": b"reply:456:2:1",
                    b"message_text": b'[quote="real_user, post:1, topic:123"]quoted[/quote] answer',
                },
            )
        finally:
            drain.send_telegram_message = original_send

        self.assertEqual(redis.acked, [("tg_notifications", "drain", b"2-0")])
        self.assertNotIn("real_user", sent[0][1])
        self.assertNotIn("[quote", sent[0][1])
        self.assertNotIn("[/quote]", sent[0][1])
        self.assertNotIn("quoted", sent[0][1])
        self.assertIn("answer", sent[0][1])


if __name__ == "__main__":
    unittest.main()
