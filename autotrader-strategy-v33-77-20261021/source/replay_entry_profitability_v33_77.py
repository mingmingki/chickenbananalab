"""Frozen-entry replay. Isolated model calls; imports no trading/order client."""
from pathlib import Path
import concurrent.futures as cf
import datetime as dt
import hashlib,json,time,sys
import pandas as pd
from openai import OpenAI
import config,indicators,timeframes,core_entry_timing
import core_entry_economics_v33_77 as economics
import trade_learning_lifecycle as lifecycle
ROOT=Path('/tmp/v33_77_profitability_20261021')
DATA=Path('/tmp/v33_77_strategy_20261021/data')
SYMS=('BTC','ETH','XRP','ADA')
KST=dt.timezone(dt.timedelta(hours=9))
def epoch(t):
    x=dt.datetime.fromisoformat(t)
    if x.tzinfo is None:x=x.replace(tzinfo=KST)
    return int(x.timestamp()*1000)
def decision_at(decision_id):
    try:
        text=decision_id.rsplit('-',1)[1]
        if len(text)!=20 or not text.isdigit():return None
        return int(dt.datetime.strptime(text,'%Y%m%d%H%M%S%f').replace(tzinfo=KST).timestamp()*1000)
    except (ValueError,IndexError,AttributeError):return None
def prices(coin):
    rows={}
    for directory in [Path('/tmp/v33_77_profitability_warmup_20261021'),ROOT]:
        path=directory/(coin+'_5m.json')
        if path.exists():
            for row in json.loads(path.read_text())['rows']:rows[int(row[0])]=row
    f=pd.DataFrame([rows[k] for k in sorted(rows)],columns=['ms','open','high','low','close','volume'])
    f.index=pd.to_datetime(f.ms,unit='ms',utc=True)
    frames={}
    for tf,rule,n,sec in [('5m','5min',1,300),('15m','15min',3,900),('1h','1h',12,3600),('4h','4h',48,14400)]:
        x=f.resample(rule,origin='epoch').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
        count=f.close.resample(rule,origin='epoch').count()
        x=x[count==n].copy();x['ms']=[int(t.timestamp()*1000) for t in x.index]
        x['timestamp']=x.index;x['end']=x.ms+sec*1000;frames[tf]=x
    # Native daily anchor and recent 1m/3m timing match the runtime ladder.
    one_path=ROOT/(coin+'_1m.json')
    if one_path.exists():
        one=pd.DataFrame(json.loads(one_path.read_text())['rows'],columns=['ms','open','high','low','close','volume'])
        one.index=pd.to_datetime(one.ms,unit='ms',utc=True)
        for tf,rule,n,sec in [('1m','1min',1,60),('3m','3min',3,180)]:
            x=one.resample(rule,origin='epoch').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
            count=one.close.resample(rule,origin='epoch').count()
            x=x[count==n].copy();x['ms']=[int(t.timestamp()*1000) for t in x.index]
            x['timestamp']=x.index;x['end']=x.ms+sec*1000;frames[tf]=x
    daily_path=ROOT/(coin+'_1d.json')
    if daily_path.exists():
        x=pd.DataFrame(json.loads(daily_path.read_text())['rows'],columns=['ms','open','high','low','close','volume'])
        x['timestamp']=pd.to_datetime(x.ms,unit='ms',utc=True);x['end']=x.ms+86400000;frames['1d']=x
    return frames
def snapshot(raw,at):
    out={}
    for tf,frame in raw.items():
        # Runtime fetches 200 native bars including the currently open one.
        native=frame[frame.ms<=at].tail(200)
        f=native[native.end<=at-3000].copy()
        if len(f)<195:continue
        out[tf]=indicators.add_indicators(f).reset_index(drop=True)
    return out
