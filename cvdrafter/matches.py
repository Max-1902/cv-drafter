"""Where exactly the draft answers the posting: each keyword and requirement, and the CV/letter lines that cover it."""
from __future__ import annotations

from .textutil import has_term, plain
from .verify import iter_items


def _label(loc: str, profile: dict) -> str:
    names = {e["id"]: e.get("company") or e.get("name") for s in ("experience", "projects") for e in profile[s]}
    parts = loc.split("/")
    if parts[0] == "cover_letter":
        return f"Letter ¶{parts[1]}"
    return f"{names.get(parts[1], parts[1])} · bullet {parts[2]}"


def _locked(profile: dict) -> dict[str, str]:
    """Fact id -> CV label for lines every CV prints as-is (education, languages, activities)."""
    out = {}
    for e in profile["education"]:
        out[e["id"]] = f"Education · {e.get('institution', e['id'])}"
        out.update({f["id"]: out[e["id"]] for f in e["facts"] if not f.get("learned")})
    for name, prefix in (("languages", "lang"), ("activities", "activity")):
        out.update({f"{prefix}.{i}": name.capitalize() for i in range(1, len(profile[name]) + 1)})
    return out


def match_map(draft: dict, analysis: dict, profile: dict) -> dict:
    items = [(loc, plain(item.get("text", "")), set(item.get("sources") or [])) for loc, _, item, _ in iter_items(draft)]
    skills = [s for g in draft.get("skills") or [] for s in g.get("items") or []]

    keywords = []
    for k in analysis.get("keywords", []):
        where = [_label(loc, profile) for loc, text, _ in items if has_term(k["term"], text)]
        if any(has_term(k["term"], s) for s in skills):
            where.append("Skills")
        state = "used" if where else ("unused" if k.get("in_profile") else "blocked")
        keywords.append({"term": k["term"], "type": k.get("type", "hard"), "weight": k.get("weight", "required"),
                         "state": state, "where": where})

    locked = _locked(profile)
    requirements = []
    for r in analysis.get("requirements", []):
        ids = set(r.get("fact_ids") or [])
        where = [_label(loc, profile) for loc, _, sources in items if ids & sources]
        where += dict.fromkeys(locked[i] for i in sorted(ids) if i in locked)
        requirements.append({**r, "where": where,
                             "shown": r["status"] == "missing" or bool(where)})  # covered by you, but not in this draft
    return {"keywords": keywords, "requirements": requirements}
