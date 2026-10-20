# CORE v33-78 — reproducible profitability qualification research

Date: 2026-10-21 KST

**Status: NO GO for live strategy promotion.** This branch contains an offline read-only diagnostic, not a trading policy or a claimed profitable strategy. Production v33-77 was not changed by this work.

## Data / method

- Revisited a preserved v33-77 research archive on the authorized VM, without publishing trade records.
- 363 completed CORE BTC/ETH/XRP/ADA historical lifecycles, arranged chronologically: first 226 diagnostic training rows and 137 later validation rows.
- Examined baseline, short-only, long-in-trend, short-in-trend, and avoiding opposing 1h trend. These are observational selections using the **existing actual exits**, not end-to-end counterfactual executions.
- Re-evaluated 81 frozen rule/period/cost cells: 3 trend entries × 3 exits × 3 chronological periods × 3 roundtrip friction levels (10/15/25bps).
- A promotion requires all three chronological periods to have at least 20 trades and strictly positive net/PF>1 **at 25bps costs**, and still requires separate full closed-loop prospective verification.

## Results

- Baseline recorded CORE 363 trades: **-248.47 USDT; PF 0.845**.
- Recorded LONG: 209 trades, **-257.91 USDT; PF 0.780**; SHORT: 154 trades, **+9.43 USDT; PF 1.022**. Short performance was not stable across time.
- Short aligned with 1h trend: 52 early trades **-29.32 USDT; PF 0.795**; 59 later trades **+112.94 USDT; PF 1.761**. This is NOT proof that dynamically shorting the regime would have yielded this PnL, since entries and exits are counterfactual.
- Prior GPT screen: 106 eligible proposals, 17 approvals. At extra 15bps friction early approved 7 produced +5.62 USDT; later approved 10 produced -42.13 USDT.
- **Zero of the 25bps cost-stress strategy candidates** passed the all-period positive evidence gate; no live strategy promotion.
- Five independent synthetic tests pass. Code creates reports only; cannot place/cancel orders, change stops, or size trades.

## Reproduction

Run with the retained **private** source bundle stored on the authorized VM (no exchange API calls):

```sh
python3 chickenbanana_v33_78_profitability_gate.py \
  --archive /path/to/profitability_followup_data_sources_results.tar.gz \
  --output /path/to/review.json
python3 chickenbanana_v33_78_profitability_gate_test.py
```

VM evidence directory:

`/var/lib/autotrader/deployment-evidence/core_profitability_gate_v33_78_20261021KST/`

The original order engine and v33-77 live release remain unchanged. The current data cannot identify a consistently profitable long/short system; next necessary work is frozen, forward-logged decision/entry/exit cohorts with exact OKX fills, fees, funding, protection state, and no look-ahead.
