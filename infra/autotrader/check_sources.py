"""Check the Git source inventory without printing file contents."""
import ast
from pathlib import Path
import subprocess
from release import allowed, scan

repo = Path(__file__).resolve().parents[2]
root = repo/'autotrader'
manifest = set(Path(__file__).with_name('source-files.txt').read_text().splitlines())
tracked = subprocess.check_output(['git','ls-files','-z','--','autotrader'],cwd=repo).decode().split('\0')
if not any(tracked):
    raise SystemExit('No tracked autotrader sources; stage the selected source inventory first')
count = 0
for name in filter(None,tracked):
    path = repo/name
    rel = path.relative_to(root).as_posix()
    parts = Path(rel).parts
    auxiliary = (len(parts)==2 and parts[0] in {'tests','gemini_checks','negative_guard_checks','rollback_checks','rollback_full_checks','scripts'} and path.suffix=='.py')
    if not (allowed(rel) or auxiliary):
        raise SystemExit(f'Unapproved tracked file: {name}')
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
        raise SystemExit(f'Symlink rejected: {name}')
    data=path.read_bytes()
    scan(name,data)
    if path.suffix=='.py':
        ast.parse(data,filename=name)
    if allowed(rel) and rel not in manifest:
        raise SystemExit(f'Source missing from release manifest: {name}')
    count += 1
missing=manifest-{str(Path(p).relative_to('autotrader')) for p in tracked if p}
if missing:
    raise SystemExit('Manifest contains untracked files: '+', '.join(sorted(missing)))
print(f'Validated {count} tracked source/test files; {len(manifest)} release files')
