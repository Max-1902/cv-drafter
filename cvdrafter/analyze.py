"""Step 1: understand the job and map its requirements onto the fact bank."""
from __future__ import annotations

from . import company as co
from . import fit, posting, prompts
from .llm import chat_json
from .memory import JOB_FIELDS
from .profile import corpus, facts_for_prompt, index_facts, load_interpretations
from .textutil import dedupe, has_term


def analyze_job(jd: str, company: str, role: str, profile: dict, model: str, url: str = "") -> dict:
    jd = posting.clean(jd)
    rules = load_interpretations()
    system = prompts.ANALYST_SYSTEM + prompts.FACT_BANK.format(facts=facts_for_prompt(profile))
    if rules:
        system += prompts.INTERPRETATIONS.format(rules="\n".join(
            f"  - {r['fact']} shows: {', '.join(r['shows'])}" + (f" ({r['why']})" if r["why"] else "") for r in rules))
    data = chat_json(
        system,
        prompts.ANALYST_USER.format(company=company or "?", role=role or "?", url=url or "?", jd=jd),
        model,
        temperature=0.1,
        task="analyse posting",
    )

    facts = index_facts(profile)
    requirements = []
    for r in data.get("requirements") or []:
        ids = [i for i in r.get("fact_ids") or [] if i in facts]
        status = r.get("status", "missing") if ids else "missing"
        requirements.append({
            "text": r.get("text", ""),
            "importance": r.get("importance", "nice"),
            "kind": r.get("kind") if r.get("kind") in fit.KINDS else "skill",
            "status": status,
            "evidence": "transferable" if ids and r.get("evidence") == "transferable" else "direct",
            "fact_ids": ids,
            "comment": r.get("comment", ""),
        })
    data["requirements"] = fit.apply_interpretations(requirements, rules, facts)

    keywords = []
    for k in data.get("keywords") or []:
        if isinstance(k, str):
            k = {"term": k, "type": "hard"}
        if k.get("term"):
            weight = k.get("weight") if k.get("weight") in ("required", "preferred", "mentioned") else "required"
            keywords.append({"term": k["term"].strip(), "type": k.get("type", "hard"), "weight": weight})
    keywords = dedupe_keywords(keywords)
    text = corpus(profile)
    for k in keywords:
        k["in_profile"] = has_term(k["term"], text)
    data["keywords"] = keywords

    data["eligibility"] = data.get("eligibility") or {}
    data["company_facts"] = dedupe(data.get("company_facts"))
    found = co.resolve(str(data.get("company") or ""), company, jd, url, str(data.get("company_evidence") or ""))
    data["company"] = found["company"]
    data["company_check"] = found | {"via_agency": bool(data.get("via_agency"))}
    data["role"] = data.get("role") or role
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    data["job"] = {k: str(job.get(k) or "").strip() for k in JOB_FIELDS}
    return data


def dedupe_keywords(keywords: list[dict]) -> list[dict]:
    seen, out = set(), []
    for k in keywords:
        key = k["term"].lower()
        if key not in seen:
            seen.add(key)
            out.append(k)
    return out


def absent_terms(analysis: dict) -> list[dict]:
    """JD keywords that appear nowhere in the profile — they must not show up in the CV."""
    return [k for k in analysis.get("keywords", []) if not k.get("in_profile")]


def coverage_stats(analysis: dict) -> dict:
    reqs = analysis.get("requirements", [])
    must = [r for r in reqs if r["importance"] == "must"]
    return {
        "covered": sum(r["status"] == "covered" for r in reqs),
        "partial": sum(r["status"] == "partial" for r in reqs),
        "missing": sum(r["status"] == "missing" for r in reqs),
        "must_total": len(must),
        "must_covered": sum(r["status"] == "covered" for r in must),
    }
