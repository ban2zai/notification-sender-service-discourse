# Notification Sender Service for Discourse

Python service for delivering Discourse webhook notifications to Telegram through a local Telegram Bot API sidecar.

## Repository Hygiene

- Secrets are not committed.
- Real environment files are ignored by git.
- Test and production values use separate local env files.
- VPS deployment uses a Compose overlay for `/var/tools`.

## Project Layout

- `notification-service/` - Python app: FastAPI ingestion, Redis Stream drain, pending-message reaper.
- `docker-compose.yml` - local/dev compose for this repo only.
- `deploy/docker-compose.notification-service.test.yml` - test overlay compose for `/var/tools` on the VPS.
- `deploy/docker-compose.notification-service.prod.yml` - production overlay compose for `/var/tools` on the VPS.
- `deploy/docker-compose.notification-service.yml` - legacy single-instance overlay kept for compatibility.
- `deploy/.env.notification.test.example` - test env template.
- `deploy/.env.notification.prod.example` - production env template.

## Recommended VPS Deployment

Assumed VPS layout:

```text
/var/tools/docker-compose.yml
/var/tools/notification-sender-service-discourse/
```

Clone the repo under `/var/tools`:

```bash
cd /var/tools
git clone <repo-url> notification-sender-service-discourse
```

Create a local test env file:

```bash
cp notification-sender-service-discourse/deploy/.env.notification.test.example \
   notification-sender-service-discourse/deploy/.env.notification.test
chmod 600 notification-sender-service-discourse/deploy/.env.notification.test
```

Fill real values in:

```bash
nano notification-sender-service-discourse/deploy/.env.notification.test
```

Validate the merged compose before starting anything:

```bash
docker compose \
  --env-file .env \
  --env-file notification-sender-service-discourse/deploy/.env.notification.test \
  -f docker-compose.yml \
  -f notification-sender-service-discourse/deploy/docker-compose.notification-service.test.yml \
  config
```

Start only the test services:

```bash
docker compose \
  --env-file .env \
  --env-file notification-sender-service-discourse/deploy/.env.notification.test \
  -f docker-compose.yml \
  -f notification-sender-service-discourse/deploy/docker-compose.notification-service.test.yml \
  up -d --build --no-deps notification-redis-test notification-service-test
```

Run only `notification-redis-test` and `notification-service-test` during test rollout.

## Test vs Production

Test mode uses `deploy/.env.notification.test` and points to the test Discourse forum/table/bot.
Expose it through Nginx Proxy Manager as:

```text
tgsender-test.b2zn8n.ru -> notification-service-test:8067
```

Production mode uses `deploy/.env.notification.prod` and must be created only after test mode is verified:

```bash
cp notification-sender-service-discourse/deploy/.env.notification.prod.example \
   notification-sender-service-discourse/deploy/.env.notification.prod
chmod 600 notification-sender-service-discourse/deploy/.env.notification.prod
```

Expose production through Nginx Proxy Manager as:

```text
tgsender.b2zn8n.ru -> notification-service-prod:8067
```

Production deploy command:

```bash
docker compose \
  --env-file .env \
  --env-file notification-sender-service-discourse/deploy/.env.notification.prod \
  -f docker-compose.yml \
  -f notification-sender-service-discourse/deploy/docker-compose.notification-service.prod.yml \
  up -d --build --no-deps notification-redis-prod notification-service-prod
```

Keep test and production running as separate compose services. Do not switch one container between test and production env files.

## Runtime Notes

- `notification-redis-test` and `notification-redis-prod` are separate Redis instances.
- `notification-service-test` and `notification-service-prod` have no published host ports by default.
- Nginx Proxy Manager should reach them through `proxy_network` by service name and port `8067`.
- The service reaches Telegram via `telegram-bot-api:8081` on the existing `/var/tools` default network.
- The service reaches Supabase via `supabase-kong:8000` on `supabase_default`.
- The service enriches notifications through Discourse API using `DISCOURSE_API_KEY` and `DISCOURSE_API_USERNAME`.
- Account linking endpoints are protected by `ACCOUNT_LINK_API_TOKEN`.
- n8n should call `POST /telegram/link-token` to create a short-lived forum link for `/settings`.
- The Discourse plugin should call `POST /telegram/account-link` to finalize the token with `discourse_user_id`, `discourse_username`, `email`, and `linked_at`.
- Set `NOTIFICATION_LOG_PAYLOAD_DATA=true` to log `notification.data` during template debugging.

## Checks

Local checks that do not need external services:

```bash
python -m compileall notification-service
cd notification-service
python -m unittest discover -s tests -v
```

Compose validation:

```bash
docker compose --env-file .env.example config
```

On the VPS, use the merged compose validation command from the deployment section.

## Important Behavior

- Invalid HMAC signature returns `401`.
- Invalid account-link Bearer token returns `401`.
- Account-link tokens are stored in Redis with `ACCOUNT_LINK_TOKEN_TTL_SECONDS` TTL.
- Expired account-link tokens return `410`; conflicting active links return `409`.
- Expected internal failures after successful signature validation return `200 {"ok": true}` to avoid a Discourse retry storm.
- Deduplication is atomic Redis Lua: `SET NX EX` + `XADD`.
- New-topic deduplication is semantic: notification types `9`, `17`, and `36` collapse to one `new_topic:{topic_id}:{user_id}` key.
- Drain enriches events from Discourse API cache-first and falls back to a minimal HTML message if enrichment fails.
- `XACK` happens only after Telegram accepts the message or after the retry limit is exhausted.
- Dead letters are JSON logs in stdout for Promtail/Loki, not DB rows.
