"""Short answers to an application form's questions ("Why this role?", "Which AI tools are you comfortable with?").

Written with the cover letter's rules and voice from your facts and the posting, then checked like the letter: every
answer cites its facts, numbers must be in them, never-mention and banned phrases, AI tells, skills you don't have.
One fix round runs if the check finds errors; what's left is shown next to the answer.
"""
from __future__ import annotations

import json
import re

from . import config, posting, prompts
from .generate import writer_system
from .llm import chat_json
from .textutil import plain, word_count
from .verify import errors, issue, run_checks

WORDS = (40, 120)  # when the question sets no limit
LIMIT_RE = re.compile(r"(?:max(?:imum)?\.?|up to|at most|no more than|within|in|under|limit(?:ed)? to)?\s*"
                      r"(\d{2,5})\s*(characters?|chars?|words?)(?:\s*(?:max(?:imum)?|or less|or fewer))?", re.I)
# Letter-level checks (total words, paragraph count, layout width) don't apply to separate short answers.
SKIP = ("Too long for the layout",)


def limit(question: str) -> tuple[str, int] | None:
    """("words" | "chars", n) when the question states a length limit."""
    if m := LIMIT_RE.search(question or ""):
        return ("chars" if m[2].lower().startswith("char") else "words", int(m[1]))
    return None


def split_questions(text: str) -> list[str]:
    """One question per line; a line that only states a limit, like "(max 500 characters)", joins the one above."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
        if not line:
            continue
        if out and (line.startswith("(") or LIMIT_RE.fullmatch(line.strip("()").strip())):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def _length_problem(answer: str, question: str) -> str:
    lim, text = limit(question), plain(answer)
    if lim and lim[0] == "chars" and len(text) > lim[1]:
        return f"{len(text)} characters; the question allows {lim[1]}."
    if lim and lim[0] == "words" and word_count(text) > lim[1]:
        return f"{word_count(text)} words; the question allows {lim[1]}."
    if not lim and word_count(text) > WORDS[1] + 30:
        return f"{word_count(text)} words; keep it under {WORDS[1]}."
    return ""


def check(items: list[dict], ctx: dict, checker_model: str | None) -> list[list[dict]]:
    """Issues per answer, from the letter's checks plus the question's length limit."""
    limits = config.load_slots()["limits"]
    draft = {"experience": {}, "projects": [], "skills": [],
             "cover_letter": [{"text": i["answer"], "sources": i["sources"]} for i in items]}
    found = run_checks(draft, ctx["profile"], ctx["analysis"] or {}, ctx["jd"], ctx.get("notes") or "", limits, checker_model)
    per = [[] for _ in items]
    for i in found:
        m = re.match(r"cover_letter/(\d+)$", str(i["location"]))
        if m and int(m[1]) <= len(items) and not i["message"].startswith(SKIP):
            per[int(m[1]) - 1].append(i)
    for n, item in enumerate(items):
        if msg := _length_problem(item["answer"], item["question"]):
            per[n].append(issue("error", f"cover_letter/{n + 1}", msg))
    return per


def _parse(raw: dict, questions: list[str]) -> list[dict]:
    got = {}
    for a in raw.get("answers") or []:
        if isinstance(a, dict):
            try:
                n = int(a.get("n") or 0)
            except (TypeError, ValueError):
                continue
            got[n] = {"answer": str(a.get("answer") or "").strip(),
                      "sources": [str(s) for s in a.get("sources") or [] if s]}
    return [{"question": q, **got.get(n, {"answer": "", "sources": []})} for n, q in enumerate(questions, 1)]


def answer_questions(ctx: dict, questions: list[str], writer_model: str, checker_model: str | None = None,
                     progress=lambda msg: None) -> list[dict]:
    """[{question, answer, sources, issues}]. ctx: company, role, jd, notes, analysis, profile."""
    system = writer_system(ctx["profile"])
    numbered = "\n".join(f"{n}. {q}" + (f"\n   LIMIT: {lim[1]} {lim[0]}" if (lim := limit(q)) else "")
                         for n, q in enumerate(questions, 1))
    user = prompts.ANSWERS_USER.format(words_min=WORDS[0], words_max=WORDS[1], company=ctx["company"] or "?",
                                       role=ctx["role"] or "?", jd=posting.core(ctx["jd"]),
                                       notes=ctx.get("notes") or "(none)", questions=numbered)
    progress("Answering…")
    items = _parse(chat_json(system, user, writer_model, temperature=0.3, effort="medium", task="form answers"), questions)
    progress("Fact-checking…")
    per = check(items, ctx, checker_model)
    problems = [f"- [answer {n}] {i['message']}" for n, issues in enumerate(per, 1) for i in errors(issues)]
    if problems:
        progress(f"Fixing {len(problems)} problem(s)…")
        shown = json.dumps({"answers": [{"n": n, "answer": i["answer"], "sources": i["sources"]}
                                        for n, i in enumerate(items, 1)]}, ensure_ascii=False, indent=1)
        fix = prompts.ANSWERS_FIX.format(answers=shown, problems="\n".join(problems))
        items = _parse(chat_json(system, user + "\n\n" + fix, writer_model, temperature=0.2, effort="medium",
                                 task="form answers fix"), questions)
        per = check(items, ctx, checker_model)
    return [{**item, "issues": issues} for item, issues in zip(items, per)]
