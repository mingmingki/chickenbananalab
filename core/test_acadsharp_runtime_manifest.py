import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

TOOLS = Path(settings.BASE_DIR) / "tools" / "cbl_acadsharp_poc"
MANIFEST = TOOLS / "runtime" / "RUNTIME.json"
FETCH = TOOLS / "fetch_runtime.py"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AcadSharpRuntimeManifestTests(SimpleTestCase):
    """The ~70 MB runtimes are GitHub release assets listed in RUNTIME.json, not git files.

    Each rebuild committed to git added ~150 MB to the repository (1.2 GB by
    2026-10-05).  The manifest pins the release and the SHA-256 of each file.
    """

    def manifest(self):
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_names_a_release_and_a_checksum_per_platform(self):
        manifest = self.manifest()
        self.assertTrue(manifest["release"])
        self.assertEqual(set(manifest["platforms"]), {"linux-x64", "macos-arm64"})
        for entry in manifest["platforms"].values():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(entry["asset"].endswith(".bin"))

    def test_installed_runtimes_match_the_manifest(self):
        # A rebuilt runtime must come with a new release and manifest entry.
        for name, entry in self.manifest()["platforms"].items():
            path = TOOLS / "runtime" / name / "CblAcadSharpPoc.bin"
            if path.is_file():
                self.assertEqual(_sha256(path), entry["sha256"], name)

    @skipUnless(shutil.which("git") and (Path(settings.BASE_DIR) / ".git").exists(), "git checkout required")
    def test_runtime_binaries_are_not_tracked(self):
        tracked = subprocess.run(["git", "ls-files", "tools/cbl_acadsharp_poc/runtime"], cwd=settings.BASE_DIR,
                                 capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([path for path in tracked if path.endswith(".bin")], [])
        self.assertIn("tools/cbl_acadsharp_poc/runtime/RUNTIME.json", tracked)


class AcadSharpRuntimeFetchTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-runtime-fetch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.release = self.tmp / "releases" / "rt-1"
        self.release.mkdir(parents=True)
        self.payload = b"#!/bin/sh\necho runtime\n"
        (self.release / "rt-linux.bin").write_bytes(self.payload)
        self.runtime_dir = self.tmp / "runtime"

    def fetch(self, sha256):
        manifest = self.tmp / "RUNTIME.json"
        manifest.write_text(json.dumps({"release": "rt-1", "platforms": {
            "linux-x64": {"asset": "rt-linux.bin", "sha256": sha256},
            "macos-arm64": {"asset": "rt-mac.bin", "sha256": "0" * 64}}}), encoding="utf-8")
        env = dict(os.environ, CBL_RUNTIME_MANIFEST=str(manifest), CBL_RUNTIME_DIR=str(self.runtime_dir),
                   CBL_RUNTIME_BASE_URL=(self.tmp / "releases").as_uri())
        return subprocess.run([sys.executable, str(FETCH), "linux-x64"], env=env, capture_output=True, text=True, timeout=60)

    def test_downloads_and_installs_a_matching_file(self):
        run = self.fetch(hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(run.returncode, 0, run.stderr)
        target = self.runtime_dir / "linux-x64" / "CblAcadSharpPoc.bin"
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertTrue(os.access(target, os.X_OK))
        again = self.fetch(hashlib.sha256(self.payload).hexdigest())
        self.assertIn("up to date", again.stdout)

    def test_a_mismatching_download_leaves_the_installed_file_alone(self):
        target = self.runtime_dir / "linux-x64" / "CblAcadSharpPoc.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old runtime")
        run = self.fetch("f" * 64)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("sha256", run.stderr)
        self.assertEqual(target.read_bytes(), b"old runtime")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["CblAcadSharpPoc.bin"])
