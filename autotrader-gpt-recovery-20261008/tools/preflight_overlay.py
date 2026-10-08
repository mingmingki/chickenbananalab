"""Read-only exact source check. This unqualified bundle never deploys itself."""
import argparse
import hashlib
import json
from pathlib import Path


def check(source,manifest):
    rows=[]
    for name,wanted in manifest['required_baseline_sha256'].items():
        path=source/name
        actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append(dict(file=name,expected=wanted,actual=actual,matched=actual==wanted))
    for name,wanted in manifest['new_source_files'].items():
        path=source/name
        if path.exists():
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            rows.append(dict(file=name,expected=wanted,actual=actual,matched=actual==wanted))
    return dict(source_hashes_match=all(r['matched'] for r in rows),
                deployment_qualified=manifest['deployment_qualified'],files=rows,
                blockers=manifest['deployment_blockers'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    manifest=json.loads((Path(__file__).resolve().parents[1]/'manifest.json').read_text())
    result=check(args.source,manifest)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['source_hashes_match'] and result['deployment_qualified'] else 2)


if __name__=='__main__': main()
