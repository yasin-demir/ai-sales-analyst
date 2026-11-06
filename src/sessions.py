"""Server-issued local sessions. Only token hashes are persisted."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from uuid import uuid4
from typing import Literal

from pydantic import AwareDatetime
from .contracts import ContractModel


def utc_now():
    return datetime.now(timezone.utc)


class AuthenticationRequired(PermissionError):
    pass


class StoreInUse(RuntimeError):
    pass


class APIStoreLock:
    """An OS-released lock prevents two API processes sharing a lease registry."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open('a+b')
        try:
            if path.stat().st_size == 0:
                self._file.write(b'0')
                self._file.flush()
            self._file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            self._file = None
            raise StoreInUse('Another API process is already using this store.') from None

    def close(self):
        if self._file is None:
            return
        if os.name == 'nt':
            import msvcrt
            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        self._file.close()
        self._file = None


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    _digest: str = field(repr=False)
    _issuer: object = field(repr=False)


class SessionCredentials(ContractModel):
    access_token: str
    token_type: Literal['bearer'] = 'bearer'
    expires_at: AwareDatetime


class SessionStore:
    def __init__(self, path, *, ttl_seconds=86400, clock=utc_now):
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError('Session TTL must be a positive integer.')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, expires TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
        self._db.execute('CREATE TABLE IF NOT EXISTS deleted_datasets (id TEXT PRIMARY KEY)')
        self._db.commit()
        self._lock = RLock()
        self._issuer = object()
        self._clock = clock
        self._ttl = ttl_seconds

    def issue(self):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        expires = self._clock() + timedelta(seconds=self._ttl)
        with self._lock, self._db:
            self._db.execute('INSERT INTO sessions(id,digest,expires) VALUES (?,?,?)',
                             (uuid4().hex, digest, expires.isoformat()))
        return SessionCredentials(access_token=token, expires_at=expires)

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) != 43 or not token.isascii():
            raise AuthenticationRequired('A valid local session is required.')
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        with self._lock:
            row = self._db.execute('SELECT id,expires,revoked FROM sessions WHERE digest=?', (digest,)).fetchone()
        if row is None or row[2] or datetime.fromisoformat(row[1]) <= self._clock():
            raise AuthenticationRequired('Session is invalid, expired or revoked.')
        return SessionContext(row[0], digest, self._issuer)

    def require(self, context):
        if not isinstance(context, SessionContext) or context._issuer is not self._issuer:
            raise AuthenticationRequired('Session context must come from this server.')
        with self._lock:
            row = self._db.execute('SELECT expires,revoked FROM sessions WHERE id=? AND digest=?',
                                   (context.session_id, context._digest)).fetchone()
        if row is None or row[1] or datetime.fromisoformat(row[0]) <= self._clock():
            raise AuthenticationRequired('Session is invalid, expired or revoked.')
        return datetime.fromisoformat(row[0])

    def is_active(self, session_id):
        with self._lock:
            row = self._db.execute('SELECT expires,revoked FROM sessions WHERE id=?', (session_id,)).fetchone()
        return bool(row and not row[1] and datetime.fromisoformat(row[0]) > self._clock())

    def revoke(self, context):
        self.require(context)
        with self._lock, self._db:
            self._db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (context.session_id,))

    def mark_deleted(self, dataset_id):
        with self._lock, self._db:
            self._db.execute('INSERT OR IGNORE INTO deleted_datasets(id) VALUES (?)', (dataset_id,))

    def is_deleted(self, dataset_id):
        with self._lock:
            return self._db.execute('SELECT 1 FROM deleted_datasets WHERE id=?', (dataset_id,)).fetchone() is not None

    def prune(self):
        with self._lock, self._db:
            expired = [row[0] for row in self._db.execute('SELECT id,expires,revoked FROM sessions').fetchall()
                       if row[2] or datetime.fromisoformat(row[1]) <= self._clock()]
            self._db.executemany('DELETE FROM sessions WHERE id=?', ((session_id,) for session_id in expired))

    def close(self):
        with self._lock:
            self._db.close()
