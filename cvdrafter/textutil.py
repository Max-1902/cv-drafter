"""Small deterministic text helpers used by the fact checker."""
from __future__ import annotations

import html
import re

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
THOUSANDS_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
MD_SPECIAL_RE = re.compile(r"([\\`*_{}\[\]()#+!|~$-])")
# Spellings of the same tool. A posting asking for "ReactJS" is matched by "React" in your profile, and the other way round.
# No plain words here ("node", "js"): an ordinary word in your profile must never unlock a tool you don't have.
ALIASES = [{"react", "reactjs", "react.js"}, {"node.js", "nodejs"}, {"vue", "vuejs", "vue.js"},
           {"postgresql", "postgres"}, {"kubernetes", "k8s"},
           {"object-oriented programming", "object oriented programming", "oop"}, {"ci/cd", "ci / cd"},
           {".net", "dotnet"}, {"c#", "csharp"}, {"amazon web services", "aws"}, {"google cloud", "gcp"},
           {"machine learning", "ml"}, {"large language models", "llms", "llm"}, {"go", "golang"}]
# Tools named like ordinary words ("go live", "excel at", "R&D"): only the capitalised name counts.
WORD_TOOLS = {"go", "golang", "r", "rust", "excel", "swift", "spark", "dart", "julia", "ruby"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "item"


def plain(text: str) -> str:
    """Strip the **bold** markup the writer may use."""
    return (text or "").replace("**", "")


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", plain(text)))


def numbers(text: str) -> set[str]:
    return set(NUM_RE.findall(text or ""))


def _variants(n: str) -> set[str]:
    # "2,5" == "2.5"; "1,000" == "1.000" == "1000". Separators are only dropped when they group thousands,
    # so "9.5" never matches "95".
    out = {n, n.replace(",", ".")}
    if THOUSANDS_RE.fullmatch(n):
        out.add(re.sub(r"[.,]", "", n))
    return out


def unsupported_numbers(text: str, allowed_text: str) -> list[str]:
    allowed: set[str] = set()
    for n in numbers(allowed_text):
        allowed |= _variants(n)
    return sorted(n for n in numbers(text) if not (_variants(n) & allowed))


def has_term(term: str, text: str) -> bool:
    term = (term or "").strip().lower()
    if not term:
        return False
    spellings = next((group for group in ALIASES if term in group), {term})
    if term in WORD_TOOLS:
        return any(re.search(r"(?<![\w])" + re.escape(t.capitalize()) + r"(?![\w&-])", text or "") for t in spellings)
    text = (text or "").lower()
    return any(re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", text) for t in spellings)


def dedupe(items) -> list:
    seen, out = set(), []
    for item in items or []:
        key = str(item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def safe(text) -> str:
    """Escape text from the AI or a job posting before it goes into st.markdown / unsafe HTML.

    Blocks injected HTML and markdown images/links, which would make the browser load an outside URL
    (a way for a malicious posting to leak your data).
    """
    return MD_SPECIAL_RE.sub(r"\\\1", html.escape(str(text if text is not None else ""), quote=False))


def safe_html(text) -> str:
    """Escape text placed inside an HTML block (<div>…</div>) in st.markdown.

    Markdown isn't parsed inside an HTML block, so only HTML is escaped. Newlines are flattened because a blank
    line would end the block and let the rest be parsed as markdown.
    """
    return html.escape(" ".join(str(text if text is not None else "").split()), quote=False)
