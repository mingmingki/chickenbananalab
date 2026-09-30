# Autotrader transition

The transition imports source and tests only. It does not change trading logic,
activate a release, stop/restart the service, change account settings, or call an
exchange. Existing live code, positions and exchange-hosted protection remain under
the existing operational process.

## Local verification

Use Python 3.11 in a development environment:

```sh
python3.11 -m pip install -r autotrader/requirements-ci.txt
PYTHON=python3.11 bash infra/autotrader/verify.sh
```

The runner uses a temporary project directory and a Python network-denial plugin,
removes inherited credential environment variables, and runs each test directory in
its own process because historical suites reuse fixture module names. It is an
accidental-I/O guard, not a sandbox for hostile native code. Source inventory checks
require the selected source files to be tracked/staged in Git.

`requirements-ci.txt` preserves the existing server freeze captured during the
previous migration work (Python 3.11). A fresh dependency installation still needs
verification on a network-connected runner. Runtime requirements are unchanged.

## Workflow

- `source-safety`: packaging/SSH/isolation tests, source inventory and shell syntax.
- `regression`: five independent offline suites; failures are never ignored.
- `package`: source-only archive and SHA256, only after all regressions pass.
- `server-connection`: transition-branch pushes/manual runs after source safety;
  strictly read-only. Application regressions do not block this transport check.

No job activates a release or deploys to production. The package is a candidate
artifact, not a complete production installer. Old one-time deployment utilities
are excluded because they stop the service and rewrite configuration.

## Existing VM connection

Set repository variables `AUTOTRADER_SSH_HOST` (existing VM host/IP) and
`AUTOTRADER_SSH_USER` (existing dedicated SSH user). Set Actions secrets:

- `AUTOTRADER_SSH_KEY`: dedicated private SSH key; never put it in source or chat.
- `AUTOTRADER_SSH_KNOWN_HOSTS`: trusted host-key entry for that exact hostname/IP,
  verified against the existing administrator connection; do not trust a fresh
  network scan without verification.

The probe uses SSH on port 22 with strict host-key validation and no sudo. It checks
`/opt/autotrader`, confirms that `autotrader.service` exists, prints ActiveState and
SubState, and requires the `AUTOTRADER_READ_ONLY_CONNECTION_OK` marker. This proves
SSH and service discovery, not application or exchange health. It never reads
credentials, process environments, account files, logs or transaction records.

A server-side dedicated key may be restricted to the exact command in `probe.sh`
with `restrict,command="..."` in authorized_keys. Account/key provisioning and any
firewall changes require existing administrator access; do not use the trading
application's credentials. This work does not alter VM IAM, firewall or SSH keys.

Push only `infra/autotrader-cicd`. Inspect that commit's Actions jobs. Missing secrets,
network access, authentication or a missing success marker must fail the check.
A push triggers this new workflow even before it is on the default branch.

## Production activation gate

Production activation remains blocked until application regressions, a clean runner
installation, source review and server checks pass. A later operator-approved
activation must account for active strategy processes and shared state. This
workflow deliberately has no stop/start/restart, symlink switch or account-config
mutation step; do not reuse the old deploy scripts as an automatic continuation.
