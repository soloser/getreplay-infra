#!/usr/bin/env bash

set -euo pipefail

required_environment=(
  GITHUB_API_URL
  GITHUB_REPOSITORY
  GITHUB_SHA
  GITHUB_TOKEN
  RELEASE_HOST
  RELEASE_KEY
  RELEASE_KNOWN_HOSTS
  RELEASE_PORT
  RELEASE_SCOPE
  RELEASE_USER
  RUNNER_TEMP
)

for variable_name in "${required_environment[@]}"; do
  [ -n "${!variable_name:-}" ] || {
    printf 'required environment value is empty: %s\n' "$variable_name" >&2
    exit 1
  }
done

[[ "$RELEASE_HOST" =~ ^[A-Za-z0-9.-]+$ ]]
[[ "$RELEASE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]
[[ "$RELEASE_PORT" =~ ^[0-9]{1,5}$ ]]
(( RELEASE_PORT >= 1 && RELEASE_PORT <= 65535 ))
[[ "$RELEASE_SCOPE" =~ ^(all|frontend|node|php|go|migrations)$ ]]

credential_directory="$(mktemp -d "$RUNNER_TEMP/getreplay-release-ssh.XXXXXX")"
cleanup() {
  rm -f "$credential_directory/id" "$credential_directory/known_hosts"
  rmdir "$credential_directory" 2>/dev/null || true
}
trap cleanup EXIT

printf '%s\n' "$RELEASE_KEY" > "$credential_directory/id"
printf '%s\n' "$RELEASE_KNOWN_HOSTS" > "$credential_directory/known_hosts"
chmod 0600 "$credential_directory/id"
chmod 0644 "$credential_directory/known_hosts"

fetch_reviewed_file() {
  local source_path="$1"
  local destination="$2"
  local api_url response encoded

  api_url="$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/contents/$source_path?ref=$GITHUB_SHA"
  response="$(curl --fail --silent --show-error \
    --header "Accept: application/vnd.github+json" \
    --header "Authorization: Bearer $GITHUB_TOKEN" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    "$api_url")"
  encoded="$(jq -r '.content // empty' <<<"$response" | tr -d '\n')"
  [[ "$encoded" =~ ^[A-Za-z0-9+/]+={0,2}$ ]]
  printf '%s' "$encoded" | base64 --decode > "$destination"
}

fetch_reviewed_file release/candidate.json "$RUNNER_TEMP/candidate.json"
fetch_reviewed_file release/select_scope.py "$RUNNER_TEMP/select_scope.py"

if [ "$RELEASE_SCOPE" = all ]; then
  release_id=candidate
else
  release_id="candidate-$RELEASE_SCOPE"
fi

python3 "$RUNNER_TEMP/select_scope.py" \
  --scope "$RELEASE_SCOPE" \
  --release-id "$release_id" \
  --input "$RUNNER_TEMP/candidate.json" \
  --output "$RUNNER_TEMP/selected-candidate.json"
python3 -m json.tool "$RUNNER_TEMP/selected-candidate.json" >/dev/null

manifest="$(base64 < "$RUNNER_TEMP/selected-candidate.json" | tr -d '\n')"
[[ "$manifest" =~ ^[A-Za-z0-9+/]+={0,2}$ ]]

release_ssh() {
  local release_command="$1"

  ssh \
    -F /dev/null \
    -T \
    -o BatchMode=yes \
    -o ClearAllForwardings=yes \
    -o ConnectTimeout=15 \
    -o IdentitiesOnly=yes \
    -o RequestTTY=no \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$credential_directory/known_hosts" \
    -i "$credential_directory/id" \
    -p "$RELEASE_PORT" \
    "$RELEASE_USER@$RELEASE_HOST" \
    "$release_command"
}

release_ssh "getreplay-release stage $release_id $manifest"
release_ssh "getreplay-release preview promote $release_id"
release_ssh "getreplay-release promote $release_id"
