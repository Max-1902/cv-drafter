"""The fact bank: everything true about you, each fact with a stable id the AI must cite."""
from __future__ import annotations

import re
from datetime import date

import yaml

from . import config
from .textutil import slug

# Sections whose entries carry their own facts, and the keys used to build an entry id.
ENTRY_SECTIONS = {
    "experience": ("company", "title"),
    "projects": ("name",),
    "education": ("institution", "degree"),
}


def _norm_facts(raw, parent: str) -> list[dict]:
    out = []
    for i, f in enumerate(raw or [], 1):
        if f is None:
            continue
        if not isinstance(f, dict):
            f = {"text": f}
        fact = {
            "id": str(f.get("id") or f"{parent}.{i}"),
            "text": str(f.get("text", "")).strip(),
            "skills": [str(s) for s in (f.get("skills") or [])],
        }
        if f.get("about"):  # the job/project id this fact came from (e.g. an opinion earned on one project)
            fact["about"] = str(f["about"])
        out.append(fact)
    return out


def load_guardrails() -> dict:
    if not config.GUARDRAILS_PATH.exists():
        return {"never_mention": [], "banned_phrases": [], "claim_guards": [], "never_mention_for": {}}
    with open(config.GUARDRAILS_PATH, encoding="utf-8") as f:
        g = yaml.safe_load(f) or {}
    out = {k: [str(x) for x in (g.get(k) or [])] for k in ("never_mention", "banned_phrases", "claim_guards")}
    # entry id -> terms that must never appear in text about that job/project
    scoped = g.get("never_mention_for") if isinstance(g.get("never_mention_for"), dict) else {}
    out["never_mention_for"] = {str(k): [str(x) for x in (v or [])] for k, v in scoped.items()}
    return out