def prepare():
    raw={s:prices(s) for s in SYMS}
    shadow={}
    f=DATA/'profitability_gpt_shadow_frozen.jsonl'
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                x=json.loads(line)
                if x.get('decision_id'):shadow[x['decision_id']]=x
            except (ValueError,TypeError):pass
    lives=lifecycle.build_completed_lifecycles(str(DATA))
    rows=[];labels=[];checks=[]
    for life in sorted(lives,key=lambda x:x.get('entry_time') or ''):
        if life.get('coverage')!='complete' or life.get('strategy_group')!='core':continue
        if not '2026-08-28'<=life['exit_time']<'2026-10-21':continue
        coin=life['symbol'].split('/')[0]
        if coin not in raw:continue
        opened=next((x for x in life['events'] if x.get('type')=='open'),None)
        if not opened:continue
        fill_at=epoch(life['entry_time']);entry=float(life['entry_price'])
        at=fill_at
        bar=raw[coin]['5m'][(raw[coin]['5m'].ms<=fill_at)&(raw[coin]['5m'].ms+300000>fill_at)]
        good=bool(len(bar)==1 and float(bar.iloc[0].low)<=entry<=float(bar.iloc[0].high))
        checks.append(dict(id=life['trade_id'],entry_in_raw_bar=good))
        decision_id=str(opened.get('execution_id','')).removeprefix('core-entry:')
        recorded=shadow.get(decision_id,{})
        original_at=decision_at(decision_id)
        at=original_at or fill_at
        delay=fill_at-at
        side=life['side']
        plan=recorded.get('gemini_exit_plan') or {}
        if not plan:
            plan=dict(stop_loss_price=opened.get('sl_price'),take_profit_1_price=opened.get('tp_price'),take_profit_2_price=opened.get('tp_price'))
        contract=recorded.get('reviewed_exit_price_contract')
        if not isinstance(contract,dict):contract={}
        # Only an original pre-approval reference quote is admissible.
        contract=dict(contract)
        known_quote=bool(contract.get('entry_price') and contract.get('estimated_roundtrip_cost_rate') is not None)
        decision=dict(action=side,confidence=recorded.get('gemini_confidence') or .7,
            exit_plan=plan,reasoning=plan.get('reasoning') or 'Original direction reasoning not retained; judge closed bars independently.')
        frames=snapshot(raw[coin],at)
        structure=economics.structure(side,frames,now=dt.datetime.fromtimestamp(at/1000,dt.timezone.utc))
        target_tfs=timeframes.for_interval(config.UserConfig('/opt/autotrader/users/chickenbananalab').POLL_INTERVAL_SECONDS)
        for tf in ['5m','15m','1h','4h']:
            if tf not in target_tfs:target_tfs.append(tf)
        frames={tf:frames[tf] for tf in target_tfs if tf in frames}
        asof=dt.datetime.fromtimestamp(at/1000,dt.timezone.utc)
        summary=economics.market_summary(frames,now=asof)
        summary+=core_entry_timing.format_prompt(core_entry_timing.evaluate(frames,now=asof))
        summary=economics.with_structure(summary,structure)
        prompt=economics.prompt(symbol=life['symbol'],frames=list(frames),position='none',decision=decision,summary=summary,short_context='',contract=contract)
        record=dict(id=life['trade_id'],decision_id=decision_id,at=at,symbol=coin,prompt=prompt,
                    prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                    source_price_file=coin+'_5m.json',entry_price_verified=good,
                    source_reasoning_available=bool(plan.get('reasoning')),
                    economics_known=economics.economics(decision,contract)['status']=='known',
                    original_decision_time_known=bool(original_at),fill_delay_ms=delay,
                    eligible=bool(original_at and 0<=delay<=360000 and known_quote and good and set(target_tfs).issubset(frames)))
        rows.append(record)
        labels.append(dict(id=life['trade_id'],symbol=coin,side=side,entry_time=life['entry_time'],
            exit_time=life['exit_time'],exit_ms=epoch(life['exit_time']),net=life['lifecycle_net'],
            quantity_coin=opened.get('amount'),entry_price=entry,fee=life.get('fee_cost'),
            reason=life['final_close_reason']))
    (ROOT/'replay_inputs.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n')
    (ROOT/'replay_labels.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2))
    (ROOT/'replay_input_integrity.json').write_text(json.dumps(dict(n=len(rows),invalid_entry_bars=[x for x in checks if not x['entry_in_raw_bar']],known_economics=sum(x['economics_known'] for x in rows),
        source_reasoning=sum(x['source_reasoning_available'] for x in rows),eligible=sum(x['eligible'] for x in rows),
        limitation='Fixed recorded entries only; missing original Gemini reasoning/timing not reconstructed as fact.'),indent=2))
    print('ELIGIBLE',sum(x['eligible'] for x in rows),flush=True)
    print('PREPARED',len(rows),'BAD_ENTRY_BARS',sum(not x['entry_in_raw_bar'] for x in checks),'KNOWN_ECON',sum(x['economics_known'] for x in rows),flush=True)
    return rows
def call_one(item,client,model):
    started=time.monotonic()
    out={k:item[k] for k in ('id','at','symbol','prompt_sha256')}
    try:
        response=client.chat.completions.create(model=model,messages=[dict(role='user',content=item['prompt'])],response_format=dict(type='json_object'),**economics.request_options(model))
        text=response.choices[0].message.content or ''
        result=json.loads(text)
        if result.get('decision') not in ('approve_now','wait','reject'):raise ValueError('invalid_verdict')
        out.update(result=result,usage=response.usage.model_dump(),response_model=response.model,
                   elapsed_s=round(time.monotonic()-started,3),error=None)
    except Exception as exc:
        # Never credit a model error as a profitable rejected historical trade.
        out.update(result=dict(decision='error_keep_baseline'),error=type(exc).__name__,
                   elapsed_s=round(time.monotonic()-started,3))
    return out
def run(rows,limit):
    cfg=config.UserConfig('/opt/autotrader/users/chickenbananalab')
    client=OpenAI(api_key=cfg.OPENAI_API_KEY,timeout=25,max_retries=0)
    path=ROOT/'replay_model_results_v3.jsonl'
    previous=[json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    current={x['id']:x['prompt_sha256'] for x in rows}
    done={x['id'] for x in previous if x.get('prompt_sha256')==current.get(x['id'])}
    todo=[x for x in rows if x['id'] not in done and x['eligible']]
    if limit:todo=todo[:limit]
    print('MODEL',cfg.OPENAI_MODEL,'PENDING',len(todo),'MAX_WORKERS',4,'ORDERS_DISABLED',True,flush=True)
    errors=0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        pending={pool.submit(call_one,x,client,cfg.OPENAI_MODEL):x for x in todo}
        for future in cf.as_completed(pending):
            out=future.result()
            with path.open('a') as f:f.write(json.dumps(out,ensure_ascii=False)+'\n')
            done.add(out['id']);errors+=bool(out['error'])
            print('RESULT',len(done),out['symbol'],out['result'].get('decision'),out['error'],out['elapsed_s'],flush=True)
    client.close()
    print('FINISHED','n',len(done),'errors',errors,flush=True)
if __name__=='__main__':
    rows=prepare()
    if '--run' in sys.argv:run(rows,int(sys.argv[sys.argv.index('--limit')+1]) if '--limit' in sys.argv else None)
