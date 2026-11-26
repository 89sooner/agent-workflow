#!/usr/bin/env bash
# Run the existing browser acceptance against an isolated native OpenCode server.
set -euo pipefail
binary=${1:?Pass the absolute path to the OpenCode 1.3.17 executable}
sandbox=$(mktemp -d)
project=$sandbox/project
mkdir "$project"
server_pid=
cleanup() {
  if [[ -n $server_pid ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$sandbox"
}
trap cleanup EXIT
git -C "$project" init --quiet
(
  cd "$project"
  exec env -i PATH="$PATH" HOME="$sandbox" XDG_CONFIG_HOME="$sandbox/config" \
    XDG_DATA_HOME="$sandbox/data" XDG_CACHE_HOME="$sandbox/cache" XDG_STATE_HOME="$sandbox/state" \
    OPENCODE_DISABLE_MODELS_FETCH=true OPENCODE_DISABLE_AUTOUPDATE=true \
    "$binary" serve --hostname 127.0.0.1 --port 0
) >"$sandbox/server.log" 2>&1 &
server_pid=$!

# Read the bound port from this process, including when the default port is occupied.
url=
for ((i=0; i<120; i++)); do
  url=$(sed -nE 's|^opencode server listening on (http://127\.0\.0\.1:[0-9]+).*|\1|p' "$sandbox/server.log")
  if [[ -n $url ]] || ! kill -0 "$server_pid" 2>/dev/null; then break; fi
  sleep 0.5
done
if [[ -z $url ]]; then cat "$sandbox/server.log" >&2; exit 1; fi
curl -fsS --max-time 10 "$url/global/health" | jq -e '.healthy and .version == "1.3.17"'

sessions="$url/session"
list_sessions() {
  curl -fsSG --max-time 10 --data-urlencode "directory=$project" "$sessions"
}
list_sessions | jq -e 'length == 0'
# The scenario starts with one visible session, then creates another with the browser open.
seed=$(curl -fsS --max-time 10 -X POST -H 'Content-Type: application/json' \
  -H "x-opencode-directory: $project" -d '{"title":"CI baseline"}' "$sessions" | jq -er '.id')
OPENCODE_TEST_URL="$url" OPENCODE_TEST_PROJECT="$project" \
  python tests/opencode_integration.py | tee "$sandbox/browser.log"

# A fixture-only pass must fail CI. Match the ID observed in Chromium to the real server.
observed=$(sed -n 's/^Native OpenCode new-session discovery: //p' "$sandbox/browser.log")
test -n "$observed"
list_sessions | jq -e --arg seed "$seed" --arg observed "$observed" \
  'length == 2 and $seed != $observed and any(.[]; .id == $seed) and any(.[]; .id == $observed)'
