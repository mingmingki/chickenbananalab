"""Economic screen of frozen entry proposals; no order or model calls."""
from pathlib import Path
import json,math,statistics,collections
ROOT=Path('/tmp/v33_77_profitability_20261021')
def metrics(rows,extra_bps=0):
    vals=[]
    unknown=0
    for x in sorted(rows,key=lambda x:x['exit_ms']):
        n=x.get('quantity_coin');p=x.get('entry_price')
        notional=float(n)*float(p) if n is not None and p is not None else None
        if extra_bps and notional is None:unknown+=1
        vals.append(float(x['net'])-(notional or 0)*extra_bps/10000)
    wins=[x for x in vals if x>0];losses=[x for x in vals if x<0]
    balance=peak=drawdown=0.
    for x in vals:
        balance+=x;peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    return dict(n=len(vals),net=round(sum(vals),6),pf=round(sum(wins)/-sum(losses),6) if losses else None,
        win_pct=round(100*len(wins)/len(vals),3) if vals else None,
        average_win=round(statistics.mean(wins),6) if wins else None,
        average_loss=round(statistics.mean(losses),6) if losses else None,
        average_win_loss_ratio=round(statistics.mean(wins)/-statistics.mean(losses),6) if wins and losses else None,
        realized_close_curve_drawdown=round(drawdown,6),missing_stress_notional=unknown)
def read_lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
def main():
    inputs={x['id']:x for x in read_lines(ROOT/'replay_inputs.jsonl')}
    labels=json.loads((ROOT/'replay_labels.json').read_text())
    records=read_lines(ROOT/'replay_model_results_v3.jsonl')
    results={x['id']:x for x in records if x.get('prompt_sha256')==inputs.get(x['id'],{}).get('prompt_sha256')}
    eligible=[x for x in labels if inputs[x['id']]['eligible']]
    pending=[x['id'] for x in eligible if x['id'] not in results]
    def accepted(x):
        result=results.get(x['id'],{}).get('result',{})
        # Errors/pending retain observed baseline; never credit them as avoided loss.
        return result.get('decision') not in ('wait','reject')
    candidate=[x for x in eligible if accepted(x)]
    skipped=[x for x in eligible if not accepted(x)]
    out=dict(mode='RECORDED_ENTRY_POLICY_SCREEN_NOT_FULL_STRATEGY_BACKTEST',
        source_candidate_commit='ea332ce87de538d2054c85ee64a85e0c58f89c10',
        total_lifecycles=len(labels),eligible=len(eligible),pending=pending,
        status='COMPLETE' if not pending else 'IN_PROGRESS',
        original_before_Oct13_eligible=sum(x['exit_time']<'2026-10-13' for x in eligible),
        decisions=dict(collections.Counter(x['result'].get('decision') for x in results.values())))
    out['comparison']={}
    groups=[('all_eligible',eligible),('earlier_Oct13_to16',[x for x in eligible if x['exit_time']<'2026-10-17']),
        ('later_Oct17_to20',[x for x in eligible if x['entry_time']>='2026-10-17'])]
    for name,pop in groups:
        keep=[x for x in pop if accepted(x)]
        out['comparison'][name]={str(bps)+'bps':dict(baseline=metrics(pop,bps),candidate=metrics(keep,bps)) for bps in [0,5,15]}
    out['by_symbol']={s:dict(baseline=metrics([x for x in eligible if x['symbol']==s]),
        candidate=metrics([x for x in candidate if x['symbol']==s])) for s in ['BTC','ETH','XRP','ADA']}
    out['by_side']={s:dict(baseline=metrics([x for x in eligible if x['side']==s]),
        candidate=metrics([x for x in candidate if x['side']==s])) for s in ['long','short']}
    out['skipped_winners']=metrics([x for x in skipped if x['net']>0])
    out['skipped_losers']=metrics([x for x in skipped if x['net']<0])
    out['accepted_ids']=[x['id'] for x in candidate]
    out['call_measurements']={}
    for file in ['replay_model_results.jsonl','replay_model_results_v2.jsonl','replay_model_results_v3.jsonl']:
        rows=read_lines(ROOT/file)
        times=[x['elapsed_s'] for x in rows]
        out['call_measurements'][file]=dict(attempts=len(rows),errors=sum(bool(x.get('error')) for x in rows),
            prompt_tokens=sum(x.get('usage',{}).get('prompt_tokens',0) for x in rows),
            completion_tokens=sum(x.get('usage',{}).get('completion_tokens',0) for x in rows),
            mean_s=round(statistics.mean(times),3) if times else None,
            max_s=max(times) if times else None)
    c=out['comparison']
    flags={}
    for name in ['earlier_Oct13_to16','later_Oct17_to20']:
        a=c[name]['0bps'];b=a['baseline'];n=a['candidate']
        flags[name]=bool(n['n']>=10 and n['net']>0 and n['net']>b['net'] and n['pf'] is not None and n['pf']>1)
    stressed=c['later_Oct17_to20']['15bps']['candidate']
    flags['later_15bps_positive']=bool(stressed['net']>0)
    out['diagnostic_screen_checks']=flags
    out['live_promotion_allowed']=False
    out['promotion_reason']='Original pre-Oct13 training coverage is zero and complete closed-loop strategy interactions are not replayed. No live promotion from this screen.'
    out['limitations']=[
        'Observed historical Gemini entry proposals only; rejected/HOLD proposal opportunities absent.',
        'Original main Gemini rationale and external market-context snapshots unavailable; price rationale is retained.',
        'Original management exits/quantities retained for accepted entries; revised Gemini and management outputs are not economically reproduced.',
        'Some original protection/funding provenance is incomplete in the historical journal.',
        'Drawdown is the cumulative realized-close curve, not mark-to-market account-equity drawdown.',
        'Chronological diagnostic split is retrospective; no untouched forward evidence.']
    (ROOT/'economic_screen_v33_77.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
