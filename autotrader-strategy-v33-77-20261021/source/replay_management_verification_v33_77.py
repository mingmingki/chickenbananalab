"""Read-only verification of recorded Gemini exits; never imports an order client."""
from pathlib import Path
import json,sys,datetime as dt,logging,concurrent.futures as cf,time
import config,openai_analyzer,core_ai_context,core_management_context_v33_77 as context
import core_entry_economics_v33_77 as economics
import replay_entry_profitability_v33_77 as replay
import trade_learning_lifecycle as ll
ROOT=Path('/tmp/v33_77_profitability_20261021')
DATA=Path('/tmp/v33_77_strategy_20261021/data')
CS={'BTC':.01,'ETH':.1,'XRP':100.,'ADA':100.}
def prepare():
    accepted=set(json.loads((ROOT/'economic_screen_v33_77.json').read_text())['accepted_ids'])
    lives=ll.build_completed_lifecycles(str(DATA))
    reviews=[json.loads(x) for x in (DATA/'position_ai_log.jsonl').read_text().splitlines()]
    prices={c:replay.prices(c) for c in CS}
    one={}
    for c in CS:
        one[c]=json.loads((ROOT/(c+'_1m.json')).read_text())['rows']
    rows=[];excluded=[]
    for life in lives:
        if life['trade_id'] not in accepted or life['final_close_reason']!='position_ai_close_all' or life['lifecycle_net']>=0:continue
        coin=life['symbol'].split('/')[0];final=life['events'][-1];opened=life['events'][0]
        proof=final.get('close_execution_evidence')
        if not isinstance(proof,dict) or not proof.get('order_id') or not life.get('exit_price'):
            excluded.append(dict(id=life['trade_id'],reason='unverified_exit_fill'));continue
        candidates=[x for x in reviews if x.get('symbol')==life['symbol'] and x.get('action')=='CLOSE_ALL' and life['entry_time']<=x['time']<=life['exit_time']]
        if not candidates:excluded.append(dict(id=life['trade_id'],reason='no_original_review'));continue
        review=max(candidates,key=lambda x:x['time'])
        at=replay.decision_at(review['review_id'])
        if not at:excluded.append(dict(id=life['trade_id'],reason='unknown_review_time'));continue
        initial_contracts=opened['amount']/CS[coin]
        prior=[x for x in life['events'][1:-1] if x['time']<=review['time'] and x.get('type')=='reduce']
        remaining=initial_contracts-sum(float(x['amount']) for x in prior)
        filled=float(proof['filled_contracts'])
        if abs(remaining-filled)>max(.01*filled,1e-7):
            excluded.append(dict(id=life['trade_id'],reason='quantity_not_reconciled',remaining=remaining,filled=filled));continue
        frames=replay.snapshot(prices[coin],at);asof=dt.datetime.fromtimestamp(at/1000,dt.timezone.utc)
        mark=float(frames['1m'].iloc[-1].close)
        entry=life['entry_price'];sign=1 if life['side']=='long' else -1
        stop=opened['sl_price'];risk=abs(entry-stop);qty=remaining*CS[coin]
        path=[x for x in one[coin] if x[0]>=replay.epoch(life['entry_time']) and x[0]+60000<=at-3000]
        mfe=max(0.,max((sign*((x[2] if sign==1 else x[3])-entry)/risk for x in path),default=0.))
        mae=max(0.,max((-sign*((x[3] if sign==1 else x[2])-entry)/risk for x in path),default=0.))
        position=dict(side=life['side'],contracts=remaining,entry_price=entry,mark_price=mark,last_price=mark,
            unrealized_pnl=sign*(mark-entry)*qty,initial_contracts=initial_contracts)
        protection=dict(sl_price=stop,tp_price=opened['tp_price'],last_price=mark,mark_price=mark,
            source='recorded initial bracket; subsequent amendments unknown in historical archive',
            current_order_status_verified=False)
        summary=economics.market_summary(frames,now=asof)
        evidence=dict(initial_contracts=initial_contracts,remaining_contracts=remaining,prior_reduction_count=len(prior),
            current_r=sign*(mark-entry)/risk,mfe_r=mfe,mae_r=mae,giveback_r=mfe-sign*(mark-entry)/risk,
            realized_reductions_net=sum(float(x.get('okx_net_pnl',0) or 0) for x in prior),
            current_mark_source='last confirmed1m close before original review; actual later exit fill excluded',
            protection_provenance='initial bracket known; current pending order snapshot unavailable')
        summary+='\n[POSITION_MANAGEMENT_EVIDENCE]\n'+json.dumps(evidence)+'\nUnknown evidence is not zero.\n'
        summary=context.with_context(summary,economics.structure(life['side'],frames,now=asof))
        proposal=dict(action='CLOSE_ALL',confidence=review['confidence'],reasoning=review['reasoning'])
        summary+='\n[PROPOSED_MANAGEMENT_PLAN]\n'+json.dumps(proposal,ensure_ascii=False)
        rows.append(dict(id=life['trade_id'],symbol=life['symbol'],review_id=review['review_id'],at=at,
            position=position,protection=protection,summary=summary,
            gemini_decision=dict(action='close',confidence=review['confidence'],reasoning=review['reasoning']),
            gemini_review=dict(assessment='invalidated',confidence=review['confidence'],reasoning=review['reasoning'],
                management_action='CLOSE_ALL',risk_level='high',suggested_reduce_fraction=0.),
            proposal=proposal,original_net=life['lifecycle_net'],entry_time=life['entry_time'],exit_time=life['exit_time'],
            original_exit_fill=life['exit_price'],remaining_coin=qty,initial_stop=stop,initial_tp=opened['tp_price'],
            evidence=evidence))
    (ROOT/'management_replay_inputs.json').write_text(json.dumps(dict(rows=rows,excluded=excluded),ensure_ascii=False,indent=2))
    print('PREPARED',len(rows),'EXCLUDED',excluded,flush=True)
    return rows
def run_one(cfg,row):
    start=time.monotonic()
    verdict=openai_analyzer.verify_position_management(cfg,row['symbol'],['1m','3m','5m','15m','1h','4h','1d'],
        row['summary'],row['position'],row['gemini_decision'],row['gemini_review'],timeout=20.,max_retries=0,
        protection=row['protection'],allowed_actions=('HOLD','CLOSE_ALL'),purpose='core_management_verification',review_path='historical_verified_exit')
    approved=context.verified_proposal(row['proposal'],verdict,cfg.MIN_CONFIDENCE)
    return dict(id=row['id'],symbol=row['symbol'],original_action='CLOSE_ALL',verdict=verdict,
        candidate_action=approved['action'],elapsed_s=round(time.monotonic()-start,3))
def main():
    rows=prepare();folder=ROOT/'management_call_audit';folder.mkdir(exist_ok=True)
    cfg=config.UserConfig('/opt/autotrader/users/chickenbananalab');cfg.user_dir=str(folder);cfg.logger=logging.getLogger('isolated_management_replay')
    openai_analyzer._ensure_response_models_warmed_up();openai_analyzer._get_client(cfg)
    path=ROOT/'management_replay_results.jsonl'
    done={json.loads(x)['id'] for x in path.read_text().splitlines()} if path.exists() else set()
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_one,cfg,row) for row in rows if row['id'] not in done]
        for f in cf.as_completed(futures):
            out=f.result()
            with path.open('a') as stream:stream.write(json.dumps(out,ensure_ascii=False)+'\n')
            print('RESULT',out['id'],out['candidate_action'],out['verdict'].get('error_reason'),out['elapsed_s'],flush=True)
    print('FINISHED',flush=True)
if __name__=='__main__':main()
