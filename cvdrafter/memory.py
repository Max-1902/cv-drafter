"""Application history in SQLite. Past approved applications become examples for new ones."""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from . import config

STATUSES = ["draft", "ready", "sent", "interview", "offer", "rejected", "withdrawn"]
# How much a past application's style should count when picking examples.
STATUS_WEIGHT = {"offer": 2.0, "interview": 1.6, "sent": 1.0, "ready": 1.0, "rejected": 0.6}

_COLUMNS = ["id", "created_at", "company", "role", "language", "jd", "notes", "analysis_json",
            "generated_json", "final_json", "files_json", "status", "user_notes",
            "sent_at", "follow_up_at", "prep_json", "answers_json", "url",
            "location", "work_mode", "employment_type", "salary", "deadline", "job_ref", "company_confidence"]
_JSON = ("analysis", "generated", "final", "files", "prep", "answers")
# Pulled from the posting by the analyst and stored as plain columns, so the log table and SQL queries can use them.
JOB_FIELDS = ("location", "work_mode", "employment_type", "salary", "deadline", "job_ref")
# Columns added after the first release; init_db adds them to older databases.
_MIGRATIONS = {"sent_at": "TEXT", "follow_up_at": "TEXT", "prep_json": "TEXT", "answers_json": "TEXT", "url": "TEXT DEFAULT ''",
               **{f: "TEXT DEFAULT ''" for f in JOB_FIELDS}, "company_confidence": "TEXT DEFAULT ''"}
FOLLOW_UP_DAYS = 7
# Share of word pairs two postings must have in common to count as the same posting pasted again.
DUPLICATE_OVERLAP = 0.85
# ...or share of the shorter posting's word pairs found in the longer one (the same posting with extra LinkedIn
# boilerplate, or only part of it), once the shorter one is long enough to tell.
DUPLICATE_CONTAINED, DUPLICATE_MIN_PAIRS = 0.9, 40
# Query parameters that identify a job; everything else in a link (utm_, gh_src, trk…) is tracking.
URL_ID_PARAMS = ("gh_jid", "jid", "jobid", "job_id", "currentjobid", "jk", "req", "reqid", "posting_id", "id")
# Words in company names that don't tell two companies apart.
GENERIC_NAME_WORDS = {"inc", "llc", "ltd", "lp", "llp", "co", "corp", "corporation", "company", "group", "holdings",
                      "the", "and", "of", "unnamed", "startup", "a", "an"}
ROLE_STOPWORDS = {"and", "the", "for", "with", "team", "program", "programme", "position", "role"}


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


def last_activity() -> dict[int, str]:
    """Application id -> time of its newest event (generated, rendered, status change…)."""
    with _conn() as c:
        return {r["app_id"]: r["at"] for r in c.execute("SELECT app_id, MAX(at) AS at FROM events GROUP BY app_id")}


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


def url_key(url: str) -> str:
    """A job link without tracking: host (no www), path and the id parameters. Greenhouse and LinkedIn jobs reduce
    to their job id, so the board link and the company-site link of one job match."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url if "://" in url else "https://" + url)
    host, path = parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/").lower()
    query = {k.lower(): v[0] for k, v in parse_qs(parts.query).items() if v}
    if m := re.search(r"greenhouse\.io/.*?/jobs/(\d+)", host + path):
        return f"greenhouse:{m.group(1)}"
    if query.get("gh_jid"):
        return f"greenhouse:{query['gh_jid']}"
    if "linkedin.com" in host and (m := re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", path) or
                                   re.fullmatch(r"(\d+)", query.get("currentjobid", ""))):
        return f"linkedin:{m.group(1)}"
    ids = [f"{k}={query[k]}" for k in URL_ID_PARAMS if k in query and query[k].lower() not in path]
    return host + path + ("?" + "&".join(ids) if ids else "")


def _same_text(mine: set[str], theirs: set[str]) -> bool:
    if not mine or not theirs:
        return False
    common = len(mine & theirs)
    return (common / len(mine | theirs) >= DUPLICATE_OVERLAP
            or (min(len(mine), len(theirs)) >= DUPLICATE_MIN_PAIRS and common / min(len(mine), len(theirs)) >= DUPLICATE_CONTAINED))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _name_cores(company: str) -> list[set[str]]:
    """The distinctive words of a company name and of its alias in brackets: "Northwind Trading (Northwind Group)" ->
    [{northwind, trading}, {northwind}]. Empty for names that say nothing ("Unnamed startup")."""
    names = [re.sub(r"\(.*?\)", " ", company or "")] + re.findall(r"\((.*?)\)", company or "")
    cores = [_words(n) - GENERIC_NAME_WORDS for n in names]
    return [c for c in cores if c]


def _role_stems(role: str) -> set[str]:
    return {w[:5] for w in _words(role) if len(w) > 2 and w not in ROLE_STOPWORDS}


def _same_company(a: str, b: str) -> bool:
    words_a, words_b = _words(a), _words(b)
    return any(core <= words_b for core in _name_cores(a)) or any(core <= words_a for core in _name_cores(b))


def _same_role(a: str, b: str) -> bool:
    sa, sb = _role_stems(a), _role_stems(b)
    if not sa or not sb:
        return False
    small, large = sorted((sa, sb), key=len)
    return len(sa & sb) / len(sa | sb) >= 0.6 or (len(small) >= 2 and small <= large)


def _named_in(app: dict, text: str) -> bool:
    """The posting names this application's company and nearly all of its role title."""
    words, stems = _words(text), {w[:5] for w in _words(text)}
    role = _role_stems(app["role"])
    return (any(core <= words for core in _name_cores(app["company"])) and len(role) >= 2
            and len(role & stems) / len(role) >= 0.8)


def find_duplicate(jd: str, url: str = "") -> dict | None:
    """An application already logged for this posting, checked before any AI call: the same job link, nearly the
    same text, or (for one logged without its posting, e.g. imported) a posting naming its company and role.
    The result has "match": "link", "text" or "name"."""
    mine, key = _shingles(jd), url_key(url)
    apps = list_all()
    for app in apps:
        if key and app["url"] and key == url_key(app["url"]):
            return {**app, "match": "link"}
    for app in apps:
        if _same_text(mine, _shingles(app["jd"])):
            return {**app, "match": "text"}
    for app in apps:
        if not (app["jd"] or "").strip() and _named_in(app, jd):
            return {**app, "match": "name"}
    return None


def find_same_job(company: str, role: str, exclude: int | None = None) -> dict | None:
    """An application with the same company and role, once the analysis has read them from a new posting."""
    if not company or not role:
        return None
    return next((a for a in list_all() if a["id"] != exclude and _same_company(company, a["company"])
                 and _same_role(role, a["role"])), None)


def attach_posting(app_id: int, jd: str, analysis: dict, url: str = "") -> dict:
    """Give an application logged without its posting (e.g. imported) the posting and its analysis. Status, dates,
    notes and every detail you already entered stay; only empty job details are filled in from the posting."""
    app = get(app_id)
    job = analysis.get("job") or {}
    fields = {"jd": jd, "analysis": analysis, **{f: str(job.get(f) or "") for f in JOB_FIELDS if not app[f] and job.get(f)}}
    if url and not app["url"]:
        fields["url"] = url
    update(app_id, **fields)
    log_event(app_id, "posting", "posting attached")
    return get(app_id)


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
