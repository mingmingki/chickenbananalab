# v33-2 CORE close execution evidence

LIVE scope: legacy-owned CORE direct close with OKX create_order receipt ID. Does not affect routed unified close or manual close. No historic trades can be assigned order IDs without evidence.

A confirmed close price requires exact order ID, terminal closed status, full filled contract quantity, remaining zero, expected symbol and direction, all unique trade IDs and complete sum of their quantities. Any incompleteness results in None, retaining existing journal PnL and price behavior. The success path stores close_execution_evidence alongside close_price in the immutable append-only trade journal, and the AI shadow store carries order/trade IDs. Gross/net realized PnL is unchanged. Price is VWAP of order-ID-matched fills; no aggregate position-history price is misattributed to a partially reduced residual.

Follow-up is still necessary for unified controller and historical missing IDs. No changes to entry, leverage, SL/TP, account protection or placement logic.
