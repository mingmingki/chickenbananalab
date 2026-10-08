from pathlib import Path
import json,hashlib,shutil,os
repo=Path('/Users/bagmingi/chickenbanana-work/chickenbananalab')
root=Path('/private/tmp/autotrader-gpt-recovery-final-20261008');candidate=root/'candidate';dest=repo/'autotrader-gpt-final-20261008'
base=json.loads((repo/'autotrader-live-recovery-20261008/production-source-manifest.json').read_text())['files']
allowed=set(base)
for folder in ('tests','gemini_checks','negative_guard_checks','rollback_checks','rollback_full_checks','static'):
 allowed.update(str(p.relative_to(candidate)) for p in (candidate/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.html','.css'))
allowed.update(('core_entry_events.py','core_entry_orders.py','core_entry_policy.py'))
excluded=lambda name: any(x in name for x in ('__pycache__','.env','.pem','secret.key','vapid','candidate_c_preregistration_v3.json'))
manifest={};changed={}
for name in sorted(allowed):
 p=candidate/name
 if excluded(name) or not p.is_file():continue
 target=dest/'candidate'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
 h=hashlib.sha256(p.read_bytes()).hexdigest();manifest[name]=h
 if base.get(name)!=h:changed[name]={'baseline':base.get(name),'candidate':h}
for folder in ('evidence','history'):
 for p in (root/folder).rglob('*'):
  if p.is_file() and not excluded(p.name):
   t=dest/folder/p.relative_to(root/folder);t.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,t)
for p in root.glob('*.py'):
 (dest/'tools').mkdir(exist_ok=True);shutil.copy2(p,dest/'tools'/p.name)
shutil.copy2(root/'PLAN.md',dest/'PLAN.md')
(dest/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(dest/'changed-files.json').write_text(json.dumps(changed,indent=2)+'\n')
print(json.dumps({'preserved_source_files':len(manifest),'changed_files':len(changed),'changed':list(changed)},indent=2))
