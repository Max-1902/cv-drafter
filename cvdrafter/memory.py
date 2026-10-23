"""Application history in SQLite. Past approved applications become examples for new ones."""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from . import config

STATUSES = ["draft", "ready", "sent", "interview", "offer", "rejected", "withdrawn"]
# How much a past application's style should count when picking examples.
STATUS_WEIGHT = {"offer": 2.0, "interview": 1.6, "sent": 1.0, "ready": 1.0, "rejected": 0.6}

_COLUMNS = ["id", "created_at", "company", "role", "language", "jd", "notes", "analysis_json",
            "generated_json", "final_json", "files_json", "status", "user_notes",
            "sent_at", "follow_up_at", "prep_json", "url",
            "location", "work_mode", "employment_type", "salary", "deadline", "job_ref", "company_confidence"]
_JSON = ("analysis", "generated", "final", "files", "prep")
# Pulled from the posting by the analyst and stored as plain columns, so the log table and SQL queries can use them.
JOB_FIELDS = ("location", "work_mode", "employment_type", "salary", "deadline", "job_ref")
# Columns added after the first release; init_db adds them to older databases.
_MIGRATIONS = {"sent_at": "TEXT", "follow_up_at": "TEXT", "prep_json": "TEXT", "url": "TEXT DEFAULT ''",
               **{f: "TEXT DEFAULT ''" for f in JOB_FIELDS}, "company_confidence": "TEXT DEFAULT ''"}
FOLLOW_UP_DAYS = 7
# Share of word pairs two postings must have in common to count as the same posting pasted again.
DUPLICATE_OVERLAP = 0.85


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close (sqlite3's own context manager doesn't close)."""
    config.DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, company TEXT, role TEXT, language TEXT, jd TEXT, notes TEXT,
            analysis_json TEXT, generated_json TEXT, final_json TEXT, files_json TEXT,
            status TEXT DEFAULT 'draft', user_notes TEXT DEFAULT '')""")
        have = {r["name"] for r in c.execute("PRAGMA table_info(applications)")}
        for col, kind in _MIGRATIONS.items():
            if col not in have:
                c.execute(f"ALTER TABLE applications ADD COLUMN {col} {kind}")
        c.execute("""CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, task TEXT, model TEXT, via TEXT, input INTEGER,
            cached INTEGER, output INTEGER, seconds REAL, billed REAL, api_value REAL)""")
        # Every status change, with its time: the application's timeline.
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, app_id INTEGER, at TEXT, kind TEXT, detail TEXT)""")
        # Every CV + cover letter the writer produced, with its time. Regenerating adds a row, never replaces one.
        c.execute("""CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, app_id INTEGER, at TEXT, draft_json TEXT)""")


def _log(c: sqlite3.Connection, app_id: int, kind: str, detail: str = "") -> None:
    c.execute("INSERT INTO events (app_id, at, kind, detail) VALUES (?,?,?,?)",
              (app_id, datetime.now().isoformat(timespec="seconds"), kind, detail))


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for key in _JSON:
        raw = d.pop(f"{key}_json", None)
        d[key] = json.loads(raw) if raw else None
    return d


def create_application(company, role, language, jd, notes, analysis, url: str = "") -> int:
    """Store a new application, with the job details and company check the analyst pulled from the posting."""
    job = (analysis or {}).get("job") or {}
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO applications (created_at, company, role, language, jd, notes, analysis_json, url, company_confidence, "
            f"{', '.join(JOB_FIELDS)}) VALUES ({', '.join('?' * (9 + len(JOB_FIELDS)))})",
            (datetime.now().isoformat(timespec="seconds"), company, role, language, jd, notes,
             json.dumps(analysis, ensure_ascii=False), url or "", ((analysis or {}).get("company_check") or {}).get("confidence", ""),
             *(str(job.get(f) or "") for f in JOB_FIELDS)),
        )
        _log(c, cur.lastrowid, "created", f"{company} · {role}")
        return cur.lastrowid


def log_event(app_id: int, kind: str, detail: str = "") -> None:
    with _conn() as c:
        _log(c, app_id, kind, detail)


def update(app_id: int, **fields) -> None:
    """Save fields. A new `generated` draft is also kept as its own timestamped version (see drafts())."""
    sets, values = [], []
    for key, value in fields.items():
        col = f"{key}_json" if key in _JSON else key
        if col not in _COLUMNS:
            raise ValueError(f"Unknown column {col}")
        sets.append(f"{col} = ?")
        values.append(json.dumps(value, ensure_ascii=False) if col.endswith("_json") else value)
    with _conn() as c:
        old = c.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()
        c.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id = ?", (*values, app_id))
        if old and "status" in fields and fields["status"] != old["status"]:
            _log(c, app_id, "status", f"{old['status']} → {fields['status']}")
        if old and fields.get("generated"):
            at = datetime.now().isoformat(timespec="seconds")
            c.execute("INSERT INTO drafts (app_id, at, draft_json) VALUES (?,?,?)",
                      (app_id, at, json.dumps(fields["generated"], ensure_ascii=False)))
            _log(c, app_id, "generated", "CV + cover letter written")


def as_date(value) -> date | None:
    """A stored date ('2026-10-01' or a full timestamp) as a date; None when empty or not a date."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def mark_ready(app_id: int, **fields) -> None:
    """Save fields (e.g. rendered files). A draft becomes 'ready'; a later status (sent, interview…) is never undone."""
    app = get(app_id)
    if app and app["status"] == "draft":
        fields["status"] = "ready"
    update(app_id, **fields)


