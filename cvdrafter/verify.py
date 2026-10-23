"""Hallucination guard: deterministic checks first, then an optional AI fact-check pass."""
from __future__ import annotations

import re
from datetime import datetime

from . import default_cv, posting, prompts, voice
from .analyze import absent_terms
from .llm import LLMError, chat_json
from .profile import graduation, index_facts, load_guardrails
from .textutil import has_term, plain, unsupported_numbers, word_count

CL_EXTRA_SOURCES = {"jd", "notes"}
PLACEHOLDER_RE = re.compile(r"\[[^\]]{3,}\]")
COURSE_TALK = re.compile(r"\b(?:courses?|coursework|class(?:es)?)\b", re.I)
COURSE_LEVEL = re.compile(r"\s+[IVX]+(?:\s*(?:[–-]|and|&)\s*[IVX]+)?$")  # "Data Structures I–II" -> "Data Structures"
NEAR_DONE = re.compile(r"\b(?:finishing|wrapping up|about to (?:finish|complete))\b[^.]{0,40}?\b(?:MS|M\.S\.|BS|B\.S\.|BA|MBA|PhD|"
                       r"master'?s|bachelor'?s|degree|studies|program)\b|\bfinal (?:year|semester|term|quarter)\b|"
                       r"\babout to graduate\b", re.I)
NEAR_DONE_MONTHS = 6


def issue(level: str, location: str, message: str) -> dict:
    return {"level": level, "location": location, "message": message}


def iter_items(draft: dict):
    """Yield (location, kind, item, parent_id) for every free-text slot."""
    for job_id, bullets in (draft.get("experience") or {}).items():
        for i, b in enumerate(bullets, 1):
            yield f"experience/{job_id}/{i}", "bullet", b, job_id
    for proj in draft.get("projects") or []:
        for i, b in enumerate(proj.get("bullets") or [], 1):
            yield f"projects/{proj['id']}/{i}", "bullet", b, proj["id"]
    for i, para in enumerate(draft.get("cover_letter") or [], 1):
        yield f"cover_letter/{i}", "cl", para, None


def course_names(profile: dict) -> list[str]:
    names = [COURSE_LEVEL.sub("", c.strip()) for e in profile["education"]
             for c in re.split(r"[,;]", str(e.get("coursework", "")))]
    return [n for n in names if n]


def months_to_graduation(profile: dict, today: datetime | None = None) -> int | None:
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b", graduation(profile))
    if not m:
        return None
    try:
        grad = datetime.strptime(f"{m[1]} {m[2]}", "%b %Y")
    except ValueError:
        return None
    today = today or datetime.now()
    return (grad.year - today.year) * 12 + grad.month - today.month


def check_cl_paragraph(text: str, sources: list[str], facts: dict, profile: dict) -> list[str]:
    """Letter problems where every fact is true but the way they're put together isn't."""
    out = []
    cited = {facts[s]["parent"] for s in sources if s in facts and facts[s]["section"] in ("experience", "projects")}
    projects = sorted(e for e in cited if facts[e]["section"] == "projects")
    if len(projects) > 1:
        out.append(f"Mixes facts from different projects ({', '.join(projects)}) in one paragraph, so the reader can't "
                   "tell which work each claim belongs to. Tell one project per paragraph.")
    for s in sources:
        about = facts.get(s, {}).get("about")
        if about and (about not in cited or cited - {about}):
            here = ", ".join(sorted(cited - {about})) or "no job or project"
            out.append(f"Uses {s}, which comes from {about}, in a paragraph about {here}. It may only close a "
                       f"paragraph about {about}; otherwise leave it out.")
    if COURSE_TALK.search(text):
        named = [c for c in course_names(profile) if has_term(c, text)]
        if named:
            out.append(f"Lists coursework ({', '.join(named)}). The CV already shows your courses; leave them out "
                       "of the letter.")
    months = months_to_graduation(profile)
    if months is not None and months > NEAR_DONE_MONTHS and (m := NEAR_DONE.search(text)):
        out.append(f"\"{m[0]}\" overstates where you are: graduation is {months} months away "
                   f"({graduation(profile)}). Say you're in the program and when you graduate.")
    return out


