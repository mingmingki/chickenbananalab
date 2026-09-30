import datetime as dt
from types import SimpleNamespace

import ai_strategy_review as review
import gemini_analyzer
import openai_analyzer

KST = dt.timezone(dt.timedelta(hours=9))
START = dt.datetime(2026,9,24,12,0,tzinfo=KST)
END = dt.datetime(2026,9,24,18,0,tzinfo=KST)


def _analysis():
    return {'summary':{'completed_trades':1},'coverage':{'unmatched_or_excluded':0},'groups':[{'condition':'side:short','count':50,'sample_class':'established_sample','win_rate':55,'profit_factor':1.2,'net_pnl':10}], 'trades':[{'trade_id':'t1','exit_time':'2026-09-24T15:00:00','net_pnl':2.0}]}


def _cfg():
    return SimpleNamespace(GEMINI_API_KEY='g',OPENAI_API_KEY='o',GEMINI_MODEL='gemini-test',OPENAI_MODEL='gpt-test',user_dir='/tmp/u',logger=None)


def test_review_prompts_forbid_execution_changes():
    gp = gemini_analyzer.STRATEGY_REVIEW_PROMPT_TEMPLATE.lower()
    op = openai_analyzer.STRATEGY_REVIEW_PROMPT_TEMPLATE.lower()
    assert 'do not change' in gp and 'do not change' in op
    assert 'min_confidence' not in gemini_analyzer.STRATEGY_REVIEW_FIELDS
    assert 'order' not in openai_analyzer.STRATEGY_REVIEW_FIELDS


def test_gemini_and_gpt_receive_same_snapshot_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(review.trade_pattern_analysis, 'analyze', lambda _: _analysis())
    monkeypatch.setattr(review.strategy_learning, 'latest_hypotheses', lambda _: {})
    seen = []
    monkeypatch.setattr(review.gemini_analyzer, 'review_strategy_report', lambda cfg,payload: seen.append(('g',payload)) or {'observations':['g'],'confidence':0.7})
    monkeypatch.setattr(review.openai_analyzer, 'review_strategy_report', lambda cfg,payload: seen.append(('o',payload)) or {'observations':['o'],'confidence':0.8})
    result = review.generate_review(str(tmp_path), _cfg(), START, END)
    assert result['status'] == 'complete'
    assert result['gemini']['input_hash'] == result['gpt']['input_hash']
    assert seen[0][1] == seen[1][1]
    assert result['gemini']['review']['observations'] == ['g']
    assert result['gpt']['review']['observations'] == ['o']


def test_one_model_failure_is_contained(tmp_path, monkeypatch):
    monkeypatch.setattr(review.trade_pattern_analysis, 'analyze', lambda _: _analysis())
    monkeypatch.setattr(review.strategy_learning, 'latest_hypotheses', lambda _: {})
    monkeypatch.setattr(review.gemini_analyzer, 'review_strategy_report', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('down')))
    monkeypatch.setattr(review.openai_analyzer, 'review_strategy_report', lambda *a,**k: {'observations':['ok'],'confidence':0.7})
    result = review.generate_review(str(tmp_path), _cfg(), START, END)
    assert result['status'] == 'partial'
    assert result['gemini']['status'] == 'error'
    assert result['gpt']['status'] == 'ok'


def test_scheduled_review_updates_learning_memory_from_same_analysis(tmp_path, monkeypatch):
    analysis=_analysis()
    monkeypatch.setattr(review.trade_pattern_analysis, 'analyze', lambda _: analysis)
    calls=[]
    monkeypatch.setattr(review.strategy_learning, 'update_hypotheses', lambda user_dir,payload: calls.append((user_dir,payload)) or [])
    monkeypatch.setattr(review.strategy_learning, 'latest_hypotheses', lambda _: {})
    monkeypatch.setattr(review.gemini_analyzer, 'review_strategy_report', lambda *_a,**_k: {'observations':['g'],'confidence':0.7})
    monkeypatch.setattr(review.openai_analyzer, 'review_strategy_report', lambda *_a,**_k: {'observations':['o'],'confidence':0.8})
    result=review.generate_review(str(tmp_path), _cfg(), START, END)
    assert result['status']=='complete'
    assert calls==[(str(tmp_path),analysis)]
