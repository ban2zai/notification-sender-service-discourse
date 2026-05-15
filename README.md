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
- `deploy/docker-compose.notification-service.yml` - overlay compose for `/var/tools` on the VPS.
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
  -f notification-sender-service-discourse/deploy/docker-compose.notification-service.yml \
  config
```

Start only the new services:

```bash
docker compose \
  --env-file .env \
  --env-file notification-sender-service-discourse/deploy/.env.notification.test \
  -f docker-compose.yml \
  -f notification-sender-service-discourse/deploy/docker-compose.notification-service.yml \
  up -d --build --no-deps notification-redis notification-service
```

Run only `notification-redis` and `notification-service` during rollout.

## Test vs Production

Test mode uses `deploy/.env.notification.test` and points to the test Discourse forum/table/bot.

Production mode uses `deploy/.env.notification.prod` and must be created only after test mode is verified:

```bash
cp notification-sender-service-discourse/deploy/.env.notification.prod.example \
   notification-sender-service-discourse/deploy/.env.notification.prod
chmod 600 notification-sender-service-discourse/deploy/.env.notification.prod
```

Then replace the second `--env-file` in the compose commands with:

```bash
--env-file notification-sender-service-discourse/deploy/.env.notification.prod
```

## Runtime Notes

- `notification-redis` is separate from other services and stores only this service queue.
- `notification-service` has no published host ports by default.
- Nginx Proxy Manager should reach it through `proxy_network` by service name `notification-service` and port `8067`.
- The service reaches Telegram via `telegram-bot-api:8081` on the existing `/var/tools` default network.
- The service reaches Supabase via `supabase-kong:8000` on `supabase_default`.
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
- Expected internal failures after successful signature validation return `200 {"ok": true}` to avoid a Discourse retry storm.
- Deduplication is atomic Redis Lua: `SET NX EX` + `XADD`.
- `XACK` happens only after Telegram accepts the message or after the retry limit is exhausted.
- Dead letters are JSON logs in stdout for Promtail/Loki, not DB rows.
