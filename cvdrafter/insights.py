"""Aggregate statistics across all applications."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta

from . import config, prompts
from .llm import LLMError, chat_json
from .memory import as_date
from .textutil import has_term
from .verify import iter_items

DIRECTIONS = {"trading/quant/fintech": "Trading / quant", "ai engineering": "AI engineering",
              "software engineering": "Software engineering", "other": "Other"}
NOT_BUILDABLE = ("soft", "skip")

ACTIVE = ("sent", "interview")
SUCCESS = ("interview", "offer")


def keyword_demand(apps: list[dict]) -> list[dict]:
    """How often each posting keyword appears, and whether you have it."""
    count, have, kinds = Counter(), {}, {}
    for a in apps:
        for k in (a["analysis"] or {}).get("keywords", []):
            if k.get("weight") == "mentioned":  # "you may work with…" lists aren't demand
                continue
            key = k["term"].strip()
            norm = key.lower()
            count[norm] += 1
            have.setdefault(norm, k.get("in_profile", False))
            kinds.setdefault(norm, (key, k.get("type", "hard")))
    return [{"term": kinds[n][0], "type": kinds[n][1], "postings": c, "in_profile": have[n]} for n, c in count.most_common()]


def gap_radar(apps: list[dict], limit: int = 15) -> list[dict]:
    """Hard skills employers keep asking for that aren't in your profile: what to learn or build next."""
    return [k for k in keyword_demand(apps) if not k["in_profile"] and k["type"] == "hard"][:limit]


def fact_usage(apps: list[dict]) -> list[dict]:
    """Which facts end up in applications, and how often those applications got an interview."""
    used, success = Counter(), Counter()
    for a in apps:
        draft = a["final"] or a["generated"]
        if not draft:
            continue
        ids = {s for _, _, item, _ in iter_items(draft) for s in item.get("sources") or []}
        for fid in ids:
            used[fid] += 1
            if a["status"] in SUCCESS:
                success[fid] += 1
    return [{"fact": f, "applications": n, "interviews": success[f]} for f, n in used.most_common()]


def coverage(a: dict) -> float | None:
    reqs = [r for r in (a["analysis"] or {}).get("requirements", []) if r["importance"] == "must"]
    if not reqs:
        return None
    return sum(r["status"] == "covered" for r in reqs) / len(reqs)


def follow_ups_due(apps: list[dict], today: date | None = None) -> list[dict]:
    today = today or date.today()
    due = []
    for a in apps:
        follow_up = as_date(a.get("follow_up_at"))
        if a["status"] in ACTIVE and follow_up and follow_up <= today:
            due.append(a)
    return due


def weekly_counts(apps: list[dict], weeks: int = 10) -> list[dict]:
    start = date.today() - timedelta(weeks=weeks - 1, days=date.today().weekday())
    buckets = Counter()
    for a in apps:
        d = datetime.fromisoformat(a["created_at"]).date()
        if d >= start:
            buckets[(d - timedelta(days=d.weekday())).isoformat()] += 1
    return [{"week": (start + timedelta(weeks=i)).isoformat(), "applications": buckets[(start + timedelta(weeks=i)).isoformat()]}
            for i in range(weeks)]


# ---------------------------------------------------------------- what each direction asks for

def direction(a: dict) -> str:
    return DIRECTIONS.get((a["analysis"] or {}).get("role_type"), "Other")


def _asked(a: dict) -> list[dict]:
    """The posting's keywords that count as demand ("you may work with…" lists don't)."""
    return [k for k in (a["analysis"] or {}).get("keywords", []) if k.get("term") and k.get("weight") != "mentioned"]


def _fallback(k: dict) -> dict:
    term = k["term"].strip()
    return {"skill": re.sub(r"\s*\([^)]*\)", "", term).strip() or term, "category": "soft" if k.get("type") == "soft" else "other"}


def skill_map(apps: list[dict], model: str | None) -> tuple[dict, bool]:
    """Raw posting keyword -> {skill, category}, so "ReactJS" and "React" count as one skill. New terms go to the
    model once and are saved; with no model (or if it fails) they get a plain cleanup. Second value: all mapped by AI."""
    path = config.SKILL_MAP_PATH
    try:
        known = json.loads(path.read_text())
    except (OSError, ValueError):
        known = {}
    new = sorted({k["term"].strip() for a in apps for k in _asked(a)} - set(known))
    if new and model:
        try:
            existing = sorted({v["skill"] for v in known.values() if v["category"] not in NOT_BUILDABLE})
            data = chat_json(prompts.SKILLS_SYSTEM, prompts.SKILLS_USER.format(
                existing=", ".join(existing) or "(none yet)", terms="\n".join(f"- {t}" for t in new)),
                model, temperature=0, effort="low", task="group keywords")
            got = {t["term"]: t for t in data.get("terms") or [] if t.get("term") in new and t.get("skill")}
            for term in new if got else []:  # a term the model leaves out isn't a skill (degree, job title, …)
                t = got.get(term) or {"skill": term, "category": "skip"}
                known[term] = {"skill": str(t["skill"]).strip(), "category": str(t.get("category") or "other")}
            path.write_text(json.dumps(known, indent=1, ensure_ascii=False, sort_keys=True))
        except LLMError:
            pass
    mapping = {k["term"].strip(): known.get(k["term"].strip()) or _fallback(k) for a in apps for k in _asked(a)}
    return mapping, not [t for t in new if t not in known]


def direction_demand(apps: list[dict], mapping: dict, profile_text: str, group=direction) -> dict[str, dict]:
    """Per direction (or any other grouping): how many postings, and for each skill the share of them asking for it."""
    out: dict[str, dict] = {}
    for a in apps:
        d = out.setdefault(group(a), {"postings": 0, "skills": {}})
        d["postings"] += 1
        seen = set()
        for k in _asked(a):
            m = mapping[k["term"].strip()]
            name = m["skill"]
            row = d["skills"].setdefault(name.lower(), {"skill": name, "category": m["category"], "postings": 0,
                                                        "have": False, "companies": [], "spellings": set()})
            row["spellings"].add(k["term"].strip())
            row["have"] = row["have"] or bool(k.get("in_profile")) or has_term(name, profile_text)
            if name.lower() not in seen:
                seen.add(name.lower())
                row["postings"] += 1
                row["companies"].append(a["company"] or "?")
    for d in out.values():
        rows = sorted(d["skills"].values(), key=lambda r: (-r["postings"], r["skill"].lower()))
        d["skills"] = [r | {"share": r["postings"] / d["postings"], "spellings": sorted(r["spellings"])} for r in rows]
    return out


def suggest_projects(name: str, demand: dict, profile: dict, model: str) -> list[dict]:
    """Portfolio projects that cover the skills this direction asks for most and you don't have yet."""
    rows = [r for r in demand["skills"] if r["category"] not in NOT_BUILDABLE][:30]
    lines = "\n".join(f"{r['share']:.0%} · {r['skill']} · {'has it' if r['have'] else 'MISSING'}" for r in rows)
    background = "\n".join(f"- {e.get('name') or e.get('company')}: " + "; ".join(f["text"] for f in e["facts"][:3])
                            for sec in ("projects", "experience") for e in profile[sec])
    data = chat_json(prompts.PROJECTS_SYSTEM, prompts.PROJECTS_USER.format(
        direction=name, n=demand["postings"], demand=lines, background=background), model, temperature=0.4,
        task="project ideas")
    return [p for p in data.get("projects") or [] if p.get("title")][:3]
