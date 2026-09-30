import datetime as dt
import hashlib, json
from pathlib import Path
from types import SimpleNamespace

import ai_strategy_review, trade_learning_cache, trade_pattern_analysis, strategy_learning

KST=dt.timezone(dt.timedelta(hours=9))

def _write(path, rows):
    with Path(path).open('w',encoding='utf-8') as f:
        for row in rows: f.write(json.dumps(row,ensure_ascii=False)+'\n')

def _seed(user):
    _write(user/'trades_log.jsonl',[
        {'type':'open','symbol':'BTC/USDT:USDT','side':'short','price':100,'amount':2,'dry_run':False,'strategy_group':'core','market_regime':'bearish','trade_alignment':'with_regime','time':'2026-09-24T10:00:00'},
        {'type':'reduce','symbol':'BTC/USDT:USDT','side':'short','entry_price':100,'amount':1,'pnl':2,'fee':0.1,'okx_net_pnl':1.9,'dry_run':False,'strategy_group':'core','time':'2026-09-24T10:20:00'},
        {'type':'close','symbol':'BTC/USDT:USDT','side':'short','entry_price':100,'amount':1,'pnl':3,'fee':0.1,'okx_net_pnl':2.9,'close_price':97,'dry_run':False,'strategy_group':'core','time':'2026-09-24T10:40:00'},
    ])
    _write(user/'market_structure_log.jsonl.20260924_100000_000001',[{'symbol':'BTC/USDT:USDT','time':'2026-09-24T09:59:50','gemini_action':'short','gemini_confidence':0.8,'structures':{'5m':{'closed':{'trend':'bearish'}}}}])
    _write(user/'candle_finality_log.jsonl.20260924_100000_000001',[{'symbol':'BTC/USDT:USDT','time':'2026-09-24T09:59:55','gemini_action':'short','gemini_confidence':0.8,'indicators':{'3m':{'closed':{'close':90,'ema20':95,'ema50':100,'macd':-1}},'5m':{'closed':{'close':90,'ema20':95,'ema50':100,'macd':-1}},'1h':{'closed':{'close':90,'ema20':95,'ema50':100,'macd':-1}}}}])
    _write(user/'gpt_shadow_log.jsonl',[{'symbol':'BTC/USDT:USDT','time':'2026-09-24T09:59:58','gemini_action':'short','gemini_confidence':0.8,'gpt_decision':'approve_now','gpt_confidence':0.75,'gate_result':'approved'}])

def _hash(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def test_end_to_end_backfill_builds_analysis_learning_and_review(tmp_path, monkeypatch):
    _seed(tmp_path)
    analysis=trade_pattern_analysis.analyze(str(tmp_path))
    hypotheses=strategy_learning.update_hypotheses(str(tmp_path),analysis)
    monkeypatch.setattr(ai_strategy_review.gemini_analyzer,'review_strategy_report',lambda *_a,**_k:{'observations':['g'],'confidence':.7})
    monkeypatch.setattr(ai_strategy_review.openai_analyzer,'review_strategy_report',lambda *_a,**_k:{'observations':['o'],'confidence':.8})
    cfg=SimpleNamespace()
    review=ai_strategy_review.generate_review(str(tmp_path),cfg,dt.datetime(2026,9,24,6,tzinfo=KST),dt.datetime(2026,9,24,12,tzinfo=KST))
    assert analysis['summary']['completed_trades']==1
    assert analysis['coverage']['lifecycle_matched']==1
    assert analysis['coverage']['feature_complete']==1
    assert hypotheses
    assert review['status']=='complete'

def test_cache_analysis_does_not_modify_source_logs(tmp_path):
    _seed(tmp_path)
    source=[tmp_path/'trades_log.jsonl',tmp_path/'gpt_shadow_log.jsonl',tmp_path/'market_structure_log.jsonl.20260924_100000_000001',tmp_path/'candle_finality_log.jsonl.20260924_100000_000001']
    before={str(p):_hash(p) for p in source}
    payload=trade_learning_cache.run_analysis(str(tmp_path))
    after={str(p):_hash(p) for p in source}
    assert before==after
    assert payload['analysis']['coverage']['feature_complete']==1
    assert (tmp_path/'strategy_learning.jsonl').exists()
    assert (tmp_path/'trade_learning_analysis_cache.json').exists()


def test_feature_backfill_reads_each_source_family_once(tmp_path, monkeypatch):
    import trade_learning_features as features
    rows=[
        {'trade_id':'a','symbol':'BTC/USDT:USDT','side':'long','entry_time':'2026-09-24T10:00:00','events':[]},
        {'trade_id':'b','symbol':'ETH/USDT:USDT','side':'short','entry_time':'2026-09-24T10:01:00','events':[]},
    ]
    monkeypatch.setattr(features,'build_completed_lifecycles',lambda _u:rows)
    calls=[]
    monkeypatch.setattr(features,'_read_family',lambda _u,name: calls.append(name) or [])
    out=features.build_featured_trades(str(tmp_path))
    assert len(out)==2
    assert calls==['market_structure_log.jsonl','candle_finality_log.jsonl','gpt_shadow_log.jsonl','position_ai_log.jsonl','low_follow_through_shadow.jsonl','entry_tf_daily_backfill.jsonl']


def test_analysis_cache_reuses_unchanged_sources(tmp_path, monkeypatch):
    _seed(tmp_path)
    first=trade_learning_cache.run_analysis(str(tmp_path))
    def should_not_run(_user_dir):
        raise AssertionError('heavy analysis reran despite unchanged source fingerprint')
    monkeypatch.setattr(trade_learning_cache.trade_pattern_analysis,'analyze',should_not_run)
    second=trade_learning_cache.run_analysis(str(tmp_path))
    assert second['reused_cache'] is True
    assert second['generated_at']==first['generated_at']


def test_analysis_cache_fingerprint_includes_exit_reentry_shadow():
    import trade_learning_cache
    assert 'exit_reentry_shadow.jsonl*' in trade_learning_cache.SOURCE_PATTERNS
