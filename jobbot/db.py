"""SQLite application tracker."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Job
from .util import now_iso

# Pipeline-managed statuses, then the ones you set as the process moves on.
STATUSES = [
    "prepared",      # tailored CV ready, waiting for your approval (review mode)
    "applied",       # submitted by jobbot (or marked applied by you)
    "needs_manual",  # jobbot couldn't submit (captcha, unknown questions...) - files ready for you
    "failed",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "archived",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    url TEXT,
    apply_url TEXT,
    description TEXT,
    posted_at TEXT,
    remote INTEGER DEFAULT 0,
    salary TEXT,
    extra TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    keyword_score REAL,
    skip_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    status TEXT NOT NULL,
    match_score REAL,
    llm_score REAL,
    match_summary TEXT,
    ats_before REAL,
    ats_after REAL,
    missing_keywords TEXT,
    cv_pdf TEXT,
    cv_docx TEXT,
    cover_letter TEXT,
    channel TEXT,
    applied_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES applications(id),
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


class Tracker:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- jobs ---------------------------------------------------------
    def upsert_job(self, job: Job) -> str:
        """Store a job. Returns 'new', 'seen' (same posting again) or 'duplicate' (same role via another source)."""
        now = now_iso()
        row = self.conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if row:
            self.conn.execute("UPDATE jobs SET last_seen = ? WHERE id = ?", (now, job.id))
            self.conn.commit()
            return "seen"
        dup = self.conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)).fetchone()
        data = asdict(job)
        self.conn.execute(
            """INSERT INTO jobs (id, source, external_id, dedupe_key, title, company, location, url, apply_url,
                description, posted_at, remote, salary, extra, first_seen, last_seen, skip_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.id, job.source, str(job.external_id), job.dedupe_key, job.title, job.company, job.location,
             job.url, job.apply_url, job.description, job.posted_at, int(job.remote), job.salary,
             json.dumps(data["extra"]), now, now, f"duplicate of {dup['id']}" if dup else None),
        )
        self.conn.commit()
        return "duplicate" if dup else "new"

    def set_job_skip(self, job_id: str, reason: str, keyword_score: float | None = None) -> None:
        self.conn.execute("UPDATE jobs SET skip_reason = ?, keyword_score = COALESCE(?, keyword_score) WHERE id = ?",
                          (reason, keyword_score, job_id))
        self.conn.commit()

    def set_keyword_score(self, job_id: str, score: float) -> None:
        self.conn.execute("UPDATE jobs SET keyword_score = ? WHERE id = ?", (score, job_id))
        self.conn.commit()

    def backlog(self, limit: int = 200) -> list[Job]:
        """Jobs that passed the keyword filter in an earlier run but were never processed further
        (e.g. the per-run limit was reached)."""
        rows = self.conn.execute(
            """SELECT j.* FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
               WHERE a.id IS NULL AND j.skip_reason IS NULL AND j.keyword_score IS NOT NULL
               ORDER BY j.keyword_score DESC LIMIT ?""", (limit,)).fetchall()
        return [_row_to_job(r) for r in rows]

    def get_job(self, job_id: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    # ---- applications -------------------------------------------------
    def create_application(self, job_id: str, status: str, **fields) -> int:
        now = now_iso()
        existing = self.conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if existing:
            self.update_application(existing["id"], status=status, **fields)
            return existing["id"]
        cols = ["job_id", "status", "created_at", "updated_at", *fields]
        vals = [job_id, status, now, now, *[_db_value(v) for v in fields.values()]]
        cur = self.conn.execute(
            f"INSERT INTO applications ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        self.conn.commit()
        self.log(cur.lastrowid, "created", status)
        return cur.lastrowid

    def update_application(self, app_id: int, **fields) -> None:
        if not fields:
            return
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}; choose from {', '.join(STATUSES)}")
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE applications SET {sets} WHERE id = ?",
                          [_db_value(v) for v in fields.values()] + [app_id])
        self.conn.commit()
        if "status" in fields:
            self.log(app_id, "status", fields["status"])

    def log(self, app_id: int, kind: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO events (application_id, at, kind, detail) VALUES (?,?,?,?)",
                          (app_id, now_iso(), kind, detail))
        self.conn.commit()

    def application(self, app_id: int) -> dict | None:
        row = self.conn.execute(_APP_SELECT + " WHERE a.id = ?", (app_id,)).fetchone()
        return dict(row) if row else None

    def applications(self, status: str | None = None) -> list[dict]:
        sql, args = _APP_SELECT, []
        if status:
            sql += " WHERE a.status = ?"
            args.append(status)
        sql += " ORDER BY a.updated_at DESC"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def events(self, app_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM events WHERE application_id = ? ORDER BY id", (app_id,)).fetchall()]

    def applied_since(self, hours: int = 24) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM applications WHERE applied_at IS NOT NULL AND applied_at >= ?", (since,)
        ).fetchone()[0]

    def stats(self) -> dict:
        counts = {r["status"]: r["n"] for r in self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status")}
        counts["jobs_seen"] = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return counts


_APP_SELECT = """
SELECT a.*, j.title, j.company, j.location, j.url, j.apply_url, j.source, j.posted_at, j.description
FROM applications a JOIN jobs j ON j.id = a.job_id
"""


def _db_value(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    if isinstance(v, Path):
        return str(v)
    return v


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        source=row["source"], external_id=row["external_id"], title=row["title"], company=row["company"],
        location=row["location"] or "", url=row["url"] or "", apply_url=row["apply_url"] or "",
        description=row["description"] or "", posted_at=row["posted_at"], remote=bool(row["remote"]),
        salary=row["salary"] or "", extra=json.loads(row["extra"] or "{}"),
    )
