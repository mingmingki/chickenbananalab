import datetime as dt
import json
import os
from pathlib import Path
def record(user_dir, **fields):
    required = ('symbol','side','lifecycle_id','algo_id','entry_price','previous_sl',
                'new_sl','tp','contracts','reason','mfe_r','fee_pct')
    if any(fields.get(k) is None for k in required):
        raise ValueError('incomplete_provenance')
    row = dict(origin='core_profit_floor_stop',time_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        **{k:fields[k] for k in required})
    raw = (json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode()
    fd = os.open(str(Path(user_dir)/'core_profit_floor_provenance_v33_111.jsonl'),
                 os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    try:
        if os.write(fd,raw) != len(raw):
            raise OSError('short_provenance_write')
        os.fsync(fd)
    finally:
        os.close(fd)
    return row
