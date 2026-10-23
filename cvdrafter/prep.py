"""Interview prep grounded in the facts the application actually used."""
from __future__ import annotations

from .llm import chat_json
from .posting import core
from .profile import facts_for_prompt, load_guardrails
from .verify import iter_items

SYSTEM = """You prepare a candidate for interviews. Every suggested answer must be built ONLY from the candidate's \
fact bank; if a good answer needs something the facts don't contain, say so in "gap" rather than inventing it. \
Respect these claim guards:
{guards}
Output a single JSON object."""

USER = """JOB POSTING:
\"\"\"
{jd}
\"\"\"

WHAT THE CANDIDATE SENT (CV bullets and cover letter):
{sent}

FACT BANK:
{facts}

Return JSON:
{{
  "questions": [{{"question": "...", "why_they_ask": "...", "answer_points": ["talking point grounded in facts"], "fact_ids": ["..."], "gap": "what the candidate lacks for this question, or empty"}}],
  "defend": [{{"claim": "a statement from the application an interviewer will probe", "be_ready_to_explain": "what details to know cold"}}],
  "ask_them": ["smart question for the interviewer, based on the posting"]
}}
8-10 questions mixing technical deep-dives on the cited work, role-fit questions, and the posting's biggest gaps."""


def interview_prep(app: dict, profile: dict, model: str) -> dict:
    draft = app["final"] or app["generated"]
    sent = "\n".join(f"- {item['text']}" for _, _, item, _ in iter_items(draft)) if draft else "(nothing generated yet)"
    guards = "\n".join(f"- {g}" for g in load_guardrails()["claim_guards"])
    return chat_json(SYSTEM.format(guards=guards), USER.format(jd=core(app["jd"]), sent=sent, facts=facts_for_prompt(profile)),
                     model, temperature=0.3, task="interview prep")