def load_learned() -> list[dict]:
    if not config.LEARNED_FACTS_PATH.exists():
        return []
    with open(config.LEARNED_FACTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def save_learned(items: list[dict]) -> None:
    with open(config.LEARNED_FACTS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(items, f, sort_keys=False, allow_unicode=True)


def add_learned_fact(target: str, text: str, skills: list[str]) -> dict:
    """Store a fact you confirmed (e.g. answering a gap question). Your profile.yaml is never rewritten."""
    items = load_learned()
    n = max([int(str(i["id"])[1:]) for i in items if re.fullmatch(r"L\d+", str(i.get("id", "")))] or [0]) + 1
    item = {"id": f"L{n}", "target": target, "text": text.strip(), "skills": skills, "added": date.today().isoformat()}
    items.append(item)
    save_learned(items)
    return item


def delete_learned_fact(fact_id: str) -> None:
    save_learned([i for i in load_learned() if i.get("id") != fact_id])


def load_interpretations() -> list[dict]:
    """[{fact: fact id, shows: [skills or traits], why}], confirmed by you. Only the fit verdict uses them."""
    if not config.INTERPRETATIONS_PATH.exists():
        return []
    with open(config.INTERPRETATIONS_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return [{"fact": str(r["fact"]), "shows": [str(s).strip() for s in r.get("shows") or [] if str(s).strip()],
             "why": str(r.get("why") or "").strip()} for r in raw if isinstance(r, dict) and r.get("fact")]


def add_interpretation(fact_id: str, shows: list[str], why: str = "") -> None:
    items = load_interpretations()
    items.append({"fact": fact_id, "shows": shows, "why": why.strip(), "added": date.today().isoformat()})
    with open(config.INTERPRETATIONS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(items, f, sort_keys=False, allow_unicode=True)


def load_profile(path=None) -> dict:
    with open(path or config.PROFILE_PATH, encoding="utf-8") as f:
        return profile_from_raw(yaml.safe_load(f) or {})


def profile_from_raw(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("profile.yaml must be a mapping (name: value pairs) at the top level.")
    p = {
        # Empty YAML values (e.g. "linkedin:") are None; drop them so "None" never prints on the CV.
        "personal": {k: str(v) for k, v in (raw.get("personal") or {}).items() if v is not None},
        "experience": [],
        "projects": [],
        "education": [],
        "skills": {str(k): [str(s) for s in (v or [])] for k, v in (raw.get("skills") or {}).items()},
        "languages": [str(x) for x in (raw.get("languages") or [])],
        "activities": [str(x) for x in (raw.get("activities") or [])],
        "certifications": [str(x) for x in (raw.get("certifications") or [])],
        "general_facts": _norm_facts(raw.get("general_facts"), "general"),
    }
    for section, keys in ENTRY_SECTIONS.items():
        for entry in raw.get(section) or []:
            entry = {k: "" if v is None else v for k, v in dict(entry).items()}
            entry["id"] = str(entry.get("id") or slug(" ".join(str(entry.get(k, "")) for k in keys)))
            entry["facts"] = _norm_facts(entry.get("facts"), entry["id"])
            p[section].append(entry)

    _merge_learned(p)
    return p


def _merge_learned(p: dict) -> None:
    entries = {e["id"]: e for s in ENTRY_SECTIONS for e in p[s]}
    for item in load_learned():
        fact = {"id": str(item["id"]), "text": item["text"], "skills": list(item.get("skills") or []), "learned": True}
        target = entries.get(item.get("target"))
        (target["facts"] if target else p["general_facts"]).append(fact)
        known = {s.lower() for items in p["skills"].values() for s in items}
        for skill in fact["skills"]:
            if skill.lower() not in known:
                p["skills"].setdefault("Additional", []).append(skill)
                known.add(skill.lower())


def candidate_name(p: dict) -> str:
    return p["personal"].get("name") or "the candidate"


def graduation(p: dict) -> str:
    """When you graduate: personal.graduation if set, else the first education entry's dates."""
    if p["personal"].get("graduation"):
        return p["personal"]["graduation"]
    return str(p["education"][0].get("dates", "")) if p["education"] else ""


def entry_header(entry: dict, section: str) -> str:
    dates = entry.get("dates", "")
    if section == "experience":
        return f"{entry.get('title', '')} at {entry.get('company', '')}, {entry.get('location', '')} ({dates})"
    if section == "education":
        parts = [f"{entry.get('degree', '')}, {entry.get('institution', '')} ({dates})", entry.get("gpa", "")]
        if entry.get("coursework"):
            parts.append(f"Relevant coursework: {entry['coursework']}")
        return "; ".join(p for p in parts if p)
    return f"Project: {entry.get('name', '')} (built with {entry.get('stack', '')})"


def index_facts(p: dict) -> dict[str, dict]:
    """Every citeable fact: id -> {text, section, parent}."""
    idx: dict[str, dict] = {}

    def add(fid, text, section, parent=None, about=None):
        idx[fid] = {"id": fid, "text": text, "section": section, "parent": parent, "about": about}

    if p["personal"].get("location"):
        add("personal.location", f"Based in {p['personal']['location']}", "personal")
    if p["personal"].get("work_authorization"):
        add("personal.work_authorization", p["personal"]["work_authorization"], "personal")
    for section in ENTRY_SECTIONS:
        for e in p[section]:
            add(e["id"], entry_header(e, section), section, e["id"])
            for f in e["facts"]:
                text = f["text"] + (f" (skills: {', '.join(f['skills'])})" if f["skills"] else "")
                add(f["id"], text, section, e["id"])
    for f in p["general_facts"]:
        add(f["id"], f["text"], "general", about=f.get("about"))
    for cat, items in p["skills"].items():
        add(f"skill.{slug(cat)}", f"{cat}: {', '.join(items)}", "skills")
    for name, prefix in (("languages", "lang"), ("activities", "activity"), ("certifications", "cert")):
        for i, text in enumerate(p[name], 1):
            add(f"{prefix}.{i}", text, name)
    return idx


def skill_lookup(p: dict) -> dict[str, tuple[str, str]]:
    """lowercase skill -> (category, canonical spelling)."""
    return {s.lower(): (cat, s) for cat, items in p["skills"].items() for s in items}


def corpus(p: dict) -> str:
    """All profile text, for 'does this term appear anywhere in my profile' checks (has_term handles case)."""
    return "\n".join(f["text"] for f in index_facts(p).values())


def facts_for_prompt(p: dict) -> str:
    lines = []
    for section, title in (("experience", "EXPERIENCE"), ("projects", "PROJECTS"), ("education", "EDUCATION (locked, printed as-is)")):
        if not p[section]:
            continue
        lines.append(f"## {title}")
        for e in p[section]:
            cap = f"  [max {e['max_bullets']} bullets]" if e.get("max_bullets") and section != "education" else ""
            lines.append(f"[{e['id']}] {entry_header(e, section)}{cap}")
            for f in e["facts"]:
                skills = f"  (skills: {', '.join(f['skills'])})" if f["skills"] else ""
                lines.append(f"  - {f['id']}: {f['text']}{skills}")
    if p["general_facts"]:
        lines.append("## GENERAL FACTS (cover letter only)")
        lines += [f"  - {f['id']}: {f['text']}" + (f"  (only in a paragraph about [{f['about']}])" if f.get("about") else "")
                  for f in p["general_facts"]]
    lines.append("## SKILLS (the ONLY skills that may be listed; ids in brackets)")
    lines += [f"  - [skill.{slug(c)}] {c}: {' | '.join(items)}" for c, items in p["skills"].items()]
    for name, prefix in (("languages", "lang"), ("activities", "activity"), ("certifications", "cert")):
        if p[name]:
            lines.append(f"## {name.upper()}")
            lines += [f"  - {prefix}.{i}: {t}" for i, t in enumerate(p[name], 1)]
    personal = [f"  - personal.location: Based in {p['personal']['location']}"] if p["personal"].get("location") else []
    if p["personal"].get("work_authorization"):
        personal.append(f"  - personal.work_authorization: {p['personal']['work_authorization']}"
                        "  (only when the posting or question raises work authorization)")
    if personal:
        lines += ["## PERSONAL"] + personal
    return "\n".join(lines)


def validate_profile(p: dict) -> list[str]:
    problems = []
    ids = [fid for s in ENTRY_SECTIONS for e in p[s] for fid in [e["id"]] + [f["id"] for f in e["facts"]]]
    ids += [f["id"] for f in p["general_facts"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"Duplicate ids: {', '.join(dupes)}. Give these entries/facts an explicit unique 'id'.")
    if not p["personal"].get("name"):
        problems.append("personal.name is empty.")
    if not p["experience"]:
        problems.append("No experience entries.")
    for section in ("experience", "projects"):
        for e in p[section]:
            if not e["facts"]:
                problems.append(f"{section} '{e['id']}' has no facts, so the AI has nothing to write about.")
    entries = {e["id"] for s in ENTRY_SECTIONS for e in p[s]}
    for f in p["general_facts"]:
        if f.get("about") and f["about"] not in entries:
            problems.append(f"general fact '{f['id']}' is about '{f['about']}', but no job, project or school has that id.")
    if not p["skills"]:
        problems.append("No skills defined, so the skills section will be empty.")
    return problems
