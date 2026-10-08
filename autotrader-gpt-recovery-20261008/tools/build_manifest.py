"""Record source hashes/diff without turning scoped tests into deployment approval."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent/'autotrader-rc-structure-risk-ai-20261005'
V4=ROOT.parent/'autotrader-handoff-20261008/patch'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    files={str(p.relative_to(ROOT/'patch')):sha(p) for p in sorted((ROOT/'patch').rglob('*'))
        if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.html','.js')}
    existing={};new={};diff=[]
    for name in files:
        path=ROOT/'patch'/name
        baseline=V4/name if name in ('trader.py','adaptive_exit_engine.py') else BASE/name
        if baseline.is_file():
            existing[name]=sha(baseline)
            if existing[name]==files[name]: continue
            before=baseline.read_text().splitlines(keepends=True)
        else:
            new[name]=files[name];before=[]
        diff.extend(difflib.unified_diff(before,path.read_text().splitlines(keepends=True),
            fromfile='a/'+name,tofile='b/'+name))
    manifest=dict(scope='Archived v4 legacy CORE entry + Oct5 support modules; actual LIVE unverified',
        deployment_qualified=False,deployment_blockers=[
            'Fresh running release/source/account/ownership audit unavailable',
            'Complete v4/runtime dependencies and current full regression suite unavailable',
            'Oct5 comparison suite has baseline and candidate failures; not production qualification',
            'Actual Oct8 decision/lifecycle logs unavailable; ETH incident attribution unconfirmed',
            'Current account Telegram destination/send success and protection audit unavailable',
            'Existing GCP network/auth refresh and SSH access blocked'],
        required_baseline_sha256=existing,new_source_files=new,candidate_sha256=files,
        unchanged_dependency_sha256={'adaptive_exit_engine.py':sha(V4/'adaptive_exit_engine.py')})
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (ROOT/'source.diff').write_text(''.join(diff))
    print({'candidate_files':len(files),'trader_sha256':files['trader.py'],'deployment_qualified':False})


if __name__=='__main__': main()
