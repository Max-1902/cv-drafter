"""The CV and the cover letter as one continuous text, and back: what the Apply page's document editor shows and reads.

CV text:                         Letter text:
    # EXPERIENCE                     First paragraph.
    ## Acme Bank | Intern
    - A bullet with **bold**         Second paragraph.
    # PROJECTS
    ## Local RAG Pipeline
    - Another bullet
    # SKILLS
    Programming: Python, SQL

Headings are matched by name, never rewritten. A bullet keeps the sources it cites by matching the line you typed to the
closest old one, so the fact check still compares each text with its own facts; a line you add cites what the bullet
above it cites.
"""
from __future__ import annotations

import re

from .preview import similarity

BULLET_RE = re.compile(r"^\s*(?:[-*•❖]\s+)")
SECTIONS = ("EXPERIENCE", "PROJECTS", "SKILLS")
SAME_BULLET = 0.6  # how alike two texts must be to count as the same bullet, edited


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def job_heading(job: dict) -> str:
    return f"{job['company']} | {job['title']}"


def cv_to_text(draft: dict, profile: dict) -> str:
    names = {p["id"]: p["name"] for p in profile["projects"]}
    out = ["# EXPERIENCE", ""]
    for job in profile["experience"]:
        out += [f"## {job_heading(job)}", *(f"- {b['text']}" for b in draft["experience"].get(job["id"], [])), ""]
    out += ["# PROJECTS", ""]
    for proj in draft["projects"]:
        out += [f"## {names.get(proj['id'], proj['id'])}", *(f"- {b['text']}" for b in proj["bullets"]), ""]
    out += ["# SKILLS", "", *(f"{g['category']}: {', '.join(g['items'])}" for g in draft["skills"])]
    return "\n".join(out)


def _assign(old: list[dict], lines: list[str]) -> list[dict]:
    """The typed lines as items that keep the sources of the old item they came from."""
    items: list[dict | None] = [None] * len(lines)
    used: set[int] = set()
    for i, line in enumerate(lines):  # the same bullet, edited a little
        scored = [(similarity(o["text"], line), j) for j, o in enumerate(old) if j not in used]
        if scored and (best := max(scored))[0] >= SAME_BULLET:
            items[i] = {**old[best[1]], "text": line}
            used.add(best[1])
    spare = [j for j in range(len(old)) if j not in used]
    for i, line in enumerate(lines):  # a rewritten bullet takes an old one that nothing else matched; a new one the bullet above
        if items[i] is not None:
            continue
        if spare:
            items[i] = {**old[spare.pop(0)], "text": line}
        else:
            above = next((items[k] for k in range(i - 1, -1, -1) if items[k]), None) or (old[-1] if old else None)
            items[i] = {"text": line, "sources": list(above["sources"]) if above else []}
    return items


def text_to_cv(text: str, draft: dict, profile: dict) -> tuple[dict | None, list[str]]:
    """(the draft with your text, []) or (None, what couldn't be understood). Nothing is changed in `draft` itself."""
    jobs = {_key(job_heading(j)): j for j in profile["experience"]}
    projects = {_key(p["name"]): p for p in profile["projects"]}
    old_projects = {p["id"]: p["bullets"] for p in draft["projects"]}
    categories = {_key(c): c for c in profile["skills"]} | {_key(g["category"]): g["category"] for g in draft["skills"]}
    errors: list[str] = []
    section, block = "", None
    typed_jobs: dict[str, list[str]] = {}
    typed_projects: dict[str, list[str]] = {}
    skills: list[dict] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            title = _key(line.lstrip("#"))
            lookup = jobs if section == "EXPERIENCE" else projects if section == "PROJECTS" else {}
            if title not in lookup:
                errors.append(f"Line {n}: “{line.lstrip('#').strip()}” isn't a {'job' if section == 'EXPERIENCE' else 'project'}"
                              f" of yours here. Headings stay as they are.")
                block = None
            else:
                block = lookup[title]["id"]
                target = typed_jobs if section == "EXPERIENCE" else typed_projects
                if block in target:
                    errors.append(f"Line {n}: “{line.lstrip('#').strip()}” appears twice.")
                target.setdefault(block, [])
        elif line.startswith("#"):
            name = line.lstrip("#").strip().upper()
            if name not in SECTIONS:
                errors.append(f"Line {n}: unknown section “{line.lstrip('#').strip()}”. The sections are {', '.join(SECTIONS)}.")
            section, block = name, None
        elif section == "SKILLS":
            category, colon, items = line.partition(":")
            listed = [s.strip() for s in items.replace("**", "").split(",") if s.strip()]
            if not colon:
                errors.append(f"Line {n}: write a skills line as “Category: skill, skill”.")
            elif listed:
                skills.append({"category": categories.get(_key(category), category.strip()), "items": listed})
        elif section in ("EXPERIENCE", "PROJECTS") and block is not None:
            (typed_jobs if section == "EXPERIENCE" else typed_projects)[block].append(BULLET_RE.sub("", line).strip())
        elif section in ("EXPERIENCE", "PROJECTS"):
            errors.append(f"Line {n}: this text isn't under a heading.")
        else:
            errors.append(f"Line {n}: put this under # EXPERIENCE, # PROJECTS or # SKILLS.")
    if errors:
        return None, errors
    out = {**draft, "experience": {j["id"]: _assign(draft["experience"].get(j["id"], []), typed_jobs.get(j["id"], []))
                                   for j in profile["experience"]},
           "projects": [{"id": pid, "bullets": _assign(old_projects.get(pid, []), lines)}
                        for pid, lines in typed_projects.items() if lines],
           "skills": skills}
    return out, []


