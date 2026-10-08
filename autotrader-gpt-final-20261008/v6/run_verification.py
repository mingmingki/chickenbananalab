from pathlib import Path
import os, subprocess, json, sys
root = Path(__file__).resolve().parents[1]
candidate = root / 'candidate'
evidence = Path(__file__).resolve().parent / 'evidence'
python = '/Users/bagmingi/chickenbanana-work/chickenbananalab/.venv-autotrader/bin/python'
isolation = root.parent / 'autotrader-gpt-recovery-20261008/tools'
assert (isolation / 'sitecustomize.py').is_file()
env = dict(os.environ, PATH='/opt/homebrew/bin:' + os.environ['PATH'], PYTHONPATH=str(isolation)+':'+str(candidate), AUTOTRADER_PROJECT_DIR=str(candidate))
phase = sys.argv[1] if len(sys.argv)>1 else 'green'
results=[]
for suite in ('tests','gemini_checks','negative_guard_checks','rollback_checks','rollback_full_checks'):
    log=evidence/f'{phase}-{suite}.log'; xml=evidence/f'{phase}-{suite}.xml'
    with log.open('w') as out:
        p=subprocess.run([python,'-m','pytest',suite,'-q','--junitxml='+str(xml)],cwd=candidate,env=env,stdout=out,stderr=subprocess.STDOUT)
    results.append(dict(suite=suite,exit_code=p.returncode,log=log.name,xml=xml.name))
    print(suite,p.returncode,log.read_text().splitlines()[-1],flush=True)
(evidence/f'{phase}-suite-results.json').write_text(json.dumps(results,indent=2))
sys.exit(any(r['exit_code'] for r in results))
