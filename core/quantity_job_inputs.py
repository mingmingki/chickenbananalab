"""Uploads and finished AI steps of a running quantity job, kept so an interrupted job can resume.

A main analysis runs in a background thread of the web worker, and its uploads otherwise live
only in that worker's memory, so a restart (a deploy, a crash) loses them. Each job gets a
private folder named by a hash of its job id:

  job.json            owner, sizes and SHA-256 of each upload, the settings the job started with
  <input>.bin         the merged uploads
  steps/<name>.json   AI results already paid for (one per architectural step or structural
                      batch), each tagged with a key of everything that produced it

The folder is removed when the job stores its result. Folders of jobs that were never resumed
expire after RETENTION_SECONDS. Nothing resumes on its own: a resume is always started by the
job's owner, and a saved step is reused only when its key matches exactly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

INPUT_NAMES = ("structural_zip", "structural_pdf", "architectural_zip", "architectural_pdf")
RETENTION_SECONDS = 3 * 24 * 60 * 60
MIN_FREE_BYTES = 2 * 1024 ** 3   # never fill the server disk to keep a resume possible
MAX_TOTAL_BYTES = 3 * 1024 ** 3  # all kept job folders together
_META = "job.json"
_STEPS = "steps"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write_private(path, data):
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _folder_bytes(folder):
    return sum(entry.stat().st_size for entry in folder.rglob("*") if entry.is_file())


class StepStore:
    """Finished AI steps of one job; a step is returned only for the same key."""

    def __init__(self, folder, base):
        self.folder = Path(folder) / _STEPS
        self.base = base
        self.reused = []  # names of the steps this run took from the folder

    def _key(self, parts):
        encoded = json.dumps([self.base, list(parts)], ensure_ascii=False, sort_keys=True, default=str)
        return _digest(encoded.encode("utf-8"))

    def _read(self, name, parts):
        try:
            saved = json.loads((self.folder / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return saved if saved.get("key") == self._key(parts) else None

    def has(self, name, *parts):
        return self._read(name, parts) is not None

    def load(self, name, *parts):
        saved = self._read(name, parts)
        if saved is None:
            return None
        self.reused.append(name)
        return saved.get("value")

    def save(self, name, value, *parts):
        try:
            encoded = json.dumps({"key": self._key(parts), "saved_at": time.time(), "value": value},
                                 ensure_ascii=False, allow_nan=False)
            self.folder.mkdir(mode=0o700, exist_ok=True)
            _write_private(self.folder / f"{name}.json", encoded.encode("utf-8"))
            return True
        except (OSError, TypeError, ValueError) as exc:  # the job goes on without this step saved
            logger.warning("quantity_job_step_not_saved name=%s error=%s", name, str(exc)[:200])
            return False


class JobInputStore:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, job_id):
        return self.root / _digest(str(job_id).encode("utf-8"))[:40]

    def _meta(self, job_id, owner):
        try:
            meta = json.loads((self.folder(job_id) / _META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if meta.get("job_id") != str(job_id) or meta.get("owner") != str(owner):
            return None
        if time.time() - float(meta.get("created_at") or 0) > RETENTION_SECONDS:
            return None
        return meta

    def purge(self, now=None):
        """Remove expired folders. Returns the bytes the remaining folders use."""
        now = time.time() if now is None else now
        if not self.root.is_dir():
            return 0
        total = 0
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            try:
                created = json.loads((folder / _META).read_text(encoding="utf-8")).get("created_at") or 0
            except (OSError, ValueError):
                created = folder.stat().st_mtime  # a save that never finished
            if now - float(created) > RETENTION_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                total += _folder_bytes(folder)
        return total

    def save(self, job_id, owner, inputs, settings):
        """Keep the uploads of a new job and return its record. Returns None (the job runs, but
        cannot resume) when the disk is short or saving fails."""
        payload = {name: inputs.get(name) for name in INPUT_NAMES if inputs.get(name)}
        size = sum(len(data) for data in payload.values())
        folder = self.folder(job_id)
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            kept = self.purge()
            if kept + size > MAX_TOTAL_BYTES or shutil.disk_usage(self.root).free - size < MIN_FREE_BYTES:
                logger.warning("quantity_job_inputs_not_kept reason=space size=%s kept=%s", size, kept)
                return None
            folder.mkdir(mode=0o700, exist_ok=True)
            files = {}
            for name, data in payload.items():
                _write_private(folder / f"{name}.bin", data)
                files[name] = {"size": len(data), "sha256": _digest(data)}
            meta = {"job_id": str(job_id), "owner": str(owner), "created_at": time.time(),
                    "files": files, "settings": settings}
            _write_private(folder / _META, json.dumps(meta, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            return meta
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("quantity_job_inputs_not_kept reason=error error=%s", str(exc)[:200])
            shutil.rmtree(folder, ignore_errors=True)
            return None

    def load(self, job_id, owner):
        """(inputs, settings, meta) of a kept job, or None when missing, expired, another
        owner's or damaged."""
        meta = self._meta(job_id, owner)
        if meta is None:
            return None
        inputs = dict.fromkeys(INPUT_NAMES)
        for name, info in (meta.get("files") or {}).items():
            if name not in INPUT_NAMES:
                return None
            try:
                data = (self.folder(job_id) / f"{name}.bin").read_bytes()
            except OSError:
                return None
            if len(data) != info.get("size") or _digest(data) != info.get("sha256"):
                logger.warning("quantity_job_inputs_damaged name=%s", name)
                return None
            inputs[name] = data
        return inputs, meta.get("settings") or {}, meta

    def resume_info(self, job_id, owner):
        """What a resume would reuse, without reading the uploads; None when it cannot resume."""
        meta = self._meta(job_id, owner)
        if meta is None:
            return None
        steps = self.folder(job_id) / _STEPS
        names = sorted(entry.stem for entry in steps.glob("*.json")) if steps.is_dir() else []
        return {"saved_steps": len(names),
                "structural_batches": sum(1 for name in names if name.startswith("structural-")),
                "expires_at": float(meta["created_at"]) + RETENTION_SECONDS}

    def steps(self, job_id, base):
        return StepStore(self.folder(job_id), base)

    def discard(self, job_id):
        shutil.rmtree(self.folder(job_id), ignore_errors=True)
