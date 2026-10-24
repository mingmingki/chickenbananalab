"""v33-14 GET-only bounded, owned-ORDER-ID CORE AI close fill reconciliation.

Never sends, cancels or amends an order. Never changes trades_log.jsonl.
A terminal order and exact filled quantity plus owned IDs must all verify.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
from decimal import Decimal,InvalidOperation
import hashlib,json,os,tempfile,time,math

KST=timezone(timedelta(hours=9))
CUTOVER="v33_14_ai_close_receipt_cutover.json"
SIDECAR="v33_14_ai_close_recovered_fills.json"
MAX_ATTEMPTS=12
MAX_AGE_SECONDS=7*86400

def _hash(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _finite(v,positive=False):
    if v is None or isinstance(v,bool):return None
    try:
        n=float(v)
        return n if math.isfinite(n) and (not positive or n>0) else None
    except (ValueError,TypeError,OverflowError):return None

def _rows(user_dir):
    p=Path(user_dir)/"trades_log.jsonl"
    raw=p.read_text(encoding="utf-8")
    if raw and not raw.endswith("\n"):
        raw=raw.rsplit("\n",1)[0]+"\n" if "\n" in raw else ""
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row,dict) for row in rows):raise ValueError("journal_non_mapping")
    return rows

def _eligible(row):
    if (not isinstance(row,dict) or row.get('strategy_group')!='core'
        or row.get('type')!='close' or row.get('reason')!='position_ai_close_all'
        or row.get('dry_run') is not False
        or row.get('close_execution_evidence')):
        return False
    ref=row.get('close_order_reference')
    if not isinstance(ref,dict) or ref.get('basis')!='owned_market_reduce_only_order_receipt':
        return False
    if not isinstance(ref.get('order_id'),str) or not ref.get('order_id').strip():
        return False
    started=ref.get('start_ms')
    contracts=_finite(ref.get('expected_contracts'),positive=True)
    actual=_finite(row.get('amount'),positive=True)
    if type(started) is not int or started<1600000000000 or contracts is None or actual is None:
        return False
    if abs(contracts-actual)>1e-8 or ref.get('side')!=row.get('side') or row.get('side') not in ('long','short'):
        return False
    try:
        stamp=datetime.fromisoformat(row['time'])
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=KST)
        diff=stamp.timestamp()*1000-started
        if not (-10000<=diff<=600000):return False
    except (ValueError,TypeError,KeyError,OverflowError):
        return False
    return True

def _load_cutover(user_dir,total):
    try:
        d=json.loads((Path(user_dir)/CUTOVER).read_text())
        n=d.get('baseline_line_count')
        if type(n) is not int or n<0 or n>total:return None
        return n
    except (OSError,ValueError,TypeError):return None

def _load_state(user_dir):
    p=Path(user_dir)/SIDECAR
    if not p.exists():return {'schema_version':1,'entries':{}}
    d=json.loads(p.read_text())
    if d.get('schema_version')!=1 or not isinstance(d.get('entries'),dict):
        raise ValueError('sidecar_schema')
    return d

def _save(user_dir,data):
    home=Path(user_dir)
    fd,name=tempfile.mkstemp(dir=str(home),prefix='.v33_14_ai_close.')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,allow_nan=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(name,home/SIDECAR)
    finally:
        if os.path.exists(name):os.unlink(name)

def _verify(client,row):
    import core_close_fill_evidence
    ref=row['close_order_reference']
    sym=row['symbol']
    position={'side':ref['side'],'contracts':ref['expected_contracts']}
    receipt={'id':ref['order_id'],'symbol':sym}
    evidence=core_close_fill_evidence.verified_close_fills(
        client,position,receipt,start_ms=ref['start_ms'])
    if not isinstance(evidence,dict):return None
    if (evidence.get('order_id')!=ref['order_id']
        or evidence.get('price_basis')!='verified_order_fills'
        or abs(float(evidence['filled_contracts'])-ref['expected_contracts'])>1e-8
        or _finite(evidence.get('vwap'),positive=True) is None
        or not evidence.get('trade_ids')):
        return None
    return evidence

def reconcile(user_dir,*,now=None,client_factory=None,max_queries=2):
    now=time.time() if now is None else float(now)
    try:
        rows=_rows(user_dir)
        cutoff=_load_cutover(user_dir,len(rows))
        if cutoff is None:return {'status':'UNKNOWN','reason':'cutover_missing_or_invalid'}
        state=_load_state(user_dir)
        if client_factory is None:
            import config,okx_client
            cfg=config.UserConfig(str(user_dir))
            client_factory=lambda symbol:okx_client.OkxClient(symbol,cfg)
        clients={};attempted=0;changed=False
        for i in range(cutoff,len(rows)):
            row=rows[i]
            if not _eligible(row):continue
            key=str(i)
            checksum=_hash(row)
            entry=state['entries'].get(key)
            if entry and (entry.get('sha256')!=checksum or entry.get('order_id')!=row['close_order_reference']['order_id']):
                return {'status':'UNKNOWN','reason':'journal_sidecar_conflict','index':i}
            if entry and entry.get('status') in ('VERIFIED','UNCONFIRMED'):
                continue
            if entry and (entry.get('next_at') or 0)>now:continue
            if attempted>=max_queries:break
            new=entry or dict(sha256=checksum,
                order_id=row['close_order_reference']['order_id'],attempts=0)
            when=int(row['close_order_reference']['start_ms'])/1000
            if now-when>MAX_AGE_SECONDS:
                new.update(status='UNCONFIRMED',last_error='expired',updated_at=now)
                state['entries'][key]=new;changed=True
                continue
            if new['attempts']>=MAX_ATTEMPTS:
                new.update(status='UNCONFIRMED',last_error='retry_limit',updated_at=now)
                state['entries'][key]=new;changed=True
                continue
            attempted+=1
            new['attempts']+=1
            try:
                sym=row['symbol']
                client=clients.setdefault(sym,client_factory(sym))
                proof=_verify(client,row)
                if proof is None:
                    new.update(status='RETRY',last_error='exchange_proof_incomplete',
                        next_at=now+min(3600,300*(2**min(new['attempts']-1,3))))
                else:
                    new.update(status='VERIFIED',proof=proof,updated_at=now,
                        next_at=None,last_error=None)
            except Exception as exc:
                new.update(status='RETRY',last_error=type(exc).__name__,
                    next_at=now+min(3600,300*(2**min(new['attempts']-1,3))))
            new['updated_at']=now
            state['entries'][key]=new;changed=True
        if changed:_save(user_dir,state)
        return dict(status='KNOWN',attempted=attempted,eligible=sum(
            _eligible(r) for r in rows[cutoff:]),
            states={k:sum(v.get('status')==k for v in state['entries'].values())
                    for k in ('VERIFIED','RETRY','UNCONFIRMED')})
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)

def summary(user_dir,*,now=None):
    try:
        rows=_rows(user_dir)
        cutoff=_load_cutover(user_dir,len(rows))
        if cutoff is None:return dict(status='UNKNOWN',reason='cutover_missing_or_invalid')
        state=_load_state(user_dir)
        exact=0;unconfirmed=0;retry=0;untracked=0;verified_samples=[]
        for i,row in enumerate(rows[cutoff:],start=cutoff):
            if row.get('reason')!='position_ai_close_all' or row.get('type')!='close':
                continue
            if row.get('strategy_group')!='core' or row.get('dry_run') is not False:continue
            if row.get('close_execution_evidence'):exact+=1;continue
            if not _eligible(row):untracked+=1;continue
            entry=state['entries'].get(str(i))
            if entry and entry.get('sha256')!=_hash(row):
                return dict(status='UNKNOWN',reason='sidecar_row_changed')
            status=(entry or {}).get('status')
            if status=='VERIFIED':
                p=entry['proof'];ref=row['close_order_reference']
                if p.get('order_id')!=ref['order_id'] or p.get('price_basis')!='verified_order_fills':
                    return dict(status='UNKNOWN',reason='invalid_sidecar_order_id')
                exact+=1
                verified_samples.append(dict(time=row.get('time'),symbol=row.get('symbol'),
                    order_id=ref['order_id'],vwap=p.get('vwap')))
            elif status=='UNCONFIRMED':unconfirmed+=1
            else:retry+=1
        return dict(status='KNOWN',post_cutover_exact_fill_proofs=exact,
            post_cutover_owned_receipt_pending=retry,
            post_cutover_unconfirmed=unconfirmed,
            post_cutover_missing_order_receipts=untracked,
            recovered_fill_examples=verified_samples[-3:],
            principle='Exchange-owned order-id-only fill verification; trades_log and original PnL unchanged')
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
