"""Read-only guidance for retired scheduled reviews and saved advisory history."""
import re
from pathlib import Path
import jsonl_cache


def build_analysis_ops_status(reviews,*,now=None,manual_review_available=False):
    scheduled=[r for r in (reviews or []) if isinstance(r,dict)
               and re.fullmatch(r'\d{8}-(00|06|12|18)',str(r.get('review_window_id') or ''))]
    last=max(scheduled,key=lambda r:r['review_window_id'],default={})
    no_sample=last.get('status')=='no_new_sample'
    return {
        'local_analysis':{'paid_ai':False,'live_trading_effect':False},
        'manual_review':{'available':manual_review_available is True,
            'paid_ai':manual_review_available is True,'live_trading_effect':False},
        'scheduler':{'automatic':False,'status':'retired','interval_hours':6,'timezone':'Asia/Seoul',
            'last_window_id':last.get('review_window_id'),'last_status':last.get('status'),
            'last_paid_ai':False if no_sample else None,'last_cost_usd':0. if no_sample else None,
            'next_window_start':None,
            'no_new_sample_skips_paid_ai':True}}


def read_analysis_ops_status(user_dir,*,now=None,manual_review_available=False):
    try:
        reviews=jsonl_cache.load_jsonl_cached(str(Path(user_dir)/"ai_review_reports.jsonl"))
    except OSError:
        reviews=[]
    return build_analysis_ops_status(reviews,now=now,manual_review_available=manual_review_available)