def check_deterministic(draft: dict, profile: dict, analysis: dict, jd: str, notes: str, limits: dict,
                        final: bool = False) -> list[dict]:
    """final=True when about to render: placeholders then block. While drafting they're only warnings, or the fix
    rounds would get the writer to invent the missing sentence."""
    facts = index_facts(profile)
    guards = load_guardrails()
    issues = []
    max_len = {"bullet": limits["bullet_chars"], "cl": limits["cl_paragraph_chars"]}
    missing_terms = absent_terms(analysis)
    trusted = default_cv.texts()

    for loc, kind, item, parent in iter_items(draft):
        raw, sources = item.get("text", "").strip(), item.get("sources") or []
        text = plain(raw)
        if not text:
            issues.append(issue("error", loc, "Empty text."))
            continue
        if len(text) > max_len[kind] and text.strip() not in trusted:  # the default CV's lines are proven to fit
            issues.append(issue("error", loc, f"Too long for the layout: {len(text)} > {max_len[kind]} characters."))
        if raw.count("**") % 2:
            issues.append(issue("error", loc, "Unbalanced **bold** markers."))
        if kind == "cl" and "**" in raw:
            issues.append(issue("warning", loc, "Bold markup in the cover letter will print as plain text."))
        if not sources:
            issues.append(issue("error", loc, "No sources cited. Every statement must trace back to your fact bank."))

        allowed_extra = CL_EXTRA_SOURCES if kind == "cl" else set()
        unknown = [s for s in sources if s not in facts and s not in allowed_extra]
        if unknown:
            issues.append(issue("error", loc, f"Cites ids that don't exist: {', '.join(unknown)}"))
        if parent:
            foreign = [s for s in sources if s in facts and facts[s]["parent"] != parent]
            if foreign:
                issues.append(issue("error", loc, f"Uses facts from a different job/project: {', '.join(foreign)}"))

        allowed_text = " ".join(facts[s]["text"] for s in sources if s in facts)
        if kind == "cl" and "notes" in sources:
            allowed_text += f" {notes}"
        bad_numbers = unsupported_numbers(text, allowed_text)
        not_in_jd = unsupported_numbers(text, jd) if kind == "cl" and "jd" in sources else bad_numbers
        from_jd = [n for n in bad_numbers if n not in not_in_jd]
        invented = [n for n in bad_numbers if n in not_in_jd]
        if invented:
            issues.append(issue("error", loc, f"Numbers not found in the cited facts: {', '.join(invented)}"))
        if from_jd:  # the posting's numbers can describe the company, never you
            issues.append(issue("warning", loc, f"Numbers taken from the posting: {', '.join(from_jd)}. "
                                                "Make sure they describe the company, not your experience."))

        for term in guards["never_mention"]:
            if has_term(term, text):
                issues.append(issue("error", loc, f"Contains \"{term}\", which is on your never-mention list."))
        about = {parent} if parent else {facts[s]["parent"] for s in sources if s in facts}
        for entry in about:
            for term in guards["never_mention_for"].get(entry, []):
                if has_term(term, text):
                    issues.append(issue("error", loc, f"Contains \"{term}\", which must never be said about {entry}."))
        if kind == "cl":
            for term in guards["banned_phrases"]:
                if has_term(term, text):
                    issues.append(issue("error", loc, f"Banned phrase: \"{term}\"."))
            skip = tuple(b.lower() for b in guards["banned_phrases"])
            for msg in voice.paragraph_tells(text, first=loc == "cover_letter/1", skip=skip):
                issues.append(issue("error", loc, f"Sounds like AI. {msg}"))
            for msg in check_cl_paragraph(text, sources, facts, profile):
                issues.append(issue("error", loc, msg))

        for ph in PLACEHOLDER_RE.findall(text):
            issues.append(issue("error" if final else "warning", loc, f"Placeholder to fill before sending: {ph}"))

        for k in missing_terms:
            if has_term(k["term"], text):
                hard_in_cv = k["type"] == "hard" and kind != "cl"
                issues.append(issue(
                    "error" if hard_in_cv else "warning", loc,
                    f"Mentions \"{k['term']}\" (from the posting) but it appears nowhere in your profile.",
                ))

    paragraphs = draft.get("cover_letter") or []
    if paragraphs:
        words = sum(word_count(p.get("text", "")) for p in paragraphs)
        lo, hi = limits["cl_words"]["min"], limits["cl_words"]["max"]
        if words < lo - 20 or words > hi + 20:
            issues.append(issue("error", "cover_letter", f"{words} words; target is {lo}–{hi}."))
        elif not lo <= words <= hi:
            issues.append(issue("warning", "cover_letter", f"{words} words; target is {lo}–{hi}."))
        for msg in voice.letter_tells([p.get("text", "") for p in paragraphs]):
            issues.append(issue("warning", "cover_letter", f"Sounds like AI. {msg}"))
        n, cl = len(paragraphs), limits["cl_paragraphs"]
        if not cl["min"] <= n <= cl["max"]:
            issues.append(issue("warning", "cover_letter", f"{n} paragraphs; target is {cl['min']}–{cl['max']}."))

    unused = unused_keywords(draft, analysis)
    if unused:
        issues.append(issue("warning", "keywords", f"Required posting keywords you have but this draft doesn't use: {', '.join(unused)}"))
    return issues


