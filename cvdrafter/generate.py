"""Step 2: write the tailored text, enforce structure in code, and loop with the fact checker."""
from __future__ import annotations

import copy
import json

from . import config, default_cv, memory, posting, prompts
from .llm import chat_json
from .profile import candidate_name, facts_for_prompt, graduation, load_guardrails, skill_lookup
from .textutil import has_term
from .verify import draft_text, errors, issue, run_checks


def _schema(limits: dict, profile: dict, letter_only: bool = False) -> str:
    return (prompts.SCHEMA_CL if letter_only else prompts.SCHEMA).format(
        graduation=graduation(profile) or "unknown",
        bullet_chars=limits["bullet_chars"],
        bpj_min=limits["bullets_per_job"]["min"],
        proj_min=limits["projects"]["min"],
        proj_max=limits["projects"]["max"],
        max_skill_categories=limits["max_skill_categories"],
        max_skills_per_category=limits["max_skills_per_category"],
        cl_min=limits["cl_paragraphs"]["min"],
        cl_max=limits["cl_paragraphs"]["max"],
        cl_words_min=limits["cl_words"]["min"],
        cl_words_max=limits["cl_words"]["max"],
    )


def _system(profile: dict) -> str:
    g = load_guardrails()
    return prompts.WRITER_SYSTEM.format(
        candidate=candidate_name(profile),
        claim_guards="\n".join(f"- {c}" for c in g["claim_guards"]) or "(none)",
        never_mention=", ".join(g["never_mention"]) or "(none)",
        banned_phrases=", ".join(f'"{b}"' for b in g["banned_phrases"]) or "(none)",
    )


def _analysis_for_prompt(analysis: dict) -> str:
    slim = {
        "role_type": analysis.get("role_type"),
        "summary": analysis.get("summary"),
        "seniority": analysis.get("seniority"),
        "keywords_you_can_truthfully_use": [k["term"] for k in analysis.get("keywords", []) if k.get("in_profile")],
        "keywords_NOT_in_profile_do_not_use": [k["term"] for k in analysis.get("keywords", []) if not k.get("in_profile")],
        "requirements": analysis.get("requirements"),
        "company_facts_from_posting": analysis.get("company_facts"),
        "eligibility": analysis.get("eligibility"),
        "fit_warning": analysis.get("fit_warning"),
    }
    return json.dumps(slim, ensure_ascii=False, indent=1)


def _examples_for_prompt(examples: list[dict]) -> str:
    if not examples:
        return ""
    out = ["APPROVED PAST APPLICATIONS (the candidate edited and sent these). Mirror their STYLE and tone. "
           "Their content is only usable where it matches the fact bank:"]
    for ex in examples:
        final = ex["final"]
        out.append(f"--- {ex['role']} at {ex['company']} (outcome: {ex['status']})")
        bullets = [b["text"] for bs in (final.get("experience") or {}).values() for b in bs][:4]
        out += [f"Bullet: {b}" for b in bullets]
        out += [f"Cover letter paragraph: {p['text']}" for p in (final.get("cover_letter") or [])]
    return "\n".join(out)


def _item(raw) -> dict:
    if not isinstance(raw, dict):
        return {"text": str(raw or "").strip(), "sources": []}
    sources = raw.get("sources") or []
    return {"text": str(raw.get("text") or "").strip(),
            "sources": [str(s) for s in (sources if isinstance(sources, list) else [sources])]}


