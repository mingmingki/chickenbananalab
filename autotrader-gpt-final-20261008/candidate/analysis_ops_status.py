"""Read-only guidance derived from saved advisory reviews and existing schedule."""
import re
from pathlib import Path
import jsonl_cache
from trade_learning_scheduler import window_bounds


def build_analysis_ops_status(reviews,*,now=None):
    _,end,_=window_bounds(now)
    scheduled=[r for r in (reviews or []) if isinstance(r,dict)
               and re.fullmatch(r'\d{8}-(00|06|12|18)',str(r.get('review_window_id') or ''))]
    last=max(scheduled,key=lambda r:r['review_window_id'],default={})
    no_sample=last.get('status')=='no_new_sample'
    return {
        'local_analysis':{'paid_ai':False,'live_trading_effect':False},
        'manual_review':{'paid_ai':True,'live_trading_effect':False},
        'scheduler':{'automatic':True,'interval_hours':6,'timezone':'Asia/Seoul',
            'last_window_id':last.get('review_window_id'),'last_status':last.get('status'),
            'last_paid_ai':False if no_sample else None,'last_cost_usd':0. if no_sample else None,
            'next_window_start':end.isoformat(timespec='seconds'),
            'no_new_sample_skips_paid_ai':True}}


def read_analysis_ops_status(user_dir,*,now=None):
    try:
        reviews=jsonl_cache.load_jsonl_cached(str(Path(user_dir)/"ai_review_reports.jsonl"))
    except OSError:
        reviews=[]
    return build_analysis_ops_status(reviews,now=now)
