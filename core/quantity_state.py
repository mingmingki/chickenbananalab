"""Durable, process-shared quantity review and job state on one server."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

RETENTION_SECONDS = 7 * 24 * 60 * 60


class QuantityStateStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self, write=False):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        try:
            os.chmod(self.path, 0o600)
            connection.execute('PRAGMA busy_timeout=15000')
            connection.execute('CREATE TABLE IF NOT EXISTS quantity_state '
                               '(namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, '
                               'expires REAL NOT NULL, PRIMARY KEY(namespace,key))')
            connection.execute('CREATE INDEX IF NOT EXISTS quantity_state_expiry ON quantity_state(expires)')
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                connection.execute('COMMIT')
        except BaseException:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            raise
        finally:
            connection.close()

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

    @staticmethod
    def read(connection, namespace, key):
        row = connection.execute('SELECT value FROM quantity_state WHERE namespace=? AND key=? AND expires>?',
                                 (namespace, str(key), time.time())).fetchone()
        return json.loads(row[0]) if row else None

    @classmethod
    def write(cls, connection, namespace, key, value, ttl):
        encoded = cls.encode(value)
        connection.execute('INSERT INTO quantity_state VALUES (?,?,?,?) '
                           'ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,expires=excluded.expires',
                           (namespace, str(key), encoded, time.time() + ttl))
        connection.execute('DELETE FROM quantity_state WHERE expires<?', (time.time(),))

    def get(self, namespace, key):
        if not key:
            return None
        with self.connection() as connection:
            return self.read(connection, namespace, key)

    def put(self, namespace, key, value, ttl=RETENTION_SECONDS):
        if not key:
            return
        with self.connection(write=True) as connection:
            self.write(connection, namespace, key, value, ttl)

    def pop(self, namespace, key):
        with self.connection(write=True) as connection:
            value = self.read(connection, namespace, key)
            connection.execute('DELETE FROM quantity_state WHERE namespace=? AND key=?', (namespace, str(key)))
            return value

    def update(self, namespace, key, fields, ttl=RETENTION_SECONDS):
        with self.connection(write=True) as connection:
            value = self.read(connection, namespace, key)
            if value is None:
                return False
            value.update(fields)
            self.write(connection, namespace, key, value, ttl)
            return True

    def get_or_create(self, namespace, key, default, ttl=RETENTION_SECONDS):
        with self.connection(write=True) as connection:
            value = self.read(connection, namespace, key)
            if value is None:
                self.write(connection, namespace, key, default, ttl)
                return default
            return value

    def begin_job(self, key, owner, operation, fingerprint):
        with self.connection(write=True) as connection:
            prior = self.read(connection, 'job', key)
            if prior:
                if prior['owner'] != str(owner):
                    return 'forbidden'
                if prior['operation'] != operation or prior['fingerprint'] != fingerprint:
                    return 'conflict'
                return 'duplicate'
            self.write(connection, 'job', key, {
                'owner': str(owner), 'operation': operation, 'fingerprint': fingerprint,
                'pid': os.getpid(), 'created_at': time.time(),
            }, RETENTION_SECONDS)
            return 'new'

    def release_job(self, key):
        self.pop('job', key)

    def claim_resume(self, key, owner):
        """Bind an interrupted job to this worker; a job whose worker still runs is not taken."""
        with self.connection(write=True) as connection:
            job = self.read(connection, 'job', key)
            if not job:
                return 'missing'
            if job['owner'] != str(owner):
                return 'forbidden'
            if worker_alive(job.get('pid')):
                return 'running'
            job.update(pid=os.getpid(), resumed_at=time.time(),
                       resume_count=int(job.get('resume_count') or 0) + 1)
            self.write(connection, 'job', key, job, RETENTION_SECONDS)
            return 'resumed'

    def owner_jobs(self, owner):
        with self.connection() as connection:
            rows = connection.execute('SELECT key,value FROM quantity_state WHERE namespace=? AND expires>?',
                                      ('job', time.time())).fetchall()
        return sorted([dict(json.loads(value), job_id=key) for key, value in rows
                       if json.loads(value).get('owner') == str(owner)],
                      key=lambda row: row['created_at'], reverse=True)

    def claim_confirmation(self, key, fingerprint):
        with self.connection(write=True) as connection:
            prior = self.read(connection, 'confirmation_lock', key)
            if prior and worker_alive(prior.get('pid')):
                return False
            self.write(connection, 'confirmation_lock', key,
                       {'fingerprint': fingerprint, 'pid': os.getpid()}, 10 * 60)
            return True

    def finish_confirmation(self, key, fingerprint, response):
        """Commit result, retry receipt and extraction consumption together."""
        with self.connection(write=True) as connection:
            lock = self.read(connection, 'confirmation_lock', key)
            if not lock or lock.get('fingerprint') != fingerprint or lock.get('pid') != os.getpid():
                raise RuntimeError('Confirmation lease is no longer owned by this worker')
            previous = self.read(connection, 'result', key) or {}
            results = dict(previous.get('results') or {}, **response['results'])
            self.write(connection, 'confirmation', key,
                       {'fingerprint': fingerprint, 'response': response}, RETENTION_SECONDS)
            self.write(connection, 'result', key,
                       {'ok': True, 'results': results, '_created_at': time.time()}, RETENTION_SECONDS)
            connection.execute('DELETE FROM quantity_state WHERE key=? AND namespace IN (?,?)',
                               (str(key), 'extraction', 'confirmation_lock'))


def worker_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
