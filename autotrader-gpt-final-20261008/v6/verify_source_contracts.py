"""Local, read-only source and event-boundary qualification. No provider/exchange calls."""
import ast
import hashlib
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
candidate=root/'candidate'
sys.path.insert(0,str(candidate))
import candidate_c_runtime as runtime
patch=root/'v6/deployment-patch'
manifest=json.loads((patch/'manifest.json').read_text())
for name,pins in manifest.items():
    assert name not in ('flask_secret.key','vapid_private_key.pem','accounts.json','.env')
    assert not Path(name).is_absolute() and '..' not in Path(name).parts
    assert hashlib.sha256((candidate/name).read_bytes()).hexdigest()==pins['candidate'],name
    assert hashlib.sha256((patch/'source'/name).read_bytes()).hexdigest()==pins['candidate'],name
    if name.endswith('.py'):compile((candidate/name).read_bytes(),str(candidate/name),'exec')

tree=ast.parse((candidate/'trader.py').read_text())
attempts=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='_record_core_entry_attempt']
for call in attempts:
    assert {'cfg','decision','decision_id','entry_context'} <= {kw.arg for kw in call.keywords},call.lineno
parity=runtime.backtest_live_parity_evidence()
mismatches={name for name,expected in parity['verified_source_hashes'].items() if parity['current_source_hashes'].get(name)!=expected}
assert mismatches=={'candidate_c_preregistration_v3.json'},mismatches
live=json.loads((root/'v6/evidence/live-baseline-check.json').read_text())
assert live['ok'] and all(row['matches'] for row in live['files'].values())
assert live['preregistration_sha256']==parity['verified_source_hashes']['candidate_c_preregistration_v3.json']
# Contract behavior was exercised before hash re-pinning and again by final full suites.
assert '1313 passed' in (root/'v6/evidence/qualifying-behavior-final.log').read_text()
assert all(r['exit_code']==0 for r in json.loads((root/'v6/evidence/handoff-suite-results.json').read_text()))
assert '1314 passed' in (root/'v6/evidence/handoff-bare.log').read_text()
result=dict(ok=True,runtime_patch_files=len(manifest),core_attempt_calls_wired=len(attempts),
    local_source_parity_excluding_deployment_only_preregistration=True,
    deployment_only_preregistration_live_sha_matches=True,local_full_live_parity=False,
    behavior_replay='real native DOGE live/replay entry, risk and chase + per-bar freshness/dedup; fake external boundaries',
    source_hashes=parity['current_source_hashes'],verified_source_hashes=parity['verified_source_hashes'],
    live_unchanged=True,network_isolation='sitecustomize.py socket deny in every pytest process')
(root/'v6/evidence/source-event-parity-contracts.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('source_hashes','verified_source_hashes')}))
