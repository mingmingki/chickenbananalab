#!/bin/sh
# Installed root-owned outside the application. No application imports or writes.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
if [ "${SSH_ORIGINAL_COMMAND:-}" != autotrader-read-only-probe ]; then
    printf '%s\n' 'Only the read-only connection probe is allowed' >&2
    exit 126
fi
test -d /opt/autotrader
test "$(/usr/bin/systemctl show autotrader.service --property=LoadState --value)" = loaded
printf 'ProbeUser=%s\n' "$(/usr/bin/id -un)"
/usr/bin/systemctl show autotrader.service --property=ActiveState --property=SubState
printf '%s\n' AUTOTRADER_READ_ONLY_CONNECTION_OK
