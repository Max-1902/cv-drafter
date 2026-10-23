"""Find the hiring company: the AI's answer, checked against the posting and URL, with code fallbacks."""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

# ATS hosts where the company slug sits in the path (boards.greenhouse.io/<company>/jobs/123).
PATH_ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com", "jobvite.com",
            "breezy.hr", "teamtailor.com", "rippling.com", "dover.com")
# ATS hosts where it's the subdomain (<company>.wd5.myworkdayjobs.com).
SUBDOMAIN_ATS = ("myworkdayjobs.com", "bamboohr.com", "recruitee.com", "personio.de", "personio.com", "icims.com",
                 "applytojob.com", "taleo.net", "successfactors.com", "paylocity.com", "ultipro.com", "workday.com")
# Job boards say nothing about the employer from the URL alone.
BOARDS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "joinhandshake.com", "wellfound.com",
          "angel.co", "monster.com", "dice.com", "simplyhired.com", "builtin.com", "otta.com", "welcometothejungle.com",
          "google.com", "efinancialcareers.com", "levels.fyi", "ycombinator.com", "stepstone.de", "xing.com")
GENERIC_SUBDOMAINS = {"www", "careers", "career", "jobs", "job", "apply", "boards", "job-boards", "hire", "talent", "en", "us"}
# Never the employer, even if the AI says so.
NOT_EMPLOYERS = {"greenhouse", "lever", "workday", "ashby", "linkedin", "indeed", "glassdoor", "ziprecruiter", "handshake",
                 "icims", "smartrecruiters", "workable", "jobvite", "unknown", "n/a", "company", "the company", "?", "none"}
LEGAL_SUFFIX = re.compile(r"[,.]?\s+(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|lp|llp|sa|group|holdings)\.?$", re.I)

NAME = r"([A-Z][\w&.'’-]*(?:[ \t]+(?:&[ \t]+)?[A-Z0-9][\w&.'’-]*){0,4})"
TEXT_PATTERNS = [  # (regex, weight): an EEO statement or "About X" is the strongest signal a posting gives
    (re.compile(NAME + r",?\s+(?:is|are)\s+(?:an?|a\s+proud|committed\s+to\s+being\s+an?)\s+equal\s+(?:opportunity|employment)"), 3),
    (re.compile(r"\bAbout\s+(?:us\s+at\s+)?" + NAME + r"\s*[:\n]"), 3),
    (re.compile(r"\b(?:At|Join|join)\s+" + NAME + r"[,!]?\s+(?:we|you|our|the|team|as)\b"), 2),
    (re.compile(r"(?:^|\n)\s*" + NAME + r"\s+is\s+(?:a|an|the)\s+(?:leading|global|fast|premier|top|world|quantitative|technology|financial)"), 2),
    (re.compile(r"\bWhy\s+" + NAME + r"\s*\??\s*\n"), 2),
    (re.compile(r"\b" + NAME + r"\s+is\s+hiring\b"), 2),
]
STOP = {"The", "We", "Our", "You", "This", "Your", "About", "Job", "Role", "Team", "Position", "Company", "Who", "What",
        "Why", "Join", "At", "Us", "All", "Equal", "It", "As", "In", "If", "Apply", "Benefits", "Responsibilities"}


def normalize(name: str) -> str:
    name = LEGAL_SUFFIX.sub("", (name or "").strip())
    return re.sub(r"[^a-z0-9&]+", " ", name.lower()).strip()


def _same(a: str, b: str) -> bool:
    """'Acme Robotics' == 'acmerobotics' (URL slug); 'Acme' matches 'Acme Robotics'."""
    a, b = normalize(a).replace(" ", ""), normalize(b).replace(" ", "")
    return bool(a and b) and (a in b or b in a)