def _list(value) -> list:
    """The model sometimes returns a single value where a list belongs."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _pad(bullets: list[dict], facts: list[dict], minimum: int) -> list[dict]:
    used = {s for b in bullets for s in b["sources"]}
    for f in facts:
        if len(bullets) >= minimum:
            break
        if f["id"] not in used:
            bullets.append({"text": f["text"], "sources": [f["id"]]})
    return bullets


def _strings(value) -> list[str]:
    return [str(v).strip() for v in _list(value) if str(v).strip()]


def post_process(raw: dict, profile: dict, limits: dict) -> tuple[dict, list[dict]]:
    """Force the model's output into the template's fixed structure. Locked fields never come from the model."""
    raw = _dict(raw)
    notes = []
    draft = {
        "warnings": _strings(raw.get("warnings")),
        "left_out": _strings(raw.get("left_out")),
        "check_before_sending": _strings(raw.get("check_before_sending")),
    }

    bpj = limits["bullets_per_job"]
    raw_exp = _dict(raw.get("experience"))
    draft["experience"] = {}
    for job in profile["experience"]:
        cap = int(job.get("max_bullets") or bpj["max"])
        bullets = [b for b in (_item(x) for x in _list(raw_exp.get(job["id"]))) if b["text"]][:cap]
        minimum = min(bpj["min"], cap, len(job["facts"]))
        if len(bullets) < minimum:
            bullets = _pad(bullets, job["facts"], minimum)
            notes.append(issue("warning", f"experience/{job['id']}", "Too few bullets from the model. Filled with your original facts verbatim."))
        draft["experience"][job["id"]] = bullets

    projects = {p["id"]: p for p in profile["projects"]}
    chosen = []
    for proj in _list(raw.get("projects")):
        proj = _dict(proj)
        pid = proj.get("id")
        if pid in projects and pid not in [c["id"] for c in chosen] and len(chosen) < limits["projects"]["max"]:
            cap = int(projects[pid].get("max_bullets") or limits["bullets_per_project"])
            bullets = [b for b in (_item(x) for x in _list(proj.get("bullets"))) if b["text"]][:cap]
            chosen.append({"id": pid, "bullets": bullets or _pad([], projects[pid]["facts"], 1)})
    for pid, proj in projects.items():
        if len(chosen) >= min(limits["projects"]["min"], len(projects)):
            break
        if pid not in [c["id"] for c in chosen]:
            cap = int(proj.get("max_bullets") or limits["bullets_per_project"])
            chosen.append({"id": pid, "bullets": _pad([], proj["facts"], cap)})
            notes.append(issue("warning", f"projects/{pid}", "Model chose too few projects. Added this one with your original bullets."))
    draft["projects"] = chosen

    lookup = skill_lookup(profile)
    skills, dropped = [], []
    for group in _list(raw.get("skills")):
        if len(skills) >= limits["max_skill_categories"]:
            break
        group = _dict(group)
        items = []
        for s in _list(group.get("items")):
            hit = lookup.get(str(s).lower())
            if hit and hit[1] not in items:
                items.append(hit[1])
            elif not hit:
                dropped.append(str(s))
        cat = next((c for c in profile["skills"] if c.lower() == str(group.get("category", "")).lower()), None)
        if cat is None and items:
            cat = lookup[items[0].lower()][0]
        if cat and items and cat not in [g["category"] for g in skills]:
            skills.append({"category": cat, "items": items[: limits["max_skills_per_category"]]})
    if dropped:
        notes.append(issue("warning", "skills", f"Removed skills that aren't in your profile: {', '.join(dropped)}"))
    if not skills:
        skills = [{"category": c, "items": items[: limits["max_skills_per_category"]]}
                  for c, items in list(profile["skills"].items())[: limits["max_skill_categories"]]]
    draft["skills"] = skills

    cl = [_item(p) for p in _list(raw.get("cover_letter"))]
    draft["cover_letter"] = [p for p in cl if p["text"]][: limits["cl_paragraphs"]["max"]]
    return draft, notes


def _with_cv(raw: dict, base: dict | None) -> dict:
    """The writer's reply, with the default CV in place of the CV slots when it's being reused."""
    raw = _dict(raw)
    return {**raw, **{k: base[k] for k in ("experience", "projects", "skills")}} if base else raw


CV_SLOTS = ("experience", "projects", "skills")


def _prompts(ctx: dict, base: dict | None, limits: dict) -> tuple[str, str, int]:
    """The writer's system and user prompts for this posting. base: the fixed CV when only the letter is written."""
    profile, analysis = ctx["profile"], ctx["analysis"]
    style_rules = config.STYLE_RULES_PATH.read_text(encoding="utf-8") if config.STYLE_RULES_PATH.exists() else ""
    system = (_system(profile) + prompts.FACT_BANK.format(facts=facts_for_prompt(profile))
              + prompts.STYLE_RULES.format(style_rules=style_rules or "(none yet)"))
    sample = config.voice_sample()
    if sample:
        system += prompts.VOICE_SAMPLE.format(sample=sample)
    examples = memory.similar_examples([k["term"] for k in analysis.get("keywords", [])], exclude_id=ctx.get("app_id"))
    user = prompts.WRITER_USER.format(
        company=ctx["company"], role=ctx["role"], jd=posting.core(ctx["jd"]), notes=ctx.get("notes") or "(none)",
        analysis=_analysis_for_prompt(analysis),
        examples=_examples_for_prompt(examples),
        extra=ctx.get("extra") or "(none)",
        schema=_schema(limits, profile, letter_only=base is not None),
    )
    return system, user, len(examples)


