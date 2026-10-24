"""Independent scheduled metrics refresh. Never handles orders or modifies AI hypotheses."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import config
import ops_observability as obs
import trade_learning_cache

def main():
    user_dir = str(Path(config.PROJECT_DIR) / "users" /
                   os.environ.get("AUTOTRADER_OBS_USER","chickenbananalab"))
    analysis = trade_learning_cache.run_analysis(user_dir, update_hypotheses=False)
    now = dt.datetime.now(obs.KST)
    # v33-15: public OHLCV GET-only backfill of exchange-fill-proven exits.
    # Slow/failed exchange reads never interrupt existing analysis or trading.
    # Bounded to two 5m OHLCV requests per 15-minute scheduled run.
    try:
        import core_exit_shadow_backfill_v33_15 as exit_shadow_backfill
        exit_shadow_result = exit_shadow_backfill.backfill(
            user_dir,now=now,max_queries=2)
    except Exception as exc:
        exit_shadow_result={"status":"UNKNOWN","reason":type(exc).__name__}
    data = {
        "generated_at": now.isoformat(timespec="seconds"),
        "core_exit_shadow_backfill": exit_shadow_result,
        "analysis_generated_at": analysis.get("generated_at"),
        "analysis_cache_reused": bool(analysis.get("reused_cache")),
        "strategy": obs.build_strategy_summary(user_dir, now=now),
        "cost_efficiency": obs.build_cost_efficiency(user_dir, now=now),
    }
    dst = Path(user_dir) / "ops_analysis_metrics.json"
    tmp = dst.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    os.replace(tmp,dst)
    print(json.dumps({"ok":True,"generated_at":data["generated_at"],
                      "analysis_cached":data["analysis_cache_reused"]}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
