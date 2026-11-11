"""What the quantity tool has learned from people checking its work — one store for every drawing.

There are countless design offices and the next drawing can come from any of them, so nothing here
is kept per office. Every answer people give (this layer is a wall, that one is not a member, the
mat is 1.2 m thick) is kept as an event (who, when, which job, what), and the knowledge used on the
next drawing is the sum over all of them:

- layer names with the role people confirmed, used on a new drawing only when at least two jobs
  agree (most of the time) and the geometry does not contradict it;
- the words inside layer names ("CONC", "벽체", "GIRDER") with the roles of the layers they were
  in, a hint when the geometry alone cannot decide;
- values people entered (foundation thickness), whose median replaces a fixed assumption.

A job's own answers apply to that job at once (when its drawings are read again). Plain SQLite
next to the job state, kept for good, never sent to an AI.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from contextlib import contextmanager

ROLES = ("wall", "wall_below", "wall_above", "column", "beam", "outline", "opening", "stair")
NONE = "none"                # "not a member", as a vote
NAME_MIN_JOBS = 2            # a layer name is trusted on new drawings after this many jobs ...
NAME_MIN_SHARE = 0.8         # ... agreeing this much
WORD_MIN_VOTES = 3           # a word in layer names is a hint after this many votes ...
WORD_MIN_SHARE = 0.75        # ... this much for one role
MAX_EVENTS_READ = 20000


def words_of(name):
    """Words of a layer name: Latin runs of 2+ letters and Hangul runs ("0_STR_CONC" → STR, CONC)."""
    return [w for w in re.findall(r"[A-Z]{2,}|[가-힣]{2,}", str(name or "").upper())]


class Brain:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, "
                               "profile TEXT, owner TEXT, job TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL)")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def learn(self, layers, owner=None, job=None, kind="confirm"):
        """Keep people's answers {layer name: role or None}. Returns how many were kept."""
        layers = {str(name)[:200]: (role if role in ROLES else None) for name, role in (layers or {}).items()}
        if not layers:
            return 0
        with self.connection() as c:
            c.execute("INSERT INTO event (at, owner, job, kind, payload) VALUES (?,?,?,?,?)",
                      (time.time(), str(owner or ""), str(job or "")[:200], kind, json.dumps(layers, ensure_ascii=False)))
        return len(layers)

    def learn_value(self, name, value, owner=None, job=None):
        """Keep a value a person entered (e.g. name "foundation:tower", value 1.2)."""
        with self.connection() as c:
            c.execute("INSERT INTO event (at, owner, job, kind, payload) VALUES (?,?,?,?,?)",
                      (time.time(), str(owner or ""), str(job or "")[:200], "value",
                       json.dumps({"name": str(name)[:60], "value": float(value)})))

    def _events(self):
        with self.connection() as c:
            rows = c.execute("SELECT job, kind, payload FROM event ORDER BY id DESC LIMIT ?", (MAX_EVENTS_READ,)).fetchall()
        return [(job, kind, json.loads(payload)) for job, kind, payload in rows]

    def corrections(self, job):
        """The latest answer per layer name given in one job (applied to that job as they are)."""
        latest = {}
        for event_job, kind, payload in self._events():  # newest first
            if event_job != job or kind == "value":
                continue
            for name, role in payload.items():
                latest.setdefault(name, role)
        return latest

    def knowledge(self, exclude_job=None):
        """The sum of every job's answers: {"names": {name: {"role", "jobs", "share"}}, "words":
        {word: {role: votes}}, "values": {name: median}}. A job's latest answer per name counts once,
        and a job's value its latest entry."""
        per_job = defaultdict(dict)
        per_job_values = defaultdict(dict)
        for job, kind, payload in self._events():  # newest first
            if kind == "value":
                per_job_values[job].setdefault(payload.get("name"), payload.get("value"))
                continue
            if exclude_job and job == exclude_job:
                continue
            for name, role in payload.items():
                per_job[job].setdefault(name, role or NONE)
        votes = defaultdict(Counter)
        words = defaultdict(Counter)
        for answers in per_job.values():
            for name, role in answers.items():
                votes[name][role] += 1
                for word in set(words_of(name)):
                    words[word][role] += 1
        names = {}
        for name, counter in votes.items():
            role, count = counter.most_common(1)[0]
            names[name] = {"role": None if role == NONE else role, "jobs": count, "share": round(count / sum(counter.values()), 3)}
        values = defaultdict(list)
        for entries in per_job_values.values():
            for name, value in entries.items():
                if isinstance(value, (int, float)):
                    values[name].append(value)
        return {"names": names, "words": {w: dict(c) for w, c in words.items()},
                "values": {k: statistics.median(vs) for k, vs in values.items() if vs}}

    def events(self, limit=50):
        with self.connection() as c:
            rows = c.execute("SELECT at, owner, job, kind, payload FROM event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"at": r[0], "owner": r[1], "job": r[2], "kind": r[3], "payload": json.loads(r[4])} for r in rows]


def trusted_names(knowledge, names):
    """{layer name: role or None} for the names of a drawing that enough jobs agree on."""
    out = {}
    for name in names:
        info = ((knowledge or {}).get("names") or {}).get(name)
        if info and info["jobs"] >= NAME_MIN_JOBS and info["share"] >= NAME_MIN_SHARE:
            out[name] = info["role"]
    return out


def word_hint(knowledge, name):
    """The role the words of a layer name point to (or None), from every job's answers."""
    total = Counter()
    for word in set(words_of(name)):
        total.update(((knowledge or {}).get("words") or {}).get(word) or {})
    if not total:
        return None
    role, count = total.most_common(1)[0]
    if count >= WORD_MIN_VOTES and count / sum(total.values()) >= WORD_MIN_SHARE and role != NONE:
        return role
    return None
