"""Small local event store, shared by hooks and waiting processes."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class AmbiguousAgent(ValueError):
    pass


def default_db() -> Path:
    if value := os.environ.get("SUPERWAIT_DB"):
        return Path(value).expanduser().absolute()
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "superwait/events.sqlite3"


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_db()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    provider TEXT NOT NULL, session TEXT NOT NULL,
                    key TEXT NOT NULL, state TEXT NOT NULL,
                    at REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_lookup
                    ON events(kind, provider, session, key, seq);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def emit(self, kind, key, state, *, provider="", session="", data=None):
        payload = json.dumps(data or {}, ensure_ascii=False)
        if len(payload.encode()) > 65536:
            raise ValueError("event data must fit in 64 KiB")
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO events(kind,provider,session,key,state,at,data) VALUES(?,?,?,?,?,?,?)",
                (kind, provider, session, key, state, time.time(), payload),
            )
            return cursor.lastrowid

    @staticmethod
    def decode(row):
        return {**dict(row), "data": json.loads(row["data"])}

    def latest(self, kind, key, *, provider="", session=None):
        query = "SELECT * FROM events WHERE kind=? AND provider=? AND key=?"
        args = [kind, provider, key]
        if session is not None:
            query += " AND session=?"
            args.append(session)
        query = "SELECT * FROM (" + query + " ORDER BY seq DESC) GROUP BY session HAVING seq=MAX(seq)"
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        if len(rows) > 1:
            raise ValueError("agent ID exists in multiple sessions; specify session")
        return self.decode(rows[0]) if rows else None

    def signal(self, key, state=None, after=0):
        query = "SELECT * FROM events WHERE kind='signal' AND key=? AND seq>?"
        args = [key, after]
        if state is not None:
            query += " AND state=?"
            args.append(state)
        with self.connect() as db:
            row = db.execute(query + " ORDER BY seq DESC LIMIT 1", args).fetchone()
        return self.decode(row) if row else None

    def agent(self, handle, provider, session=None):
        if provider == "codex" and handle.startswith("/") and session is None:
            raise AmbiguousAgent("Codex task paths are session-local; include session from the Superwait SessionStart context.")
        matches = [a for a in self.agents(provider, session, None)
                   if a["key"] == handle or handle in a["data"].get("aliases", [])]
        if len(matches) > 1:
            raise AmbiguousAgent(f"agent handle {handle!r} is ambiguous; specify its parent session")
        return matches[0] if matches else None

    def agents(self, provider=None, session=None, limit=50):
        query = "SELECT * FROM events WHERE kind='agent'"
        args = []
        for name, value in (("provider", provider), ("session", session)):
            if value is not None:
                query += f" AND {name}=?"
                args.append(value)
        query += " AND seq IN (SELECT MAX(seq) FROM events WHERE kind='agent' GROUP BY provider,session,key) ORDER BY seq DESC"
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        with self.connect() as db:
            return [self.decode(r) for r in db.execute(query, args)]

    def prune(self, days=30):
        with self.connect() as db:
            return db.execute("DELETE FROM events WHERE at < ?", (time.time() - days * 86400,)).rowcount
