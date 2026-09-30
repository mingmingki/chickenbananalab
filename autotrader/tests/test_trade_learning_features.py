import json
from pathlib import Path

from trade_learning_features import enrich_lifecycle, classify_tf_state


def _append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def test_feature_join_never_uses_future_record(tmp_path):
    p = Path(tmp_path) / 'market_structure_log.jsonl'
    _append(p, {'symbol':'XRP/USDT:USDT','time':'2026-09-24T09:59:59','structures':{'5m':{'closed':{'trend':'bearish'}}},'gemini_action':'short','gemini_confidence':0.7})
    _append(p, {'symbol':'XRP/USDT:USDT','time':'2026-09-24T10:00:01','structures':{'5m':{'closed':{'trend':'bullish'}}},'gemini_action':'long','gemini_confidence':0.9})
    lifecycle = {'trade_id':'t1','symbol':'XRP/USDT:USDT','side':'short','entry_time':'2026-09-24T10:00:00','strategy_group':'core','events':[{'type':'open','time':'2026-09-24T10:00:00'}]}
    result = enrich_lifecycle(str(tmp_path), lifecycle)
    assert result['features']['market_structure'] == 'bearish'
    assert result['features']['gemini_action'] == 'short'


def test_missing_observation_is_unknown_not_guessed(tmp_path):
    lifecycle = {'trade_id':'t2','symbol':'BTC/USDT:USDT','side':'long','entry_time':'2026-09-24T10:00:00','events':[]}
    result = enrich_lifecycle(str(tmp_path), lifecycle)
    assert result['features']['tf']['5m']['state'] == 'unknown'
    assert result['features']['coverage']['candle_finality'] is False


def test_tf_state_uses_closed_candle_only():
    state = classify_tf_state({'closed': {'close': 90, 'ema20': 95, 'ema50': 100, 'macd': -2}, 'live': {'close': 110, 'ema20': 105, 'ema50': 100, 'macd': 2}})
    assert state == 'bearish'


def test_unsorted_archive_future_record_cannot_win(tmp_path):
    archive = Path(tmp_path) / 'market_structure_log.jsonl.20260923_000000_000001'
    _append(archive, {'symbol':'BTC/USDT:USDT','time':'2026-09-24T10:00:02','structures':{'5m':{'closed':{'trend':'bullish'}}}})
    _append(archive, {'symbol':'BTC/USDT:USDT','time':'2026-09-24T09:59:58','structures':{'5m':{'closed':{'trend':'bearish'}}}})
    lifecycle = {'trade_id':'t3','symbol':'BTC/USDT:USDT','side':'short','entry_time':'2026-09-24T10:00:00','events':[]}
    result = enrich_lifecycle(str(tmp_path), lifecycle)
    assert result['features']['market_structure'] == 'bearish'