def draft_text(draft: dict) -> str:
    return " ".join([plain(item.get("text", "")) for _, _, item, _ in iter_items(draft)]
                    + [s for g in draft.get("skills") or [] for s in g.get("items") or []])


def unused_keywords(draft: dict, analysis: dict) -> list[str]:
    text = draft_text(draft)
    return [k["term"] for k in analysis.get("keywords", [])
            if k.get("in_profile") and k.get("type") == "hard" and k.get("weight", "required") == "required"
            and not has_term(k["term"], text)]


def check_with_llm(draft: dict, profile: dict, jd: str, notes: str, model: str) -> list[dict]:
    facts = index_facts(profile)
    blocks = []
    trusted = default_cv.texts()
    for loc, _, item, _ in iter_items(draft):
        if plain(item.get("text", "")).strip() in trusted:  # word for word from the default CV, checked when it was set up
            continue
        src_lines, shown = [], set()
        for s in item.get("sources") or []:
            if s in facts:
                # The job or project header the fact sits under: its name ("Macroeconomic Forecasting System") is
                # printed on the CV, so text may use it.
                parent = facts[s]["parent"]
                if parent and parent != s and parent in facts and parent not in shown:
                    shown.add(parent)
                    src_lines.append(f"    [{parent}] {facts[parent]['text']}")
                src_lines.append(f"    [{s}] {facts[s]['text']}")
            elif s == "jd":
                src_lines.append("    [jd] (the job posting, shown below)")
            elif s == "notes" and notes:
                src_lines.append(f"    [notes] {notes}")
        blocks.append(f"LOCATION: {loc}\n  TEXT: {plain(item.get('text', ''))}\n  SOURCES:\n" + ("\n".join(src_lines) or "    (none)"))
    if not blocks:
        return []
    items = "\n\n".join(blocks) + f"\n\nJOB POSTING [jd]:\n\"\"\"\n{jd}\n\"\"\""
    guards = "\n".join(f"- {g}" for g in load_guardrails()["claim_guards"]) or "(none)"
    data = chat_json(prompts.CHECKER_SYSTEM.format(claim_guards=guards), prompts.CHECKER_USER.format(items=items), model, temperature=0,
                     task="fact-check")
    out = []
    for i in data.get("issues") or []:
        level = "warning" if i.get("severity") == "warning" else "error"
        out.append(issue(level, i.get("location", "?"), f"AI check, \"{i.get('claim', '')}\": {i.get('problem', '')}"))
    return out


def run_checks(draft, profile, analysis, jd, notes, limits, checker_model=None) -> list[dict]:
    jd = posting.core(jd)  # what the writer saw
    issues = check_deterministic(draft, profile, analysis, jd, notes, limits)
    if checker_model:
        issues += check_with_llm(draft, profile, jd, notes, checker_model)
    return issues


def errors(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["level"] == "error"]


def evaluate(draft, profile, analysis, jd, notes, limits, checker_model=None) -> dict:
    """Check the exact text about to be rendered. status: verified | warnings | unverified | errors."""
    jd = posting.core(jd)
    issues = check_deterministic(draft, profile, analysis or {}, jd, notes or "", limits, final=True)
    ai_checked = False
    if checker_model:
        try:
            issues += check_with_llm(draft, profile, jd, notes or "", checker_model)
            ai_checked = True
        except LLMError as e:
            issues.append(issue("error", "fact-check", f"The AI fact-check could not run: {e}. Try again, or render anyway "
                                                     "once you've checked every statement yourself."))
    n_err, n_warn = len(errors(issues)), sum(i["level"] == "warning" for i in issues)
    status = "errors" if n_err else "unverified" if not ai_checked else "warnings" if n_warn else "verified"
    return {"status": status, "ai_checked": ai_checked, "checker": checker_model or "", "errors": n_err, "warnings": n_warn,
            "issues": issues, "checked_at": datetime.now().isoformat(timespec="seconds")}
