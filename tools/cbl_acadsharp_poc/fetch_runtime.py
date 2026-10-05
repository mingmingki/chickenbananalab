#!/usr/bin/env python3
"""Install the ACadSharp runtimes listed in runtime/RUNTIME.json.

The self-contained runtimes (~70 MB each) are GitHub release assets, not git
files: every rebuild committed to git added ~150 MB to the repository.  A file
whose SHA-256 already matches is left alone; a download that does not match is
discarded and the installed file is kept.

usage: fetch_runtime.py [current|all|linux-x64|macos-arm64 ...]   (default: current)
"""
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = Path(os.environ.get("CBL_RUNTIME_MANIFEST") or HERE / "runtime" / "RUNTIME.json")
RUNTIME_DIR = Path(os.environ.get("CBL_RUNTIME_DIR") or HERE / "runtime")
BASE_URL = os.environ.get("CBL_RUNTIME_BASE_URL") or "https://github.com/mingmingki/chickenbananalab/releases/download"


def current_platform():
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "linux-x64"
    raise SystemExit(f"no ACadSharp runtime for {system}/{machine}")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_asset(url):
    # GitHub's CDN varies on Accept; with urllib's default headers a 404 seen
    # just before a release was published stayed cached for a while.
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream",
                                                   "User-Agent": "chickenbananalab-fetch-runtime"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=600)
        except urllib.error.HTTPError as error:
            if attempt == 2 or error.code not in (404, 500, 502, 503, 504):
                raise
            time.sleep(5 * (attempt + 1))


def install(name, entry, release):
    target = RUNTIME_DIR / name / "CblAcadSharpPoc.bin"
    if target.is_file() and sha256(target) == entry["sha256"]:
        print(f"{name}: up to date ({entry['sha256'][:12]})")
        return
    url = f"{BASE_URL}/{release}/{entry['asset']}"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as out, open_asset(url) as response:
            shutil.copyfileobj(response, out)
        got = sha256(temp)
        if got != entry["sha256"]:
            raise SystemExit(f"{name}: sha256 {got} of {url} does not match the manifest {entry['sha256']}")
        os.chmod(temp, 0o755)
        os.replace(temp, target)
        print(f"{name}: installed {entry['asset']} ({got[:12]})")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(args):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    platforms = manifest["platforms"]
    wanted = args or ["current"]
    if "all" in wanted:
        wanted = list(platforms)
    wanted = [current_platform() if name == "current" else name for name in wanted]
    for name in wanted:
        if name not in platforms:
            raise SystemExit(f"unknown platform {name}; expected one of {', '.join(platforms)}")
        install(name, platforms[name], manifest["release"])


if __name__ == "__main__":
    main(sys.argv[1:])
