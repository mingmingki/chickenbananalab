import analysis_report
import entry_tf_daily_backfill as daily
import trade_learning_cache
import trade_learning_features as features
import trade_pattern_analysis

SYM='BTC/USDT:USDT'

def _life(tid='t1'):
    return {'trade_id':tid,'symbol':SYM,'side':'long','entry_time':'2026-09-30T10:00:00','events':[{'type':'open'}]}

def _sources(with_gpt=True):
    gpt=[{'symbol':SYM,'time':'2026-09-30T09:59:00','gpt_decision':'approve_now','gpt_confidence':0.8}] if with_gpt else []
    return {'market':[],'candle':[],'gpt':gpt,'posai':[],
            'lowfollow_by_trade':{'t1':{'states':{'3m':'bullish','5m':'bullish','1h':'bullish','4h':'mixed'}}},
            'daily_by_trade':{'t1':{'resolved':True,'state_1d':'bullish'}}}

def test_asof_backfill_completes_analysis_tf_without_faking_raw_source():
    out=features.enrich_lifecycle('/tmp',_life(),sources=_sources())
    assert [out['features']['tf'][x]['state'] for x in ('3m','5m','1h','4h','1d')]==['bullish','bullish','bullish','mixed','bullish']
    c=out['features']['coverage']
    assert c['candle_finality'] is False
    assert c['asof_tf_backfill'] is True
    assert c['analysis_tf_complete'] is True

def test_analysis_tf_complete_still_requires_real_gpt_evidence():
    out=features.enrich_lifecycle('/tmp',_life(),sources=_sources(with_gpt=False))
    assert out['features']['coverage']['analysis_tf_complete'] is False


def test_coverage_reports_raw_and_asof_completeness_separately():
    row={'coverage':'complete','features':{'coverage':{'market_structure':False,'candle_finality':False,'gpt':True,
         'analysis_tf_complete':True,'asof_tf_backfill':True}}}
    c=trade_pattern_analysis._coverage([row])
    assert c['feature_complete']==0
    assert c['tf_analysis_complete']==1
    assert c['asof_backfilled']==1


def test_learning_cache_tracks_backfill_journals():
    assert 'low_follow_through_shadow.jsonl*' in trade_learning_cache.SOURCE_PATTERNS
    assert 'entry_tf_daily_backfill.jsonl*' in trade_learning_cache.SOURCE_PATTERNS

def test_daily_backfill_is_shadow_only_and_idempotent(tmp_path):
    created=daily.refresh(str(tmp_path),[_life()],lambda symbol,entry:'bullish',limit=10)
    assert created==1
    row=daily.load_latest(str(tmp_path))['t1']
    assert row['resolved'] is True and row['state_1d']=='bullish'
    assert row['live_authority'] is False
    assert daily.refresh(str(tmp_path),[_life()],lambda symbol,entry:'bearish',limit=10)==0


def test_report_shows_raw_and_tf_analysis_coverage():
    snap={'release':'r','period':'all','coverage':{'feature_complete':9,'tf_analysis_complete':30,'asof_backfilled':21},
          'overall':{},'symbols':[],'sides':[],'top_positive':[],'top_negative':[],'tf':[],'confidence':[],
          'self_learning':{},'review':{},'settings':{},'positions':[],'system_issues':{}}
    text=analysis_report.build_report(snap)['text']
    assert '원본 feature 완전결합: 9' in text
    assert 'TF 분석완전(원본+as-of): 30' in text
    assert 'as-of TF backfill: 21' in text
