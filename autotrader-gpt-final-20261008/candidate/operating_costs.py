"""Read existing usage logs for operator estimates; no trading or provider calls."""
from __future__ import annotations
import datetime as dt
import json
import math
from pathlib import Path
import jsonl_cache

KST=dt.timezone(dt.timedelta(hours=9))
DEFAULT_ASSUMPTIONS=Path(__file__).with_name('operating_cost_assumptions.json')


def _number(value):
    try:
        number=float(value)
    except (TypeError,ValueError,OverflowError):
        return None
    return number if math.isfinite(number) and number>=0 else None


def _parse_time(value):
    try:
        stamp=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp).astimezone(KST)
    except (ValueError,TypeError,OverflowError):
        return None


def _server_summary(assumptions):
    if assumptions is None:
        try:
            assumptions=json.loads(DEFAULT_ASSUMPTIONS.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            assumptions={}
    fields=('hours_per_month','compute_hourly_usd','ip_hourly_usd','external_ipv4_count','disk_gb','disk_gb_month_usd')
    if not isinstance(assumptions,dict):
        return {'estimate_available':False}
    values={k:_number(assumptions.get(k)) for k in fields}
    if any(v is None for v in values.values()) or not values['hours_per_month']:
        return {'estimate_available':False}
    compute=values['compute_hourly_usd']*values['hours_per_month']
    ip=values['ip_hourly_usd']*values['hours_per_month']*values['external_ipv4_count']
    disk=values['disk_gb']*values['disk_gb_month_usd']
    if not all(math.isfinite(v) for v in (compute,ip,disk,compute+ip+disk)):
        return {'estimate_available':False}
    return dict(assumptions,estimate_available=True,estimate_only=True,
                compute_estimate_usd=compute,ip_estimate_usd=ip,disk_estimate_usd=disk,
                monthly_estimate_usd=compute+ip+disk)


def build_operating_cost_summary(user_dir,*,now=None,assumptions=None):
    now=now or dt.datetime.now(KST)
    now=(now.replace(tzinfo=KST) if now.tzinfo is None else now).astimezone(KST)
    day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    month=day.replace(day=1)
    start24=now-dt.timedelta(hours=24)
    path=Path(user_dir)/'token_usage.jsonl'
    totals={'today_cost_usd':0.,'rolling_24h_cost_usd':0.,'month_to_date_cost_usd':0.}
    providers={};purposes={};models={};invalid=0
    metadata={'logged_calls':0,'duplicate_request_records':0,'repeated_input_calls':0,
              'measured_retry_count':0,'unknown_retry_calls':0,'request_identity_unknown_calls':0}
    seen_requests=set();seen_inputs=set()
    available=path.is_file()
    try:
        rows=jsonl_cache.load_jsonl_cached(str(path))
    except OSError:
        rows=[];available=False
    for row in rows:
        if not isinstance(row,dict):
            invalid+=1;continue
        stamp=_parse_time(row.get('time'));cost=_number(row.get('cost_usd'))
        if stamp is None or cost is None:
            invalid+=1;continue
        if stamp>now:
            continue
        proposed=[totals[name]+cost for name,start in (('today_cost_usd',day),('rolling_24h_cost_usd',start24),('month_to_date_cost_usd',month)) if stamp>=start]
        if not all(math.isfinite(v) and math.isfinite(v*730/24) for v in proposed):
            invalid+=1;continue
        for name,start in (('today_cost_usd',day),('rolling_24h_cost_usd',start24),('month_to_date_cost_usd',month)):
            if stamp>=start:totals[name]+=cost
        if stamp>=start24:
            for dest,key in ((providers,'provider'),(purposes,'purpose'),(models,'model')):
                bucket=dest.setdefault(str(row.get(key) or 'unknown'),
                    {'calls':0,'cost_usd':0.,'input_tokens':0,'output_tokens':0,'total_tokens':0})
                bucket['calls']+=1;bucket['cost_usd']+=cost
                for token_key in ('input_tokens','output_tokens','total_tokens'):
                    tokens=_number(row.get(token_key))
                    if tokens is not None and math.isfinite(bucket[token_key]+tokens):
                        bucket[token_key]+=tokens
            metadata['logged_calls']+=1
            retries=row.get('retry_count')
            if type(retries) is int and retries>=0:
                metadata['measured_retry_count']+=retries
            else:
                metadata['unknown_retry_calls']+=1
            # Equal provider IDs prove repeated log records; equal input hashes only show
            # repeated inputs, which may be intentional. Neither is a measured saving.
            identity=None
            for identity_key in ('request_id','response_id'):
                value=row.get(identity_key)
                if isinstance(value,str) and value:
                    identity=(str(row.get('provider') or 'unknown'),identity_key,value)
                    break
            duplicate=identity is not None and identity in seen_requests
            if identity is None:
                metadata['request_identity_unknown_calls']+=1
            elif duplicate:
                metadata['duplicate_request_records']+=1
            else:
                seen_requests.add(identity)
            fingerprint=row.get('input_fingerprint')
            if isinstance(fingerprint,str) and fingerprint and not duplicate:
                input_key=tuple(str(row.get(k) or 'unknown') for k in ('provider','model','purpose','symbol'))+(fingerprint,)
                if input_key in seen_inputs:
                    metadata['repeated_input_calls']+=1
                seen_inputs.add(input_key)
    totals={k:round(v,8) for k,v in totals.items()}
    ai_month=totals['rolling_24h_cost_usd']*730/24 if available else None
    if not available:totals={k:None for k in totals}
    server=_server_summary(assumptions)
    return dict(totals,generated_at=now.isoformat(timespec='seconds'),timezone='Asia/Seoul',estimate_only=True,
        usage_log_available=available,invalid_usage_rows=invalid,by_provider_24h=providers,by_purpose_24h=purposes,
        by_model_24h=models,call_metadata_24h=metadata,
        metadata_basis='Logged successful token responses only; missing retries/identity are unknown; repeated inputs may be intentional; costs retain logged rows',
        pricing_basis='Recorded provider-rate estimates, not invoice amounts or verified per-model rates',
        projected_monthly_ai_cost_usd=ai_month,projection_basis='최근 24시간 × 730/24; 기록된 토큰비용 추정치',
        server=server,projected_monthly_total_usd=ai_month+server['monthly_estimate_usd'] if server.get('estimate_available') and available else None)