def mark_applied(app_id: int, on: date | None = None) -> None:
    """You sent it: status 'sent', today's date, and a follow-up reminder a week later (unless one is set)."""
    on = on or date.today()
    app = get(app_id)
    follow = app.get("follow_up_at") or (on + timedelta(days=FOLLOW_UP_DAYS)).isoformat()
    status = app["status"] if app["status"] in ("interview", "offer", "rejected") else "sent"
    update(app_id, status=status, sent_at=on.isoformat(), follow_up_at=follow)


def log_call(row: dict) -> None:
    with _conn() as c:
        c.execute("INSERT INTO llm_calls (at, task, model, via, input, cached, output, seconds, billed, api_value) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), *(row[k] for k in (
                      "task", "model", "via", "input", "cached", "output", "seconds", "billed", "api_value"))))


def spend() -> dict:
    """Dollars actually billed (OpenRouter / API key). Subscription calls count as 0."""
    today = date.today().isoformat()
    with _conn() as c:
        total = lambda since: c.execute("SELECT COALESCE(SUM(billed), 0) FROM llm_calls WHERE at >= ?", (since,)).fetchone()[0]  # noqa: E731
        return {"billed_today": total(today), "billed_month": total(today[:8] + "01"), "billed_ever": total("")}


def events(app_id: int) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE app_id = ? ORDER BY id", (app_id,)).fetchall()]


def drafts(app_id: int | None = None) -> list[dict]:
    """Every generated CV + cover letter, newest first, with the company and role of its application."""
    where, args = ("WHERE d.app_id = ?", (app_id,)) if app_id is not None else ("", ())
    with _conn() as c:
        rows = c.execute(f"SELECT d.id, d.app_id, d.at, d.draft_json, a.company, a.role FROM drafts d "
                         f"JOIN applications a ON a.id = d.app_id {where} ORDER BY d.at DESC, d.id DESC", args).fetchall()
    return [{**{k: r[k] for k in ("id", "app_id", "at", "company", "role")}, "draft": json.loads(r["draft_json"])} for r in rows]


def get(app_id: int) -> dict | None:
    with _conn() as c:
        return _row(c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def list_all() -> list[dict]:
    with _conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()]


def _shingles(text: str) -> set[str]:
    """Word pairs of a posting, ignoring case, punctuation and links, so a re-paste with a note or URL still matches."""
    words = re.sub(r"https?://\S+", " ", (text or "").lower())
    words = re.findall(r"[a-z0-9]+", words)
    return {f"{a} {b}" for a, b in zip(words, words[1:])}


def find_duplicate(jd: str, url: str = "") -> dict | None:
    """An application already logged for this posting: same link, or nearly the same text. Checked before any AI call."""
    mine = _shingles(jd)
    for app in list_all():
        if url and app["url"] and url.rstrip("/") == app["url"].rstrip("/"):
            return app
        theirs = _shingles(app["jd"])
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= DUPLICATE_OVERLAP:
            return app
    return None


def delete(app_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        c.execute("DELETE FROM events WHERE app_id = ?", (app_id,))
        c.execute("DELETE FROM drafts WHERE app_id = ?", (app_id,))
        # Without this, deleting the newest application leaves a gap: the next one would skip its number.
        c.execute("UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id), 0) FROM applications) WHERE name = 'applications'")


def similar_examples(keywords: list[str], exclude_id: int | None = None, k: int = 2) -> list[dict]:
    """Past applications you approved, ranked by keyword overlap and how well they did."""
    target = {t.lower() for t in keywords}
    scored = []
    for app in list_all():
        if app["id"] == exclude_id or not app["final"] or app["status"] in ("draft", "withdrawn"):
            continue
        theirs = {kw["term"].lower() for kw in (app["analysis"] or {}).get("keywords", [])}
        overlap = len(target & theirs) / max(1, len(target | theirs))
        scored.append((overlap * STATUS_WEIGHT.get(app["status"], 1.0), app))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for score, a in scored[:k] if score > 0]


def stats() -> dict:
    apps = list_all()
    by_status = {s: sum(a["status"] == s for a in apps) for s in STATUSES}
    sent = sum(by_status[s] for s in ("sent", "interview", "offer", "rejected"))
    return {"total": len(apps), "by_status": by_status, "sent": sent,
            "interview_rate": (by_status["interview"] + by_status["offer"]) / sent if sent else None}
