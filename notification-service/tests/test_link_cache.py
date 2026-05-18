import asyncio
import unittest
from types import SimpleNamespace

from link_cache import TelegramLinkCache


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LinkCacheTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, **overrides):
        values = {
            "supabase_url": "http://supabase-kong:8000",
            "discourse_links_table": "telegram_discourse_links_test",
            "supabase_key": "replace-me",
            "supabase_timeout_seconds": 3,
            "supabase_links_cache_enabled": True,
            "supabase_links_cache_refresh_seconds": 60,
            "supabase_links_cache_stale_seconds": 1800,
            "supabase_links_direct_lookup_on_miss": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    async def test_refresh_loads_active_links(self):
        http = FakeHttp(
            [
                FakeResponse(
                    [
                        {"discourse_user_id": 1, "chat_id": 641388037},
                        {"discourse_user_id": "2", "chat_id": "123"},
                    ]
                )
            ]
        )
        cache = TelegramLinkCache(http, self.settings())

        refreshed = await cache.refresh_once()

        self.assertTrue(refreshed)
        self.assertEqual(await cache.lookup(1), 641388037)
        self.assertEqual(await cache.lookup(2), 123)
        self.assertEqual(len(http.calls), 1)

    async def test_cache_miss_uses_direct_lookup_and_updates_cache(self):
        http = FakeHttp(
            [
                FakeResponse([]),
                FakeResponse([{"chat_id": 641388037}]),
            ]
        )
        cache = TelegramLinkCache(http, self.settings())
        await cache.refresh_once()

        self.assertEqual(await cache.lookup(1), 641388037)
        self.assertEqual(await cache.lookup(1), 641388037)
        self.assertEqual(len(http.calls), 2)

    async def test_disabled_cache_uses_direct_lookup(self):
        http = FakeHttp([FakeResponse([{"chat_id": 641388037}])])
        cache = TelegramLinkCache(http, self.settings(supabase_links_cache_enabled=False))

        self.assertEqual(await cache.lookup(1), 641388037)
        self.assertEqual(len(http.calls), 1)

    async def test_disabled_refresh_loop_waits_until_stop(self):
        http = FakeHttp([])
        cache = TelegramLinkCache(http, self.settings(supabase_links_cache_enabled=False))
        stop_event = asyncio.Event()
        task = asyncio.create_task(cache.refresh_loop(stop_event))

        await asyncio.sleep(0)
        self.assertFalse(task.done())
        stop_event.set()
        await task


if __name__ == "__main__":
    unittest.main()
