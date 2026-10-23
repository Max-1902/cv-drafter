"""Fill the fixed .docx templates with text. Layout, fonts and styling come only from the templates."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from docxtpl import DocxTemplate, RichText

from . import config
from .pdf import page_count, to_pdf
from .textutil import slug

BOLD_RE = re.compile(r"(\*\*.+?\*\*)")
HISTORY_FILE = "render.json"  # what was generated, read by the History page


def rich(text: str) -> RichText:
    """'Built **40+ rules**' -> RichText with the bold part bold; everything else inherits the template style."""
    rt = RichText()
    for part in BOLD_RE.split(text or ""):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            rt.add(part[2:-2], bold=True)
        elif part:
            rt.add(part.replace("**", ""))
    return rt


def _personal(profile: dict) -> dict:
    p = dict(profile["personal"])
    p["contact_line"] = "  |  ".join(p[k] for k in ("location", "phone", "email", "github", "linkedin", "website") if p.get(k))
    return p


def cv_context(draft: dict, profile: dict) -> dict:
    labels = config.labels()
    projects = {p["id"]: p for p in profile["projects"]}
    lang_line = "; ".join(x for x in [", ".join(profile["languages"]), "; ".join(profile["activities"])] if x)
    return {
        "personal": _personal(profile),
        "education": [
            {
                "institution": e.get("institution", ""),
                "location": e.get("location", ""),
                "dates": e.get("dates", ""),
                "degree": e.get("degree", ""),
                "gpa": e.get("gpa", ""),
                "details": [f["text"] for f in e["facts"] if not f.get("learned")],
                "coursework": e.get("coursework", ""),
            }
            for e in profile["education"]
        ],
        "experience": [
            {
                "company": job.get("company", ""),
                "title": job.get("title", ""),
                "location": job.get("location", ""),
                "dates": job.get("dates", ""),
                "bullets": [rich(b["text"]) for b in draft["experience"].get(job["id"], [])],
            }
            for job in profile["experience"]
        ],
        "projects": [
            {
                "name": projects[p["id"]].get("name", ""),
                "stack": projects[p["id"]].get("stack", ""),
                "bullets": [rich(b["text"]) for b in p["bullets"]],
            }
            for p in draft["projects"]
        ],
        "skills": [{"category": s["category"], "list": ", ".join(s["items"])} for s in draft["skills"]],
        "languages_label": labels["languages_activities"],
        "languages_line": lang_line,
    }


def cl_context(draft: dict, profile: dict, company: str, recipient: str = "", address: str = "") -> dict:
    labels = config.labels()
    d = date.today()
    return {
        "personal": _personal(profile),
        "date": f"{d.strftime('%B')} {d.day}, {d.year}",
        "recipient_lines": [l for l in [recipient or "", company or "", *(address or "").splitlines()] if l.strip()],
        "salutation": labels["salutation_named"].format(name=recipient) if recipient else labels["salutation_generic"],
        "paragraphs": [p["text"].replace("**", "") for p in draft["cover_letter"]],
        "closing": labels["closing"],
    }


def _render(template: Path, context: dict, out: Path) -> Path:
    tpl = DocxTemplate(template)
    tpl.render(context, autoescape=True)
    tpl.save(out)
    return out


def cv_text(draft: dict, profile: dict) -> list[dict]:
    """The tailored parts of a CV as plain text, one entry per job or project, for the history page."""
    projects = {p["id"]: p for p in profile["projects"]}
    out = [{"heading": f"{job.get('company', '')} | {job.get('title', '')}",
            "bullets": [b["text"].replace("**", "") for b in draft["experience"].get(job["id"], [])]}
           for job in profile["experience"]]
    out += [{"heading": projects[p["id"]].get("name", ""), "bullets": [b["text"].replace("**", "") for b in p["bullets"]]}
            for p in draft["projects"]]
    out.append({"heading": "Skills", "bullets": [f"{s['category']}: {', '.join(s['items'])}" for s in draft["skills"]]})
    return out


def render_documents(draft: dict, profile: dict, company: str, role: str,
                     recipient: str = "", address: str = "", make_pdf: bool = False, app_id: int | None = None,
                     evaluation: dict | None = None) -> dict:
    # One folder per application, one subfolder per render: earlier versions are never overwritten.
    # The application id keeps two applications for the same role from sharing a folder.
    suffix = f"_{app_id}" if app_id is not None else ""
    parent = config.OUTPUT_DIR / f"{slug(company)}_{slug(role)}{suffix}"
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder, n = parent / stamp, 2
    while folder.exists():
        folder, n = parent / f"{stamp}_{n}", n + 1
    folder.mkdir(parents=True)
    name = profile["personal"].get("name", "CV").replace(" ", "_")
    cl = cl_context(draft, profile, company, recipient, address)
    files = {
        "cv_docx": _render(config.CV_TEMPLATE, cv_context(draft, profile), folder / f"{name}_CV_{slug(company)}.docx"),
        "cl_docx": _render(config.CL_TEMPLATE, cl, folder / f"{name}_Cover_Letter_{slug(company)}.docx"),
    }
    if make_pdf:
        for key in ("cv", "cl"):
            pdf = to_pdf(files[f"{key}_docx"])
            if pdf:
                files[f"{key}_pdf"] = pdf
    out = {k: str(v) for k, v in files.items()}
    if "cv_pdf" in files:
        out["cv_pages"] = page_count(files["cv_pdf"])
    meta = {"app_id": app_id, "company": company, "role": role, "rendered_at": datetime.now().isoformat(timespec="seconds"),
            "files": {k: Path(v).name for k, v in files.items()},
            "cv": cv_text(draft, profile), "cover_letter": cl["paragraphs"],
            "evaluation": evaluation, "flags": []}
    (folder / HISTORY_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
