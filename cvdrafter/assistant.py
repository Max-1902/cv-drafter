"""Chat: tell a pasted posting from a short command, and turn commands into tracker actions."""
from __future__ import annotations

import re
from datetime import date

from . import memory, prompts
from .llm import chat_json

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
APPLIED_RE = re.compile(r"\b(i\s+)?(just\s+)?(applied|sent( it)?|submitted)\b", re.I)
EDITABLE = ("company", "role", "status", "location", "work_mode", "employment_type", "salary", "deadline", "job_ref",
            "url", "sent_at", "follow_up_at", "user_notes")
DATE_FIELDS = ("sent_at", "follow_up_at")
POSTING_HINTS = re.compile(r"\b(responsibilities|requirements|qualifications|what you'll do|about the role|"
                           r"about us|we are looking|you will|benefits|equal opportunity)\b", re.I)


def find_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else ""


def is_posting(text: str) -> bool:
    """A pasted job description, not a chat message."""
    text = text or ""
    return len(text) > 600 or (len(text) > 250 and (text.count("\n") >= 4 or bool(POSTING_HINTS.search(text))))


def says_applied(text: str) -> bool:
    """'applied yesterday, here it is: <posting>' -> the note before the posting says it was already sent."""
    head = (text or "")[:200]
    return bool(APPLIED_RE.search(head)) and not re.search(r"\b(should|will|want to|going to|before)\b", head, re.I)


def quick_action(message: str) -> dict | None:
    """Common one-liners handled without an AI call."""
    m = message.strip().lower().rstrip("!.")
    core = " ".join(re.sub(r"\b(today|it|this|that|now|done|just|ok|okay|yes)\b|[,.!]", " ", m).split())
    if core in ("applied", "sent", "submitted", "i applied", "i sent", "i submitted", "have applied", "i have applied"):
        return {"action": "applied", "reply": "Logged as applied today. I'll remind you to follow up in 7 days."}
    for status in memory.STATUSES:
        if m in (status, f"status {status}", f"status: {status}", f"mark {status}", f"mark as {status}", f"got {status}"):
            return {"action": "update", "fields": {"status": status}, "reply": f"Status set to {status}."}
    if core in ("write", "write cv", "write letter", "write cv + letter", "write cv and letter", "draft", "go", "write them"):
        return {"action": "write", "reply": ""}
    if core in ("tailor", "tailor cv", "tailor the cv", "tailor my cv", "tailor cv too", "rewrite the cv", "rewrite cv"):
        return {"action": "write", "tailor": True, "reply": ""}
    if m in ("render", "pdf", "files", "documents", "export", "make the pdf", "give me the pdf"):
        return {"action": "render", "reply": "Rendering your CV and cover letter."}
    return None


def _summary(app: dict | None) -> str:
    if not app:
        return "(none)"
    return "\n".join(f"{k}: {app.get(k) or ''}" for k in ("id", *EDITABLE) if k != "user_notes")


def route(message: str, current: dict | None, recent: list[dict], model: str) -> dict:
    """What a short message asks for. Unknown fields and statuses are dropped, so the AI can't corrupt a row."""
    action = quick_action(message)
    if action:
        return action
    data = chat_json(prompts.ROUTER_SYSTEM, prompts.ROUTER_USER.format(
        today=date.today().isoformat(), current=_summary(current), statuses=", ".join(memory.STATUSES),
        recent="\n".join(f"{a['id']}: {a['company']} · {a['role']}" for a in recent[:15]) or "(none)", message=message,
    ), model, temperature=0, max_tokens=800, effort="low", task="chat command")
    fields = {k: str(v).strip() for k, v in (data.get("fields") or {}).items() if k in EDITABLE and v is not None}
    if fields.get("status") and fields["status"] not in memory.STATUSES:
        fields.pop("status")
    for k in DATE_FIELDS:  # "next Friday" would break every page that reads the date
        if k in fields and fields[k] and not memory.as_date(fields[k]):
            fields.pop(k)
    try:
        app_id = int(data.get("app_id") or 0) or None
    except (TypeError, ValueError):
        app_id = None
    return {"action": data.get("action") or "chat", "fields": fields, "applied_on": str(data.get("applied_on") or ""),
            "instruction": str(data.get("instruction") or ""), "app_id": app_id,
            "reply": str(data.get("reply") or "")}
