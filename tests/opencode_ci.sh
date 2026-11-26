#!/usr/bin/env bash
# Run the existing browser acceptance against an isolated native OpenCode server.
set -euo pipefail
binary=${1:?Pass the absolute path to the OpenCode executable}
version=${2:?Pass the expected OpenCode version}
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
  url=$(sed -nE 's|^(opencode )?server listening on (http://127\.0\.0\.1:[0-9]+).*|\2|p' "$sandbox/server.log")
  if [[ -n $url ]] || ! kill -0 "$server_pid" 2>/dev/null; then break; fi
  sleep 0.5
done
if [[ -z $url ]]; then sed '/server password/d' "$sandbox/server.log" >&2; exit 1; fi
auth=()
payload='{"title":"CI baseline"}'
if [[ $version == 2.2.7 ]]; then
  # This instance generates its own Basic password. Keep it out of the manifest and logs.
  OPENCODE_CI_PASSWORD=$(sed -n 's/^server password //p' "$sandbox/server.log")
  test -n "$OPENCODE_CI_PASSWORD"
  export OPENCODE_CI_PASSWORD OPENCODE_TEST_PASSWORD_ENV=OPENCODE_CI_PASSWORD
  auth=(-u "opencode:$OPENCODE_CI_PASSWORD")
  api=/api
  curl -fsS "${auth[@]}" --max-time 10 "$url/api/info" | jq -e --arg v "$version" '.version == $v'
  payload=$(jq -nc --arg dir "$project" '{title:"CI baseline",location:{directory:$dir}}')
else
  api=
  curl -fsS --max-time 10 "$url/global/health" | jq -e --arg v "$version" '.healthy and .version == $v'
fi

sessions="$url$api/session"
list_sessions() {
  curl -fsSG "${auth[@]}" --max-time 10 --data-urlencode "directory=$project" "$sessions" |
    jq 'if type == "array" then . else .data end | arrays'
}
list_sessions | jq -e 'type == "array" and length == 0'
# The scenario starts with one visible session, then creates another with the browser open.
seed=$(curl -fsS "${auth[@]}" --max-time 10 -X POST -H 'Content-Type: application/json' \
  -H "x-opencode-directory: $project" -d "$payload" "$sessions" | jq -er '.id // .data.id')
OPENCODE_TEST_URL="$url" OPENCODE_TEST_PROJECT="$project" \
  python tests/opencode_integration.py | tee "$sandbox/browser.log"

# A fixture-only pass must fail CI. Match the ID observed in Chromium to the real server.
observed=$(sed -n 's/^Native OpenCode new-session discovery: //p' "$sandbox/browser.log")
test -n "$observed"
list_sessions | jq -e --arg seed "$seed" --arg observed "$observed" \
  'length == 2 and $seed != $observed and any(.[]; .id == $seed) and any(.[]; .id == $observed)'
