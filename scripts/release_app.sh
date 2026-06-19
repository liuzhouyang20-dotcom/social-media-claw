#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/repos/social-media-claw"
APK_SRC="$ROOT/android-app/app/build/outputs/apk/debug/app-debug.apk"
APK_DST="$ROOT/downloads/social-media-claw-debug.apk"
SERVICE="link-collector-viewer.service"
AUTH_ARGS=()
ENV_FILE="/home/ubuntu/.config/link-collector-viewer/env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

BASE_URL="${LINK_VIEWER_BASE_URL:-http://49.51.72.63}"

VERSION_CODE=$(grep -E "^[[:space:]]*versionCode[[:space:]]+[0-9]+" "$ROOT/android-app/app/build.gradle" | awk '{print $2}')
VERSION_NAME=$(grep -E "^[[:space:]]*versionName[[:space:]]+\".+\"" "$ROOT/android-app/app/build.gradle" | sed -E 's/.*versionName[[:space:]]+"([^"]+)".*/\1/')

if [[ -n "${LINK_VIEWER_PASSWORD:-}" ]]; then
  AUTH_ARGS=(--user "${LINK_VIEWER_USER:-admin}:$LINK_VIEWER_PASSWORD")
fi

cd "$ROOT/android-app"
./gradlew :app:assembleDebug

cd "$ROOT"
mkdir -p "$ROOT/downloads"
cp "$APK_SRC" "$APK_DST"
mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
for key in LINK_APP_LATEST_VERSION_CODE LINK_APP_LATEST_VERSION_NAME LINK_APP_MIN_VERSION_CODE; do
  sed -i "/^${key}=/d" "$ENV_FILE"
done
{
  printf 'LINK_APP_LATEST_VERSION_CODE=%s\n' "$VERSION_CODE"
  printf 'LINK_APP_LATEST_VERSION_NAME=%s\n' "$VERSION_NAME"
  printf 'LINK_APP_MIN_VERSION_CODE=%s\n' "$VERSION_CODE"
} >> "$ENV_FILE"

if systemctl --user list-unit-files "$SERVICE" >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user restart "$SERVICE"
fi
if systemctl list-unit-files "$SERVICE" >/dev/null 2>&1; then
  sudo systemctl daemon-reload
  sudo systemctl restart "$SERVICE"
fi

for _ in {1..20}; do
  if curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL/healthz" >/dev/null; then
    break
  fi
  sleep 0.5
done

curl -fsS "$BASE_URL/api/app-version"
printf '\n'
sha256sum "$APK_DST"
