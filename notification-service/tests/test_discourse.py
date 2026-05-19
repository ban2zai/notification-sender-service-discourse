import json
import logging
import unittest
from types import SimpleNamespace

from discourse import _get_cached_json, enrich_notification

logging.getLogger("discourse").disabled = True


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        self.values[key] = value
        self.set_calls.append((key, value, ex))


class FakeResponse:
    def __init__(self, data, status_error=None):
        self.data = data
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.data


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class DiscourseTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return SimpleNamespace(
            discourse_base_url="https://forum.example.com",
            discourse_api_key="replace-me",
            discourse_api_username="system",
            discourse_api_timeout_seconds=3,
            discourse_topic_cache_ttl_seconds=1800,
            discourse_post_cache_ttl_seconds=1800,
            discourse_categories_cache_ttl_seconds=43200,
        )

    async def test_cache_hit_skips_http(self):
        redis = FakeRedis()
        redis.values["cache:key"] = json.dumps({"ok": True})
        http = FakeHttp({})

        data, had_error = await _get_cached_json(redis, http, self.settings(), "cache:key", 10, "https://x")

        self.assertFalse(had_error)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(http.calls, [])

    async def test_cache_miss_fetches_and_writes(self):
        redis = FakeRedis()
        http = FakeHttp({"https://x": FakeResponse({"ok": True})})

        data, had_error = await _get_cached_json(redis, http, self.settings(), "cache:key", 10, "https://x")

        self.assertFalse(had_error)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(redis.set_calls[0][0], "cache:key")

    async def test_enrich_notification_handles_api_failure(self):
        redis = FakeRedis()
        http = FakeHttp({"https://forum.example.com/t/456.json": RuntimeError("boom")})

        data, had_error = await enrich_notification(
            redis,
            http,
            self.settings(),
            {"topic_id": 456, "data": {}},
        )

        self.assertTrue(had_error)
        self.assertEqual(data["topic"], {})

    async def test_enrich_notification_marks_anonymous_post(self):
        redis = FakeRedis()
        http = FakeHttp(
            {
                "https://forum.example.com/t/438.json": FakeResponse(
                    {
                        "title": "Anonymous topic",
                        "post_stream": {
                            "posts": [
                                {
                                    "id": 1184,
                                    "post_number": 1,
                                    "display_username": "Real User",
                                    "is_anonymous_post": 1,
                                }
                            ]
                        },
                    }
                ),
                "https://forum.example.com/posts/1184.json": FakeResponse(
                    {
                        "id": 1184,
                        "display_username": "Real User",
                        "is_anonymous_post": 1,
                    }
                ),
            }
        )

        data, had_error = await enrich_notification(
            redis,
            http,
            self.settings(),
            {"topic_id": 438, "post_number": 1, "data": {"original_post_id": 1184}},
        )

        self.assertFalse(had_error)
        self.assertTrue(data["is_anonymous"])
        self.assertEqual(data["actor_username"], "anonuser")

    async def test_enrich_notification_works_without_anonymous_plugin_fields(self):
        redis = FakeRedis()
        http = FakeHttp(
            {
                "https://forum.example.com/t/438.json": FakeResponse({"title": "Regular topic"}),
                "https://forum.example.com/posts/1201.json": FakeResponse(
                    {
                        "id": 1201,
                        "display_username": "Regular User",
                        "username": "regular_user",
                    }
                ),
            }
        )

        data, had_error = await enrich_notification(
            redis,
            http,
            self.settings(),
            {"topic_id": 438, "post_number": 5, "data": {"original_post_id": 1201}},
        )

        self.assertFalse(had_error)
        self.assertFalse(data["is_anonymous"])
        self.assertEqual(data["actor_username"], "@regular_user")

    async def test_enrich_notification_does_not_hide_regular_reply_in_anonymous_topic(self):
        redis = FakeRedis()
        http = FakeHttp(
            {
                "https://forum.example.com/t/531.json": FakeResponse(
                    {
                        "title": "Anonymous topic",
                        "is_anonymous_topic": 1,
                        "post_stream": {
                            "posts": [
                                {
                                    "id": 1395,
                                    "post_number": 1,
                                    "username": "AleskerovTI",
                                    "is_anonymous_post": 1,
                                },
                                {
                                    "id": 1396,
                                    "post_number": 2,
                                    "username": "calayx",
                                    "is_anonymous_post": 0,
                                },
                            ]
                        },
                    }
                ),
                "https://forum.example.com/posts/1396.json": FakeResponse(
                    {
                        "id": 1396,
                        "post_number": 2,
                        "username": "calayx",
                        "is_anonymous_post": 0,
                    }
                ),
            }
        )

        data, had_error = await enrich_notification(
            redis,
            http,
            self.settings(),
            {"topic_id": 531, "post_number": 2, "data": {"original_post_id": 1396}},
        )

        self.assertFalse(had_error)
        self.assertFalse(data["is_anonymous"])
        self.assertEqual(data["actor_username"], "@calayx")


if __name__ == "__main__":
    unittest.main()
