"""Are you a good fit for this posting? A fixed, explainable score on top of the analysis (no AI call).

The analysis maps each requirement to your facts (covered / partial / missing) and says whether the evidence is
direct or a fair interpretation (varsity sport -> discipline). This turns that into a verdict:
- skills, background and traits are scored: must-haves count 3x, nice-to-haves 1x, traits half (easy to claim);
- direct evidence earns full credit, interpreted evidence 80%, partial 50% (40% if interpreted), missing nothing;
- logistics (location, graduation window, dates, work authorization) are pass/fail: a missed must-have blocks;
- a mid or senior role blocks too;
- Strong fit needs every must-have met; one missing skill or background must-have caps it at Good, two at Stretch.
Interpretations you confirmed (data/interpretations.yaml) always win: a requirement that names one counts as met.
Saved analyses from before kind/evidence existed count every requirement as a direct-evidence skill.
"""
from __future__ import annotations

from .profile import index_facts, load_interpretations
from .textutil import has_term

KINDS = ("skill", "background", "trait", "logistics")
IMPORTANCE = {"must": 3.0, "nice": 1.0}
KIND_WEIGHT = {"skill": 1.0, "background": 1.0, "trait": 0.5}
CREDIT = {("covered", "direct"): 1.0, ("covered", "transferable"): 0.8,
          ("partial", "direct"): 0.5, ("partial", "transferable"): 0.4}
TOO_SENIOR = ("mid", "senior")
# verdict, lowest score, most core must-haves (skill/background) that may be missing
LEVELS = (("strong", 80, 0), ("good", 60, 1), ("stretch", 40, 2))
LABELS = {"strong": "Strong fit", "good": "Good fit", "stretch": "Stretch", "no": "Not a fit", "unclear": "Unclear"}


def _kind(r: dict) -> str:
    return r.get("kind") if r.get("kind") in KINDS else "skill"


def _weight(r: dict) -> float:
    return IMPORTANCE.get(r.get("importance"), 1.0) * KIND_WEIGHT[_kind(r)]


def _credit(r: dict) -> float:
    return CREDIT.get((r.get("status"), r.get("evidence") or "direct"), 0.0)


def _why(r: dict) -> str:
    return f"{r['text']}: {r['comment']}" if r.get("comment") else r["text"]


def apply_interpretations(requirements: list[dict], rules: list[dict], facts: dict[str, dict]) -> list[dict]:
    """Requirements with your confirmed interpretations applied: one that names a skill or trait you said a fact
    shows becomes covered, cites that fact and says why. Logistics and rules about facts that no longer exist are
    left alone."""
    out = []
    for r in requirements:
        hits = [(rule, term) for rule in rules if rule["fact"] in facts and r.get("status") != "covered"
                and _kind(r) != "logistics" for term in rule["shows"] if has_term(term, r.get("text", ""))]
        if hits:
            ids = list(dict.fromkeys([*r.get("fact_ids", []), *(rule["fact"] for rule, _ in hits)]))
            why = "; ".join(f"you confirmed {facts[rule['fact']]['text']} shows {term}" + (f" ({rule['why']})" if rule["why"] else "")
                            for rule, term in hits)
            r = {**r, "status": "covered", "evidence": "transferable", "fact_ids": ids, "comment": why[:1].upper() + why[1:],
                 "confirmed": True}
        out.append(r)
    return out


def assess(analysis: dict, profile: dict | None = None, rules: list[dict] | None = None) -> dict:
    """{"level", "verdict", "score" (0-100 or None), "summary", "strengths", "gaps", "blockers", "risks"}.
    With your profile, your confirmed interpretations are applied first (rules: defaults to the saved ones)."""
    reqs = [r for r in analysis.get("requirements") or [] if r.get("text")]
    if profile:
        reqs = apply_interpretations(reqs, load_interpretations() if rules is None else rules, index_facts(profile))
    scored = [r for r in reqs if _kind(r) != "logistics"]
    total = sum(_weight(r) for r in scored)
    score = round(100 * sum(_weight(r) * _credit(r) for r in scored) / total) if total else None

    blockers, risks = [], []
    seniority = str(analysis.get("seniority") or "").lower()
    if seniority in TOO_SENIOR:
        blockers.append(f"The role is {seniority}-level; you'd be applying as a student.")
    elif seniority in ("new grad", "junior"):
        risks.append(f"The role is for {seniority} candidates; check it takes someone at your graduation date.")
    for r in reqs:
        if _kind(r) != "logistics" or r.get("status") == "covered":
            continue
        if r.get("importance") == "must" and r.get("status") == "missing":
            blockers.append(_why(r))
        else:
            risks.append(_why(r))
    elig = analysis.get("eligibility") or {}
    if elig.get("excludes_visa_holders"):
        quote = elig.get("work_authorization") or "the posting excludes visa holders"
        needs = bool(((profile or {}).get("personal") or {}).get("needs_sponsorship"))
        (blockers if needs else risks).append(f"Work authorization: {quote}")
    if analysis.get("fit_warning"):
        risks.append(str(analysis["fit_warning"]))

    must = [r for r in scored if r.get("importance") == "must"]
    met = [r for r in must if r.get("status") == "covered"]
    missing_core = [r for r in must if r.get("status") == "missing" and _kind(r) != "trait"]
    all_met = all(r.get("status") != "missing" for r in must)
    strengths = [_why(r) if r.get("evidence") == "transferable" else r["text"] for r in met]
    gaps = [_why(r) for r in sorted(scored, key=lambda r: (r.get("importance") != "must", _credit(r)))
            if r.get("status") != "covered"]

    if blockers:
        level = "no"
    elif score is None:
        level = "unclear"
    else:
        level = next((lvl for lvl, low, miss in LEVELS
                      if score >= low and len(missing_core) <= miss and (lvl != "strong" or all_met)), "no")
    parts = [LABELS[level]]
    if score is not None:
        parts.append(f"{score}/100")
    if must:
        interpreted = sum(r.get("evidence") == "transferable" for r in met)
        parts.append(f"{len(met)}/{len(must)} must-haves met" + (f", {interpreted} by interpretation" if interpreted else ""))
    if missing_core:
        parts.append("missing " + ", ".join(r["text"] for r in missing_core[:3]))
    if blockers:
        parts.append(f"{len(blockers)} blocker(s)")
    return {"level": level, "verdict": LABELS[level], "score": score, "summary": " · ".join(parts),
            "strengths": strengths, "gaps": gaps, "blockers": blockers, "risks": risks}
