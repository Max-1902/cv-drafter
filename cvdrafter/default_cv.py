"""Your default CV (data/default_cv.yaml), and whether a posting needs a tailored one instead.

Rewriting the CV for every posting costs ~25-50k tokens. Most postings take the default as it is: the writer then
only writes the cover letter. The decision uses the posting analysis that's already done, so it costs nothing.
"""
from __future__ import annotations

import yaml

from . import config
from .textutil import has_term, plain

# The default CV is tailored when it misses at least this many hard skills the posting asks for (required or
# preferred) that you do have: an ATS keyword filter could reject it.
TAILOR_KEYWORD_GAP = 2


def load(profile: dict) -> dict | None:
    """The default CV as a draft (no cover letter yet), forced into the template's structure like any draft."""
    from .generate import post_process  # generate imports verify, which imports this module

    if not config.DEFAULT_CV_PATH.exists():
        return None
    raw = yaml.safe_load(config.DEFAULT_CV_PATH.read_text(encoding="utf-8")) or {}
    draft, _ = post_process(raw, profile, config.load_slots()["limits"])
    return draft


def texts() -> set[str]:
    """Every bullet of the default CV, as plain text. They were checked against your facts when it was set up."""
    if not config.DEFAULT_CV_PATH.exists():
        return set()
    raw = yaml.safe_load(config.DEFAULT_CV_PATH.read_text(encoding="utf-8")) or {}
    bullets = [b for bs in (raw.get("experience") or {}).values() for b in bs or []]
    bullets += [b for p in raw.get("projects") or [] for b in p.get("bullets") or []]
    return {plain(b.get("text", "")).strip() for b in bullets if isinstance(b, dict)}


def _cv_text(draft: dict, profile: dict) -> str:
    """All the text an ATS reads on the CV: the bullets and skills, plus the locked education lines."""
    from .verify import draft_text  # verify imports this module

    locked = [" ".join(str(e.get(k) or "") for k in ("institution", "degree", "coursework"))
              + " " + " ".join(f["text"] if isinstance(f, dict) else str(f) for f in e.get("facts") or [])
              for e in profile["education"]]
    locked += [f"{job['company']} {job['title']}" for job in profile["experience"]]
    return draft_text(draft) + " " + " ".join(locked)


def decide(analysis: dict, profile: dict) -> dict:
    """{"tailor": bool, "missing": [posting skills you have that the default CV doesn't show], "reason": str}."""
    default = load(profile)
    if default is None:
        return {"tailor": True, "missing": [], "reason": "no default CV set up"}
    text = _cv_text(default, profile)
    missing = [k["term"] for k in (analysis or {}).get("keywords", [])
               if k.get("in_profile") and k.get("type") == "hard" and k.get("weight", "required") in ("required", "preferred")
               and not has_term(k["term"], text)]
    if len(missing) >= TAILOR_KEYWORD_GAP:
        return {"tailor": True, "missing": missing, "reason": f"the default CV doesn't show {', '.join(missing)}"}
    return {"tailor": False, "missing": missing,
            "reason": "the default CV already shows " + ("everything except " + missing[0] if missing else "every skill the posting asks for that you have")}