def _host(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return (urlparse(url).hostname or "").lower()


def _prettify(slug: str) -> str:
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug.title() if slug.islower() or slug.isupper() else slug


def from_url(url: str) -> str:
    """The company a careers URL belongs to, or '' if the URL can't tell (job boards, missing URL)."""
    host = _host(url)
    if not host or any(host == b or host.endswith("." + b) for b in BOARDS):
        return ""
    path = [p for p in urlparse(url if "://" in url else "https://" + url).path.split("/") if p]
    for ats in PATH_ATS:
        if host == ats or host.endswith("." + ats):
            sub = host[: -len(ats)].rstrip(".").split(".")[0]
            if sub and sub not in GENERIC_SUBDOMAINS:  # acme.breezy.hr, acme.teamtailor.com
                return _prettify(sub)
            skip = {"embed", "job_app", "jobs", "job", "en", "en-us", "us", "o", "j", "careers", "company"}
            slug = next((p for p in path if p.lower() not in skip and not p.isdigit()), "")
            return _prettify(slug) if slug else ""
    for ats in SUBDOMAIN_ATS:
        if host.endswith("." + ats):
            sub = host[: -len(ats) - 1].split(".")[0]
            sub = re.sub(r"^(careers?|jobs)-|-(careers?|jobs)$", "", sub)
            return _prettify(sub) if sub not in GENERIC_SUBDOMAINS else ""
    parts = [p for p in host.split(".") if p not in GENERIC_SUBDOMAINS]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "ac", "org") and len(parts[-1]) == 2:  # acme.co.uk
        parts = parts[:-1]
    return _prettify(parts[-2]) if len(parts) >= 2 else ""


def from_text(jd: str) -> list[str]:
    """Company names the posting states itself, most likely first."""
    score = Counter()
    for pattern, weight in TEXT_PATTERNS:
        for m in pattern.finditer(jd or ""):
            name = m.group(1).strip(" .,'’")
            while name.split(" ")[0] in STOP:  # "Join The Acme team" -> "Acme"
                name = name.partition(" ")[2]
            if name and name not in STOP and normalize(name) not in NOT_EMPLOYERS:
                score[name] += weight
    # A name the posting repeats is more likely the employer than a client or partner it mentions once.
    return [n for n, _ in sorted(score.items(), key=lambda kv: (-kv[1] - mentions(kv[0], jd), kv[0]))]


def mentions(name: str, text: str) -> int:
    key = normalize(name)
    if not key:
        return 0
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", normalize(text)))


def _supported(name: str, jd: str, url: str) -> bool:
    """The name, or its distinctive first word, appears in the posting or the URL."""
    key = normalize(name)
    if not key:
        return False
    if mentions(name, jd) or key.replace(" ", "") in _host(url).replace("-", "") + (url or "").lower().replace("-", ""):
        return True
    first = key.split(" ")[0]
    return len(first) >= 4 and (mentions(first, jd) > 0 or first in (url or "").lower())


def resolve(ai_name: str, typed: str, jd: str, url: str, evidence: str = "") -> dict:
    """Pick the company and say how sure we are: high = confirmed in posting/URL, medium = one weak source, low = guess."""
    if (typed or "").strip():
        return {"company": typed.strip(), "confidence": "high", "source": "you typed it"}
    ai_name = (ai_name or "").strip()
    if normalize(ai_name) in NOT_EMPLOYERS:
        ai_name = ""
    url_name, text_names = from_url(url), from_text(jd)
    if ai_name and _supported(ai_name, jd, url):
        agrees = any(_same(ai_name, c) for c in [url_name, *text_names] if c)
        quote = f' ("{evidence.strip()[:80]}")' if evidence.strip() else ""
        return {"company": ai_name, "confidence": "high",
                "source": ("AI, confirmed by URL/posting pattern" if agrees else "AI, found in posting") + quote}
    both = [t for t in text_names if url_name and _same(t, url_name)]
    if both:
        return {"company": both[0], "confidence": "high", "source": "URL and posting agree"}
    if text_names:
        return {"company": text_names[0], "confidence": "medium", "source": "posting text pattern"}
    if url_name:
        return {"company": url_name, "confidence": "medium", "source": "careers URL"}
    if ai_name:
        return {"company": ai_name, "confidence": "low", "source": "AI only; name not found in the posting"}
    return {"company": "", "confidence": "low", "source": "not found; add it by hand"}
