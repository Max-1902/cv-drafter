"""Trim a pasted job posting before it goes to the AI.

clean(): drops job-board page text ("About the job", "Show more", LinkedIn's "Benefits found in job post").
core():  also drops benefits, EEO, pay-transparency and recruiting-fraud boilerplate. That's often a third or more of
         a posting and the writer and checker never need it. The analyst still gets clean(), because work
         authorization and pay usually sit in exactly that boilerplate.
"""
from __future__ import annotations

import re

CHROME_START = re.compile(r"^(?:\s*(?:a?bout the job|full job description|job description:?)\s*\n)+", re.I)
CHROME_LINE = re.compile(r"^\s*(?:…\s*)?(?:show|see) (?:more|less)\s*$", re.I | re.M)
CHROME_TAIL = re.compile(r"\n\s*benefits found in job post\s*\n(?:.*\n?){0,12}$", re.I)

BOILER_HEADING = re.compile(
    r"benefits|perks|what we offer|compensation|\bpay\b|salary|equal (?:employment )?opportunit|\beeo|more about|"
    r"life at|diversity|inclusion|accommodation|privacy|fraud|scam|recruiting notice", re.I)
BOILER_PARAGRAPH = re.compile(
    r"equal (?:employment )?opportunity employer|visa sponsorship|employer sponsorship|pay range|base pay|salary range|"
    r"compensation package|401\s?\(k\)|reasonable accommodation|e-verify|recruiter who has|third[- ]party communication",
    re.I)


def clean(jd: str) -> str:
    text = (jd or "").replace("\r\n", "\n")
    text = CHROME_TAIL.sub("\n", CHROME_START.sub("", text))
    text = CHROME_LINE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _is_heading(block: str) -> bool:
    return "\n" not in block and len(block) <= 70 and not re.search(r"[.!?]$", block)


def core(jd: str) -> str:
    text = clean(jd)
    kept, dropping = [], False
    for block in (b.strip() for b in text.split("\n\n")):
        if not block:
            continue
        if _is_heading(block):
            dropping = bool(BOILER_HEADING.search(block))
            if not dropping:
                kept.append(block)
        elif not dropping and not BOILER_PARAGRAPH.search(block):
            kept.append(block)
    out = "\n\n".join(kept)
    # Unusual formatting can fool the heading test; never hand the writer a stub.
    return out if len(out) >= max(400, len(text) // 3) else text
