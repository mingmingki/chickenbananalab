"""Run ON VM from uploaded patch. Read-only; no orders, AI, Telegram, or state writes.
/opt/autotrader/.venv/bin/python verify_live_readonly.py /path/to/deployment-patch baseline|candidate
"""
import os
os.environ['AUTOTRADER_PROJECT_DIR']='/opt/autotrader'  # before config/accounts import
import sys,json,hashlib,subprocess,sqlite3
from pathlib import Path
patch=Path(sys.argv[1]);phase=sys.argv[2]
assert phase in ('baseline','candidate')
root=Path('/opt/autotrader');release=root.resolve()
manifest=json.loads((patch/'manifest.json').read_text())
if phase=='baseline':assert str(release)=='/opt/autotrader-releases/core_gpt_recovery_v5_20261008T213530KST'
else:assert release.name.startswith('entry_cost_v6_')
for name,row in manifest.items():
    assert not Path(name).is_absolute() and '..' not in Path(name).parts
    actual=hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None
    assert actual==row[phase],name
sys.path.insert(0,str(root))
import config,accounts,candidate_c_runtime,core_unified_service
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
assert accounts.is_approved('chickenbananalab')
assert cfg.EXECUTION_MODE=='LIVE' and cfg.CORE_UNIFIED_MODE=='ROLLBACK'
assert cfg.GPT_ENTRY_GATE_ENABLED and cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS and not cfg.CORE_PAID_SHADOW_ENABLED
assert all(core_unified_service.owner(cfg,s)=='legacy' for s in cfg.ENABLED_SYMBOLS)
assert not candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir)
assert candidate_c_runtime.backtest_live_parity_evidence()['verified']
assert subprocess.check_output(['systemctl','is-active','autotrader.service'],text=True).strip()=='active'
counts={};path=Path(cfg.user_dir)/'core_entry_events.sqlite3'
if path.is_file():
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        counts=dict(db.execute('SELECT delivery,COUNT(*) FROM events GROUP BY delivery').fetchall())
print(json.dumps(dict(ok=True,phase=phase,release=str(release),source_files_checked=len(manifest),
    policy_checks_passed=True,full_live_c_parity_verified=True,service_active=True,outbox_state_counts=counts)))
