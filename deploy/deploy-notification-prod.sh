#!/usr/bin/env bash
set -euo pipefail

TOOLS_DIR="${TOOLS_DIR:-/var/tools}"
REPO_DIR="${REPO_DIR:-$TOOLS_DIR/notification-sender-service-discourse-prod}"
EXPECTED_BRANCH="${EXPECTED_BRANCH:-main}"
ENV_FILE="$REPO_DIR/deploy/.env.notification.prod"
OVERLAY_FILE="$REPO_DIR/deploy/docker-compose.notification-service.prod.yml"

if [[ ! -f "$TOOLS_DIR/docker-compose.yml" ]]; then
  echo "Missing base compose file: $TOOLS_DIR/docker-compose.yml" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

cd "$REPO_DIR"

current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "$EXPECTED_BRANCH" ]]; then
  echo "Production deploy must run from branch '$EXPECTED_BRANCH', current branch is '$current_branch'." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Repository has uncommitted tracked changes. Commit, stash, or revert them before deploy." >&2
  exit 1
fi

git pull --ff-only

cd "$TOOLS_DIR"

docker compose \
  --env-file .env \
  --env-file "$ENV_FILE" \
  -f docker-compose.yml \
  -f "$OVERLAY_FILE" \
  config >/dev/null

docker compose \
  --env-file .env \
  --env-file "$ENV_FILE" \
  -f docker-compose.yml \
  -f "$OVERLAY_FILE" \
  up -d --build --no-deps notification-redis-prod notification-service-prod

docker compose \
  --env-file .env \
  --env-file "$ENV_FILE" \
  -f docker-compose.yml \
  -f "$OVERLAY_FILE" \
  ps notification-redis-prod notification-service-prod