def letter_to_text(draft: dict) -> str:
    return "\n\n".join(p["text"] for p in draft["cover_letter"])


def text_to_letter(text: str, draft: dict) -> dict:
    """Paragraphs are separated by a blank line; a line break inside one is just a space."""
    paragraphs = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text.strip())]
    return {**draft, "cover_letter": _assign(draft["cover_letter"], [p for p in paragraphs if p])}


# ---------------------------------------------------------------- the Word file, read back

WORD_SECTIONS = {"EXPERIENCE": "# EXPERIENCE", "PROJECTS": "# PROJECTS", "SKILLS & INTERESTS": "# SKILLS"}


def _runs_text(paragraph, bold: bool = True) -> str:
    """A paragraph's text with bold runs as **bold**."""
    out = "".join(f"**{r.text}**" if bold and r.bold and r.text.strip() else r.text for r in paragraph.runs)
    return out.replace("****", "")


def _heading_of(text: str, jobs: set[str], projects: set[str]) -> str | None:
    """The CV text heading for a Word paragraph that is a job or project header, else None."""
    first = text.split("\t")[0].strip()
    if _key(" | ".join(first.split(" | ")[:2])) in jobs:
        return " | ".join(first.split(" | ")[:2])
    return first if _key(first) in projects else None


def word_cv_text(paragraphs: list, profile: dict) -> str:
    """The CV in a Word file as the editor's text. Education and the languages line are locked and left out."""
    from . import config
    jobs = {_key(job_heading(j)) for j in profile["experience"]}
    projects = {_key(p["name"]) for p in profile["projects"]}
    languages = config.labels()["languages_activities"]
    out: list[str] = []
    section = ""
    for para in paragraphs:
        plain = para.text.strip()
        if not plain:
            continue
        if plain in WORD_SECTIONS:
            section = plain
            out.append(WORD_SECTIONS[plain])
        elif section in ("EXPERIENCE", "PROJECTS"):
            heading = None if plain.startswith("❖") else _heading_of(para.text, jobs, projects)
            if heading:
                out.append(f"## {heading}")
            elif not plain.startswith("❖") and "\t" in plain:  # shaped like a header but not one of yours: say so
                out.append(f"## {plain.split(chr(9))[0].strip()}")
            else:
                out.append(BULLET_RE.sub("", _runs_text(para)).strip())
        elif section == "SKILLS & INTERESTS":
            line = BULLET_RE.sub("", para.text).strip()
            if line and not line.startswith(languages):
                out.append(line)
    return "\n".join(out)


def word_letter_text(paragraphs: list, profile: dict) -> str:
    """The letter's paragraphs in a Word file: what stands between the salutation and the closing."""
    from . import config
    texts = [p.text.strip() for p in paragraphs if p.text.strip()]
    start = next((i for i, t in enumerate(texts) if t.startswith("Dear ")), None)
    if start is None:
        raise ValueError("Couldn't find the salutation (“Dear …,”) in the Word file, so I can't tell where the letter starts.")
    closing = config.labels()["closing"]
    end = max((i for i, t in enumerate(texts) if t == closing and i > start), default=len(texts) - 2)
    return "\n\n".join(texts[start + 1:end])


def read_docx(path, kind: str, draft: dict, profile: dict) -> tuple[dict | None, list[str]]:
    """Your edits in a rendered Word file ('CV' or 'Cover letter') as a draft: (draft, []) or (None, what couldn't be read)."""
    from docx import Document
    try:
        paragraphs = Document(str(path)).paragraphs
        if kind == "CV":
            return text_to_cv(word_cv_text(paragraphs, profile), draft, profile)
        return text_to_letter(word_letter_text(paragraphs, profile), draft), []
    except (ValueError, OSError, KeyError) as e:
        return None, [str(e) or "The Word file couldn't be read."]
