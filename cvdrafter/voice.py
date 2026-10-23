"""Catch what makes a cover letter read as AI-written. Code, not AI, so it runs on every draft and every fix round."""
from __future__ import annotations

import re
from statistics import mean, pstdev

from .textutil import has_term, plain

# Words and phrases people rarely write in their own letters but models reach for constantly.
AI_PHRASES = [
    "i am writing to", "i'm writing to", "thrilled", "delighted", "excited", "eager", "keen to", "keen interest",
    "drawn to", "resonate", "resonates", "resonated", "aligns", "aligned with", "align with", "perfect fit",
    "natural fit", "ideal candidate", "unique blend", "unique combination", "uniquely positioned", "well-positioned",
    "honed", "spearheaded", "showcase", "showcases", "showcased", "testament", "tapestry", "delve", "ever-evolving",
    "in today's", "furthermore", "moreover", "additionally", "i am confident", "i'm confident", "i would welcome",
    "i would love the opportunity", "thank you for considering", "thank you for your consideration",
    "look forward to the opportunity", "truly", "genuinely", "invaluable", "seamless", "seamlessly", "game-changer",
    "at the intersection of", "bridge the gap", "commitment to excellence", "meaningful impact", "make an impact",
    "impactful", "strong foundation", "solid foundation", "equipped me", "fueled", "sparked my", "ignited", "journey",
    "landscape", "realm", "elevate", "empower", "foster", "fostering", "harness", "utilize", "utilized", "pivotal",
    "paramount", "instrumental", "unwavering", "multifaceted", "holistic", "myriad", "plethora", "deep understanding",
    "robust understanding", "thrive", "thriving", "exceptional", "value proposition", "next chapter",
]

PATTERNS = [
    (re.compile(r"—|(?<!\d)\s[–-]\s|(?<=[A-Za-z])–(?=[A-Za-z])"), "Uses a dash as punctuation. Use a period or a comma."),
    (re.compile(r"\bnot (?:just|only|merely|simply)\b|\bmore than just\b|\bnot\b[^.;]{1,40},\s*but\b", re.I),
     "\"Not X, but Y\" / \"not just\" contrast."),
    (re.compile(r"[!?]"), "Exclamation or question mark."),
    (re.compile(r"(?:^|[.]\s+)(?:Ultimately|In short|Overall|In summary|In conclusion)\b|(?:^|[.]\s+)(?:This|These|That|Those)\s+"
                r"(?:experiences?|projects?|roles?|internships?)\s+(?:taught|showed|gave|ha(?:ve|s) given|equipped|prepared|"
                r"reinforced|strengthened|deepened|sharpened)"), "Moral or summary sentence after the evidence. Let the evidence stand."),
    (re.compile(r"\b(?:detail-oriented|results-driven|highly motivated|self-starter|team player|quick learner|hard-?working|"
                r"driven individual)\b", re.I), "Describes you with adjectives. The evidence should show it instead."),
]
OPENER = re.compile(r"^\s*(?:As an?\s|With (?:a|my)\s|Having\s)")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", plain(text).strip()) if s]


def paragraph_tells(text: str, first: bool = False, skip: tuple[str, ...] = ()) -> list[str]:
    """skip: phrases already reported elsewhere (your banned_phrases), so they aren't flagged twice."""
    text = plain(text)
    out = [f"AI-sounding wording: \"{p}\"." for p in AI_PHRASES if p not in skip and has_term(p, text)]
    out += [msg for pattern, msg in PATTERNS if pattern.search(text)]
    if first and OPENER.match(text):
        out.append("Opens with \"As a…\" / \"With my…\". Start with the role and your strongest evidence.")
    return out


def letter_tells(paragraphs: list[str]) -> list[str]:
    sentences = [s for p in paragraphs for s in _sentences(p)]
    if len(sentences) < 6:
        return []
    out = []
    lengths = [len(s.split()) for s in sentences]
    if pstdev(lengths) < 4:
        out.append(f"Every sentence is about {mean(lengths):.0f} words long. Mix short sentences with longer ones.")
    starts_i = sum(s.split()[0] in ("I", "I'm", "I've", "I'd") for s in sentences)
    if starts_i / len(sentences) > 0.6:
        out.append(f"{starts_i} of {len(sentences)} sentences start with \"I\".")
    return out
