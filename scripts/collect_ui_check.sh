#!/usr/bin/env bash
# UI check of the collection stage in a real browser — no key, no model calls.
#
#   bash scripts/collect_ui_check.sh        # screenshots → the temp dir printed at the end
#
# Runs on a temp copy of the repo without .env and without NVIDIA_API_KEY, so every step replays the committed
# bake (audio, transcript, extraction) and the real data/ is never written. Drives Chromium with the globally
# installed Node Playwright (NODE_PATH, PLAYWRIGHT_BROWSERS_PATH) — nothing is installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${PORT:-8061}"
TMP="$(mktemp -d)"
mkdir -p "$TMP/repo" "$TMP/shots"
tar -C "$ROOT" --exclude=./.venv --exclude=./.git --exclude=./console --exclude=./.env --exclude='__pycache__' \
    --exclude=./data/collect_runtime --exclude=./data/stt_cache --exclude=./data/tts_cache --exclude=./data/board_live -cf - . \
  | tar -C "$TMP/repo" -xf -

# the copy has no .git — name the GitHub source the way a deployment does (env), from the real checkout
SRC="$(cd "$ROOT" && "$PY" -c "from loop import collect; s = collect.source_info(); print(s['repo'], s['ref'] or s['commit'])")"
export COLLECT_GITHUB_REPO="${SRC%% *}" COLLECT_GITHUB_REF="${SRC#* }"
cd "$TMP/repo"
EXPECT_CER="$(env -u NVIDIA_API_KEY "$PY" -c "
from loop import collect, stt
s = collect.get('FS-01'); r = collect.transcript(s, mode='replay')
print(f\"{round(stt.cer(r['text'], s['text']) * 100, 1):.1f}\")")"
env -u NVIDIA_API_KEY "$PY" -m uvicorn loop.web:app --port "$PORT" --log-level warning > "$TMP/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/health" > /dev/null && break
  sleep 0.2
done

NODE_PATH="${NODE_PATH:-/opt/node22/lib/node_modules}" PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}" \
  EXPECT_CER="$EXPECT_CER" node "$ROOT/scripts/collect_ui_check.cjs" "http://127.0.0.1:$PORT" "$TMP/shots" && STATUS=0 || STATUS=$?
echo "screenshots: $TMP/shots · server log: $TMP/server.log"
exit $STATUS
