"""Verified owned-close price backfill. Run dry-run before stopping service for apply."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from collections import Counter
from close_execution_price_proof import proof_for_remaining_position


def reconcile(rows):
    corrected = []
    report = []
    seen = set()
    for row in rows:
        item = dict(row)
        evidence = item.get('close_reason_evidence')
        if item.get('type') == 'close' and isinstance(evidence, dict):
            proof = proof_for_remaining_position(
                evidence, evidence.get('lifecycle_id'), item.get('amount'))
            reason = item.get('reason')
            valid_reason = (reason == evidence.get('reason')
                if item.get('strategy_group') == 'core'
                else reason == ('EXTERNAL_SL_FILLED_EXACT' if evidence.get('reason') == 'stop_loss'
                                else 'EXTERNAL_TP_FILLED_EXACT'))
            if proof and valid_reason and item.get('pnl_source') == 'okx_realized':
                keyset = {(item.get('symbol'), proof['order_id'], tid) for tid in proof['trade_ids']}
                if seen.intersection(keyset):
                    raise RuntimeError('duplicate_proven_exchange_fill_identity')
                seen.update(keyset)
                prev = item.get('close_price')
                existing = item.get('close_execution_evidence')
                if existing not in (None, proof):
                    raise RuntimeError('conflicting_existing_execution_proof')
                if prev != proof['vwap'] or existing != proof:
                    item['close_price'] = proof['vwap']
                    item['close_execution_evidence'] = proof
                    report.append(dict(time=item.get('time'), symbol=item.get('symbol'),
                        prior_price=prev, verified_price=proof['vwap'], order_id=proof['order_id'],
                        fill_count=len(proof['trade_ids'])))
        corrected.append(item)
    return corrected, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--expected-sha256')
    args = ap.parse_args()
    path = Path(args.file).resolve()
    if path.name != 'trades_log.jsonl' or not path.is_file():
        raise RuntimeError('unexpected_journal_path')
    raw = path.read_bytes()
    original_hash = hashlib.sha256(raw).hexdigest()
    if args.expected_sha256 and args.expected_sha256 != original_hash:
        raise RuntimeError('journal_changed_since_dry_run')
    lines = raw.decode('utf-8').splitlines(keepends=True)
    rows = [json.loads(x) for x in lines]
    corrected, report = reconcile(rows)
    summary = dict(mode='apply' if args.apply else 'dry-run',
        before_sha256=original_hash, total_rows=len(rows),
        changed_rows=len(report), affected=report)
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False))
        return
    backup_dir = path.parent / '_recovery_backups' / ('owned_close_price_v33_4_' +
        datetime.datetime.now().strftime('%Y%m%dT%H%M%S'))
    backup_dir.mkdir(parents=True, exist_ok=False)
    old = backup_dir/'trades_log.before.jsonl'
    with old.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(old, 0o600)
    new_lines = [json.dumps(updated, ensure_ascii=False, separators=(',',':')) + '\n'
                 if updated != original else orig_line
                 for updated,original,orig_line in zip(corrected,rows,lines)]
    new_raw = ''.join(new_lines).encode('utf-8')
    # Check again before atomic replacement; caller must hold writers stopped.
    if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError('journal_changed_during_backup')
    fd, tmp = tempfile.mkstemp(prefix='.trades_log.v33_4.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(new_raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    summary['after_sha256']=hashlib.sha256(new_raw).hexdigest()
    summary['backup_path']=str(old)
    (backup_dir/'audit.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    main()
