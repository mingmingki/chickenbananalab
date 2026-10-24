# CORE unified deployment

This release is deployed with the systemd service stopped. Deployment does not
place orders, cancel protection orders, close positions, or start trading.

## Operator start

From the configured Mac terminal:

```sh
gcloud compute ssh autotrader-vm --zone=us-central1-a --project=autotrader-okx-gemini-0037 --command='sudo systemctl start autotrader.service'
```

This starts the dashboard process. The existing application starts CORE trading
only when the authenticated operator presses Start. Candidate C has its own Start.

## Behavior

- Flat CORE symbols: a fresh closed 1-minute signal with 3/5-minute confirmation
  requests asynchronous Gemini/GPT review. Both long and short probes use 25% of
  normal target position quantity. Approval expires after 60 seconds, and an
  executable quote that has advanced 0.25 of 5-minute ATR invalidates entry.
- Risk management runs independently of AI reviews. Favorable movement of 0.5R
  arms protection. Confirmed weakness reduces the quantity held at arming by
  25%, then another 25% after additional hourly weakness. Profitable additions
  use fresh approval and fixed lifecycle risk limits including estimated costs.
- Reversal closes the existing position first. A new position requires verified
  flat status, owned-stop cleanup and a fresh approval; no reused stale approval.
- Existing legacy positions remain under the old manager until the exchange
  position, open orders, protections and pending journals are all clear. Their
  prior peak profit and initial risk are not guessed during deployment.
- A durable SQLite owner blocks conflicting legacy writes. Exact order/fill IDs
  reconcile ambiguous submissions. Protection failures retain the kill switch.
- Unified realized PNL is recorded in SQLite and included in the CORE daily-loss
  gate alongside the legacy journal. Fees are conservatively estimated, not
  represented as actual exchange fees. Legacy trade-history views do not yet
  render these new SQLite trades; live position display still reads the exchange.

## Stop and rollback

For code maintenance, use `sudo systemctl stop autotrader.service`. Do not use
the dashboard Stop button for a non-liquidating deployment: that button requests
position closure. Existing exchange-hosted stops remain at the exchange.

`UNIFIED_DEPLOYMENT.json` in the installed release records the previous release,
configuration backup, archive checksum and source hashes. Roll back only while
the service is stopped. Once a unified lifecycle has traded, do not switch to a
legacy release that lacks the durable-owner guard while that lifecycle remains
unresolved. Preserve users, SQLite/WAL files, journals and exchange protections.

## Limits

This is functional code with deterministic and regression tests, not profitability
or real-fill performance validation. No real orders are used for deployment QA.
Missing or incomplete exchange history blocks reconciliation rather than guessing.
The first 100 fills must explain cumulative order quantity; larger paginated fill
histories require operator recovery. Net position mode is required.