def _fix(draft: dict, problems: list[str], system: str, user: str, base: dict | None, ctx: dict, limits: dict,
         writer_model: str, task: str) -> tuple[dict, list[dict]]:
    """One rewrite of the draft that addresses the listed problems."""
    text = draft_text(draft)
    keep = [k["term"] for k in ctx["analysis"].get("keywords", []) if k.get("in_profile") and has_term(k["term"], text)]
    shown = {k: v for k, v in draft.items() if k not in CV_SLOTS} if base else draft
    fix = prompts.FIX_USER.format(draft=json.dumps(shown, ensure_ascii=False, indent=1), problems="\n".join(problems),
                                  keywords=", ".join(keep) or "(none)")
    raw = chat_json(system, user + "\n\n" + fix, writer_model, temperature=0.2, effort="medium", task=task)
    return post_process(_with_cv(raw, base), ctx["profile"], limits)


def _check_and_fix(draft: dict, notes: list[dict], system: str, user: str, base: dict | None, ctx: dict, limits: dict,
                   writer_model: str, checker_model: str | None, progress) -> tuple[dict, list[dict], int]:
    """Fact-check, and let the writer fix the errors, up to MAX_FIX_ROUNDS times."""
    rounds = 0
    while True:
        progress(f"Fact-checking (round {rounds + 1})…")
        issues = run_checks(draft, ctx["profile"], ctx["analysis"], ctx["jd"], ctx.get("notes", ""), limits, checker_model)
        # With a fixed CV the writer can only change the letter, so CV errors aren't sent back to it.
        fixable = [i for i in errors(issues) if not (base and str(i["location"]).startswith(CV_SLOTS))]
        if not fixable or rounds >= config.MAX_FIX_ROUNDS:
            return draft, notes + issues, rounds
        rounds += 1
        progress(f"Fixing {len(fixable)} problem(s), round {rounds}…")
        draft, notes = _fix(draft, [f"- [{i['location']}] {i['message']}" for i in fixable], system, user, base, ctx,
                            limits, writer_model, f"fix round {rounds}")


