#!/usr/bin/env bash
# This client never uploads code, imports the app, or changes service state.
set -euo pipefail
: "${AUTOTRADER_SSH_HOST:?Set the AUTOTRADER_SSH_HOST repository variable}"
: "${AUTOTRADER_SSH_USER:?Set the AUTOTRADER_SSH_USER repository variable}"
: "${AUTOTRADER_SSH_KEY:?Set the AUTOTRADER_SSH_KEY Actions secret}"
: "${AUTOTRADER_SSH_KNOWN_HOSTS:?Set the trusted AUTOTRADER_SSH_KNOWN_HOSTS secret}"
[[ "$AUTOTRADER_SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || exit 2
[[ "$AUTOTRADER_SSH_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || exit 2
probe_dir="$(mktemp -d)"
trap 'rm -rf "$probe_dir"' EXIT
umask 077
printf '%s\n' "$AUTOTRADER_SSH_KEY" > "$probe_dir/key"
printf '%s\n' "$AUTOTRADER_SSH_KNOWN_HOSTS" > "$probe_dir/known_hosts"
unset AUTOTRADER_SSH_KEY AUTOTRADER_SSH_KNOWN_HOSTS
probe_output="$(ssh -F /dev/null -i "$probe_dir/key" \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$probe_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null \
  -o ConnectTimeout=15 -o ConnectionAttempts=1 -o ServerAliveInterval=10 -o ServerAliveCountMax=2 \
  -o ClearAllForwardings=yes \
  "$AUTOTRADER_SSH_USER@$AUTOTRADER_SSH_HOST" \
  'set -eu; test -d /opt/autotrader; test "$(systemctl show autotrader.service --property=LoadState --value)" = loaded; systemctl show autotrader.service --property=ActiveState --property=SubState; printf "%s\n" AUTOTRADER_READ_ONLY_CONNECTION_OK')"
printf '%s\n' "$probe_output"
printf '%s\n' "$probe_output" | grep -qx AUTOTRADER_READ_ONLY_CONNECTION_OK
