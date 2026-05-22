import json
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ingestion import create_app


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.set_calls = []
        self.deleted = []

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.set_calls.append((key, value, ex))
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)
        return 1


class FakeResponse:
    def __init__(self, data, status_code=200, text=None):
        self._data = data
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)


class FakeLinkCache:
    def __init__(self):
        self.remembered = []

    async def remember(self, user_id, chat_id):
        self.remembered.append((user_id, chat_id))


class AccountLinkApiTests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(
            account_link_api_token="secret",
            account_link_token_ttl_seconds=600,
            discourse_base_url="https://forum.example.com",
            supabase_url="http://supabase-kong:8000",
            supabase_key="service-key",
            supabase_timeout_seconds=3,
            telegram_api_url="http://telegram-bot-api:8081",
            telegram_timeout_seconds=10,
            bot_token="bot-token",
            telegram_users_table="telegram_users_test",
            discourse_links_table="telegram_discourse_links_test",
            webhook_secret="webhook-secret",
        )

    def client(self, redis=None, http=None, link_cache=None):
        app = create_app(
            redis_client=redis or FakeRedis(),
            http_client=http or FakeHttp(),
            settings=self.settings(),
            link_cache=link_cache or FakeLinkCache(),
        )
        return TestClient(app)

    def auth_headers(self):
        return {"Authorization": "Bearer secret"}

    def token_key(self, token="token-1"):
        return f"telegram_link_token:{token}"

    def test_link_token_requires_bearer(self):
        response = self.client().post("/telegram/link-token", json={"chat_id": 641388037})

        self.assertEqual(response.status_code, 401)

    def test_link_token_rejects_invalid_chat_id(self):
        response = self.client().post(
            "/telegram/link-token",
            headers=self.auth_headers(),
            json={"chat_id": "bad"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_chat_id")

    def test_link_token_creates_redis_token_and_returns_discourse_url(self):
        redis = FakeRedis()
        response = self.client(redis=redis).post(
            "/telegram/link-token",
            headers=self.auth_headers(),
            json={"chat_id": 641388037},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["expires_in"], 600)
        self.assertTrue(body["url"].startswith("https://forum.example.com/link-telegram?token="))
        self.assertEqual(len(redis.set_calls), 1)
        _, stored_value, ttl = redis.set_calls[0]
        self.assertEqual(json.loads(stored_value), {"chat_id": 641388037})
        self.assertEqual(ttl, 600)

    def test_account_link_rejects_expired_token(self):
        response = self.client(redis=FakeRedis()).post(
            "/telegram/account-link",
            headers=self.auth_headers(),
            json={
                "token": "missing",
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
            },
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "link_expired")

    def test_account_link_creates_rows_sends_message_and_updates_cache(self):
        redis = FakeRedis({self.token_key(): json.dumps({"chat_id": 641388037})})
        http = FakeHttp(
            get_responses=[FakeResponse([]), FakeResponse([])],
            post_responses=[
                FakeResponse({}, 201),
                FakeResponse({}, 201),
                FakeResponse({"ok": True}, 200),
            ],
        )
        link_cache = FakeLinkCache()

        response = self.client(redis=redis, http=http, link_cache=link_cache).post(
            "/telegram/account-link",
            headers=self.auth_headers(),
            json={
                "token": "token-1",
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "linked")
        self.assertEqual(redis.deleted, [self.token_key()])
        self.assertEqual(link_cache.remembered, [(1, 641388037)])
        self.assertEqual(len(http.post_calls), 3)
        self.assertEqual(http.post_calls[0][1]["json"], {"chat_id": 641388037})
        self.assertEqual(
            http.post_calls[1][1]["json"],
            {
                "chat_id": 641388037,
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
                "is_active": True,
            },
        )
        self.assertEqual(
            http.post_calls[2][1]["json"]["reply_markup"],
            {"inline_keyboard": [[{"text": "⚙️ Открыть настройки", "callback_data": "/settings"}]]},
        )

    def test_account_link_same_pair_returns_already_linked(self):
        redis = FakeRedis({self.token_key(): json.dumps({"chat_id": 641388037})})
        http = FakeHttp(
            get_responses=[
                FakeResponse([{"chat_id": 641388037}]),
                FakeResponse([{"discourse_user_id": 1}]),
            ],
        )

        response = self.client(redis=redis, http=http).post(
            "/telegram/account-link",
            headers=self.auth_headers(),
            json={
                "token": "token-1",
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_linked")
        self.assertEqual(len(http.post_calls), 0)

    def test_account_link_detects_discourse_conflict(self):
        redis = FakeRedis({self.token_key(): json.dumps({"chat_id": 641388037})})
        http = FakeHttp(
            get_responses=[
                FakeResponse([{"chat_id": 123}]),
                FakeResponse([]),
            ],
        )

        response = self.client(redis=redis, http=http).post(
            "/telegram/account-link",
            headers=self.auth_headers(),
            json={
                "token": "token-1",
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "discourse_conflict")
        self.assertEqual(len(http.post_calls), 0)

    def test_account_link_detects_telegram_conflict(self):
        redis = FakeRedis({self.token_key(): json.dumps({"chat_id": 641388037})})
        http = FakeHttp(
            get_responses=[
                FakeResponse([]),
                FakeResponse([{"discourse_user_id": 2}]),
            ],
        )

        response = self.client(redis=redis, http=http).post(
            "/telegram/account-link",
            headers=self.auth_headers(),
            json={
                "token": "token-1",
                "discourse_user_id": 1,
                "discourse_username": "calayx",
                "email": "calayx@example.com",
                "linked_at": "2026-04-16T06:15:34Z",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "telegram_conflict")
        self.assertEqual(len(http.post_calls), 0)


if __name__ == "__main__":
    unittest.main()