def draft_application(ctx: dict, writer_model: str, checker_model: str | None, progress=lambda msg: None,
                      tailor_cv: bool | None = None) -> dict:
    """ctx: company, role, jd, notes, extra, analysis, profile, app_id.
    tailor_cv: None decides from the posting (default_cv.decide); False reuses the default CV and only writes the
    cover letter; True rewrites the CV too."""
    limits = config.load_slots()["limits"]
    profile, analysis = ctx["profile"], ctx["analysis"]
    choice = default_cv.decide(analysis, profile)
    if tailor_cv is not None:
        choice = {**choice, "tailor": tailor_cv, "reason": "you asked for it"}
    base = None if choice["tailor"] else default_cv.load(profile)
    if base is None:
        choice["tailor"] = True
    system, user, n_examples = _prompts(ctx, base, limits)

    if base:
        progress(f"Using your default CV ({choice['reason']}). Writing the cover letter…")
    else:
        progress(f"Tailoring the CV ({choice['reason']}) and writing the cover letter…")
    task = "write CV + letter" if base is None else "write letter (default CV)"
    raw = chat_json(system, user, writer_model, temperature=0.4, effort="medium", task=task)
    draft, pp_notes = post_process(_with_cv(raw, base), profile, limits)
    draft, issues, rounds = _check_and_fix(draft, pp_notes, system, user, base, ctx, limits, writer_model, checker_model, progress)
    return {"draft": draft, "issues": issues, "fix_rounds": rounds, "examples_used": n_examples,
            "tailored_cv": base is None, "cv_reason": choice["reason"]}


def rewrite_letter(ctx: dict, draft: dict, writer_model: str, checker_model: str | None, problems: list[dict],
                   instruction: str = "", progress=lambda msg: None) -> dict:
    """Rewrite only the cover letter of an existing draft: fix `problems` (issues from a check) and follow your
    instruction. The CV stays exactly as it is. ctx: as for draft_application."""
    limits = config.load_slots()["limits"]
    base = {k: draft[k] for k in CV_SLOTS}
    instruction = instruction.strip()
    system, user, n_examples = _prompts({**ctx, "extra": instruction or ctx.get("extra")}, base, limits)
    # The CV is fixed here, and a guessed company name isn't the letter's to fix.
    asks = [f"- [{i['location']}] {i['message']}" for i in problems if not str(i["location"]).startswith((*CV_SLOTS, "company"))]
    if instruction:
        asks.append(f"- [cover_letter] The candidate asks for this change: {instruction}")
    if not asks:
        asks.append("- [cover_letter] Rewrite the cover letter: keep every fact, make it read better.")
    progress("Rewriting the cover letter…")
    new, notes = _fix(draft, asks, system, user, base, ctx, limits, writer_model, "rewrite letter")
    new, issues, rounds = _check_and_fix(new, notes, system, user, base, ctx, limits, writer_model, checker_model, progress)
    return {"draft": new, "issues": issues, "fix_rounds": rounds, "examples_used": n_examples}


def apply_edits(draft: dict, edits: dict[str, str], excluded: set[str] | frozenset = frozenset()) -> dict:
    """edits: location -> new text (locations as produced by verify.iter_items). excluded: locations to drop."""
    out = _apply_text(draft, edits)
    if excluded:
        out["experience"] = {jid: [b for i, b in enumerate(bs, 1) if f"experience/{jid}/{i}" not in excluded]
                             for jid, bs in out["experience"].items()}
        for proj in out["projects"]:
            proj["bullets"] = [b for i, b in enumerate(proj["bullets"], 1) if f"projects/{proj['id']}/{i}" not in excluded]
        out["projects"] = [p for p in out["projects"] if p["bullets"]]
        out["cover_letter"] = [p for i, p in enumerate(out["cover_letter"], 1) if f"cover_letter/{i}" not in excluded]
    return out


def _apply_text(draft: dict, edits: dict[str, str]) -> dict:
    out = copy.deepcopy(draft)
    for job_id, bullets in out["experience"].items():
        for i, b in enumerate(bullets, 1):
            b["text"] = edits.get(f"experience/{job_id}/{i}", b["text"])
    for proj in out["projects"]:
        for i, b in enumerate(proj["bullets"], 1):
            b["text"] = edits.get(f"projects/{proj['id']}/{i}", b["text"])
    for i, p in enumerate(out["cover_letter"], 1):
        p["text"] = edits.get(f"cover_letter/{i}", p["text"])
    return out
