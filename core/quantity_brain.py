"""What the quantity tool has learned from people checking its reading of drawings.

An office draws with its own layer names. When someone confirms or corrects how the layers of a
drawing were read (wall, wall below, beam, ... or "not a member"), the answer is kept here as an
office profile, and the next drawing whose layers look like that office's gets those roles before
any guessing from geometry. Every answer is also kept as an event (who, when, which job, what
changed) so a profile can be explained or rolled back. Plain SQLite next to the job state, kept
for good (no expiry), never sent to an AI.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

ROLES = ("wall", "wall_below", "wall_above", "column", "beam", "outline", "opening", "stair")
MATCH_MIN_SHARED = 3
MATCH_MIN_JACCARD = 0.3
MAX_PROFILES_SENT = 50


class Brain:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS profile (id TEXT PRIMARY KEY, label TEXT, layers TEXT NOT NULL, "
                               "uses INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, "
                               "profile TEXT, owner TEXT, job TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL)")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def profiles(self, limit=MAX_PROFILES_SENT):
        """[{id, label, layers: {layer name: role or None}, uses}], most used first."""
        with self.connection() as c:
            rows = c.execute("SELECT id, label, layers, uses FROM profile ORDER BY uses DESC, updated DESC LIMIT ?",
                             (limit,)).fetchall()
        return [{"id": r[0], "label": r[1], "layers": json.loads(r[2]), "uses": r[3]} for r in rows]

    def profile(self, profile_id):
        with self.connection() as c:
            row = c.execute("SELECT id, label, layers, uses FROM profile WHERE id=?", (profile_id,)).fetchone()
        return {"id": row[0], "label": row[1], "layers": json.loads(row[2]), "uses": row[3]} if row else None

    def learn(self, layers, owner=None, job=None, profile_id=None, label=None, kind="confirm"):
        """Merge confirmed roles {layer name: role or None} into a profile (the given one, else the best
        match, else a new one). Returns the profile id."""
        layers = {str(name)[:200]: (role if role in ROLES else None) for name, role in (layers or {}).items()}
        if not layers:
            return None
        now = time.time()
        with self.connection() as c:
            target = None
            if profile_id:
                row = c.execute("SELECT id, layers FROM profile WHERE id=?", (profile_id,)).fetchone()
                target = (row[0], json.loads(row[1])) if row else None
            if target is None:
                rows = c.execute("SELECT id, layers FROM profile").fetchall()
                best = match([{"id": r[0], "layers": json.loads(r[1])} for r in rows], layers.keys())
                if best:
                    target = (best["id"], next(json.loads(r[1]) for r in rows if r[0] == best["id"]))
            if target is None:
                target = (uuid.uuid4().hex[:12], {})
                c.execute("INSERT INTO profile (id, label, layers, uses, created, updated) VALUES (?,?,?,?,?,?)",
                          (target[0], label or "", "{}", 0, now, now))
            merged = dict(target[1])
            merged.update(layers)
            c.execute("UPDATE profile SET layers=?, updated=?, uses=uses+1, label=COALESCE(NULLIF(?, ''), label) WHERE id=?",
                      (json.dumps(merged, ensure_ascii=False), now, label or "", target[0]))
            c.execute("INSERT INTO event (at, profile, owner, job, kind, payload) VALUES (?,?,?,?,?,?)",
                      (now, target[0], str(owner or ""), str(job or "")[:200], kind, json.dumps(layers, ensure_ascii=False)))
        return target[0]

    def events(self, profile_id, limit=50):
        with self.connection() as c:
            rows = c.execute("SELECT at, owner, job, kind, payload FROM event WHERE profile=? ORDER BY id DESC LIMIT ?",
                             (profile_id, limit)).fetchall()
        return [{"at": r[0], "owner": r[1], "job": r[2], "kind": r[3], "layers": json.loads(r[4])} for r in rows]


def match(profiles, layer_names):
    """The profile sharing the most layer names with a drawing (Jaccard ≥ 0.3, at least 3 shared)."""
    names = set(layer_names)
    best, best_score = None, 0.0
    for profile in profiles or []:
        known = set((profile.get("layers") or {}).keys())
        shared = len(names & known)
        if shared < MATCH_MIN_SHARED:
            continue
        score = shared / len(names | known)
        if score >= MATCH_MIN_JACCARD and score > best_score:
            best, best_score = profile, score
    return best
