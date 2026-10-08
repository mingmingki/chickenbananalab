from pathlib import Path
import os,subprocess,json,sys,time,ast,hashlib,py_compile
root=Path(__file__).parent;candidate=root/'candidate';evidence=root/'evidence'
python='/Users/bagmingi/chickenbanana-work/chickenbananalab/.venv-autotrader/bin/python'
env=dict(os.environ,PYTHONPATH='/Users/bagmingi/chickenbanana-work/chickenbananalab/autotrader-gpt-recovery-20261008/tools:'+str(candidate),AUTOTRADER_PROJECT_DIR=str(candidate))
results=[]
for suite in ('tests','gemini_checks','negative_guard_checks','rollback_checks','rollback_full_checks'):
 log=evidence/('final-'+suite+'.log');xml=evidence/('final-'+suite+'.xml')
 with log.open('w') as out:
  p=subprocess.run([python,'-m','pytest',suite,'-q','--junitxml='+str(xml)],cwd=candidate,env=env,stdout=out,stderr=subprocess.STDOUT)
 results.append(dict(suite=suite,exit_code=p.returncode,log=log.name,xml=xml.name))
 print(suite,p.returncode,log.read_text().splitlines()[-1],flush=True)
 if p.returncode:print('\n'.join(log.read_text().splitlines()[-8:]),flush=True)
(evidence/'final-suite-results.json').write_text(json.dumps(results,indent=2))
sys.exit(any(r['exit_code'] for r in results))
