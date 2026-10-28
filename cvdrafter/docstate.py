"""Which CV and cover letter belong to an application, and are they ready? Read from the render records in output/."""
from __future__ import annotations

from datetime import date

LABELS = {"verified": "✅ Verified", "warnings": "🟡 Warnings", "unverified": "❔ AI check didn't run",
          "errors": "⛔ Has errors", "overridden": "⛔ Rendered despite errors", "unchecked": "❔ Not checked",
          "draft": "📝 Draft", "none": "–"}
READY = ("verified",)  # the only state that needs no further look

# Choices for the "Documents" filter on the Applications page.
FILTERS = ["Verified", "Needs a look", "Draft only", "Nothing yet"]


def by_app(entries: list[dict]) -> dict[int, list[dict]]:
    """Render records grouped by application id, newest first. Renders made before ids were recorded are left out."""
    grouped: dict[int, list[dict]] = {}
    for e in sorted(entries, key=lambda m: m.get("rendered_at", ""), reverse=True):
        if e.get("app_id") is not None:
            grouped.setdefault(e["app_id"], []).append(e)
    return grouped


def open_flags(render: dict, kind: str | None = None) -> list[dict]:
    return [f for f in render.get("flags") or [] if not f["resolved"] and kind in (None, f.get("doc"))]


def _doc_of(location: str) -> str | None:
    """Which document a check finding is about: 'cv', 'cl', or None when it concerns both (the company name)."""
    location = str(location)
    if location.startswith("cover_letter"):
        return "cl"
    return None if location in ("company", "fact-check") else "cv"


def check(render: dict, kind: str) -> str:
    """How the check judged one document of a render (kind: 'cv' or 'cl'): a key of LABELS."""
    ev = render.get("evaluation")
    if not ev:
        return "unchecked"
    issues = ev.get("issues")
    if issues is None:  # an older record without the findings: all we have is the overall verdict
        return "overridden" if ev.get("overridden") else ev.get("status") or "unchecked"
    mine = [i for i in issues if _doc_of(i["location"]) in (kind, None)]
    if any(i["level"] == "error" for i in mine):
        return "overridden" if ev.get("overridden") else "errors"
    if any(i["level"] == "warning" for i in mine):
        return "warnings"
    return "verified" if ev.get("ai_checked") else "unverified"


def label(render: dict, kind: str) -> str:
    """'✅ Verified · 🚩 2 flag(s)'"""
    n = len(open_flags(render, kind))
    return LABELS[check(render, kind)] + (f" · 🚩 {n} flag(s)" if n else "")


def state(app: dict, renders: list[dict], kind: str) -> str:
    """The row-level state of one document: its newest render's check, 'draft' if only text exists, else 'none'."""
    if renders:
        return check(renders[0], kind)
    return "draft" if (app.get("final") or app.get("generated")) else "none"


def summary(app: dict, renders: list[dict]) -> str:
    """One of FILTERS: where the application's documents stand overall."""
    states = [state(app, renders, k) for k in ("cv", "cl")]
    if states == ["none", "none"]:
        return "Nothing yet"
    if "draft" in states:
        return "Draft only"
    flagged = renders and open_flags(renders[0])
    return "Verified" if all(s in READY for s in states) and not flagged else "Needs a look"


def when(day: str | None, today: date | None = None) -> str:
    """'2026-10-26T20:51:00' -> 'Oct 26' (with the year when it isn't this one)."""
    try:
        d = date.fromisoformat((day or "")[:10])
    except ValueError:
        return "–"
    return d.strftime("%b %d") + ("" if d.year == (today or date.today()).year else f" {d.year}")
