"""Build a deterministic source-only archive; never import the trading app."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import tarfile

SECRET_PATTERNS = (
    re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    re.compile(rb'AKIA[0-9A-Z]{16}'),
    re.compile(rb'AIza[0-9A-Za-z_-]{35}'),
    re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}'),
)
EXCLUDED = {'deploy_stopped.py', 'deploy_gemini.py', 'setup.py'}

def allowed(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts):
        return False
    if len(p.parts) == 1:
        return (p.suffix == '.py' and p.name not in EXCLUDED) or p.name in {'requirements.txt', 'requirements-ci.txt'}
    return ((p.parts[0] == 'templates' and p.suffix == '.html') or
            (p.parts[0] == 'static' and (p.suffix in {'.js', '.png', '.css'} or name == 'static/manifest.json')))

def scan(name: str, data: bytes) -> None:
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise ValueError(f'Credential pattern detected in {name}; value withheld')

def build(root: Path, manifest: Path, output: Path) -> str:
    root = root.resolve()
    names = manifest.read_text().splitlines()
    if not names or len(set(names)) != len(names):
        raise ValueError('Empty or duplicate manifest')
    files = []
    for name in sorted(names):
        if not allowed(name):
            raise ValueError(f'Not an approved source path: {name}')
        p = root / name
        if any(part.is_symlink() for part in [p, *p.parents] if part != root and root in part.parents):
            raise ValueError(f'Symlink rejected: {name}')
        if not p.is_file() or not p.resolve().is_relative_to(root):
            raise ValueError(f'Missing or external source: {name}')
        data = p.read_bytes()
        scan(name, data)
        files.append((name, data))
    # Validate all files before creating output. No filesystem metadata or keys.
    with output.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, data in files:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(output.read_bytes()).hexdigest()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    digest = build(repo/'autotrader', Path(__file__).with_name('source-files.txt'), args.output)
    args.output.with_suffix(args.output.suffix+'.sha256').write_text(f'{digest}  {args.output.name}\n')
    print(f'Source-only archive SHA256: {digest}')
