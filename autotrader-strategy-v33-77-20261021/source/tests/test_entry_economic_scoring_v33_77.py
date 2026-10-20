from pathlib import Path
import json
import pytest
import score_entry_economics_v33_77 as s

def test_net_pf_average_payoff_and_drawdown_use_chronological_closes():
    rows=[dict(id=str(i),exit_ms=i,net=p,quantity_coin=10.,entry_price=100.) for i,p in enumerate([10.,-5.,2.])]
    m=s.metrics(rows[::-1])
    assert m['net']==7 and m['pf']==2.4
    assert m['average_win_loss_ratio']==1.2 and m['realized_close_curve_drawdown']==5
    assert s.metrics(rows,1)['net']==pytest.approx(6.7)

def test_no_loss_sample_never_reports_a_finite_profit_factor():
    assert s.metrics([dict(exit_ms=0,net=2.)])['pf'] is None
    assert s.metrics([])['net']==0

def test_model_error_cannot_receive_credit_for_avoiding_a_losing_trade(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(s,'ROOT',tmp_path)
    labels=[dict(id=k,exit_ms=i,net=v,quantity_coin=1.,entry_price=100.,symbol='BTC',side='long',
        entry_time='2026-10-18',exit_time='2026-10-18') for i,(k,v) in enumerate([('win',10.),('skipped',-5.),('error',-3.)])]
    (tmp_path/'replay_labels.json').write_text(json.dumps(labels))
    (tmp_path/'replay_inputs.jsonl').write_text('\n'.join(json.dumps(dict(id=x['id'],eligible=True,prompt_sha256=x['id'])) for x in labels))
    results=[dict(id=k,prompt_sha256=k,result=dict(decision=d),elapsed_s=1.,error='timeout' if k=='error' else None) for k,d in [('win','approve_now'),('skipped','wait'),('error','error_keep_baseline')]]
    (tmp_path/'replay_model_results_v3.jsonl').write_text('\n'.join(json.dumps(x) for x in results))
    s.main()
    out=json.loads((tmp_path/'economic_screen_v33_77.json').read_text())
    assert out['comparison']['all_eligible']['0bps']['candidate']['net']==7
    assert out['live_promotion_allowed'] is False
