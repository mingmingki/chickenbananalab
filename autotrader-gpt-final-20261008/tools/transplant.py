from pathlib import Path
import re,json,shutil,sys
root=Path('/Users/bagmingi/chickenbanana-work/chickenbananalab/autotrader-gpt-recovery-20261008')
dest=Path('/private/tmp/autotrader-gpt-recovery-final-20261008/candidate')
text=(root/'source.diff').read_text()
records=[]
for section in re.split(r'(?m)^--- a/',text)[1:]:
 name=section.splitlines()[0]
 if name.startswith(('tests/','templates/')) or name in ('web_app.py',): continue
 p=dest/name
 if not p.exists():
  p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/'patch'/name,p);records.append({'file':name,'new':True});continue
 original=p.read_text();updated=original
 for h in re.split(r'(?m)^@@ .* @@[^\n]*\n',section)[1:]:
  lines=h.splitlines(keepends=True)
  before=''.join(s[1:] for s in lines if s.startswith((' ','-')))
  after=''.join(s[1:] for s in lines if s.startswith((' ','+')))
  if updated.count(before)!=1: raise ValueError(f'{name}: nonunique or changed hunk: {before[:90]!r}')
  updated=updated.replace(before,after,1)
 p.write_text(updated);records.append({'file':name,'hunks':len(re.split(r'(?m)^@@ .* @@[^\n]*\n',section))-1})
sys.path.insert(0,str(root/'tools'))
from ui_integration import api_source,dashboard_source
for n,f in [('web_app.py',api_source),('templates/dashboard.html',dashboard_source)]:
 p=dest/n;p.write_text(f(p.read_text()));records.append({'file':n,'surgical_ui':True})
(dest.parent/'evidence/transplant.json').write_text(json.dumps(records,indent=2))
print(json.dumps(records))
