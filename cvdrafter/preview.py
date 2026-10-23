"""HTML live preview that mirrors cv_template.docx / cl_template.docx, so you see edits instantly.

It's an approximation for reviewing content and length; the .docx/.pdf output is the real thing.
"""
from __future__ import annotations

import difflib
import html
import re

from .render import cl_context, cv_context
from .textutil import safe_html

CSS = """
<style>
.page{background:#fff;color:#000;font-family:'Times New Roman',Times,serif;font-size:10pt;line-height:1.18;
  width:8.5in;min-height:11in;padding:.4in .5in;box-sizing:border-box;margin:0 auto;box-shadow:0 2px 12px rgba(0,0,0,.18);
  transform-origin:top left}
.name{text-align:center;font-weight:bold;font-size:17pt;margin:0}
.contact{text-align:center;font-style:italic;font-size:9.5pt;margin-bottom:2pt}
.sec{font-weight:bold;font-size:11pt;border-bottom:1px solid #000;margin:4pt 0 1pt}
.hdr{display:flex;justify-content:space-between;margin-top:2pt}
.hdr .l{background:#d9d9d9;font-weight:bold}
.hdr .r{font-style:italic}.hdr .r.b{font-weight:bold}
.b{display:flex;gap:.08in;padding-left:.05in}.b:before{content:'❖';font-size:7pt;line-height:14pt;width:.12in;flex:none}
.b .t{flex:1}.b .rt{font-weight:bold;font-style:italic;margin-left:8pt}
.changed{background:#fff3b0}
.cl{font-size:12pt;padding:.75in 1in;line-height:1.1}.cl p{margin:0 0 10pt}
.cl .name{margin:0 0 1pt}.cl .contact{border-bottom:1px solid #000;padding-bottom:4pt;margin-bottom:24pt}
.cl .sign{margin-top:16pt;margin-bottom:28pt}
</style>"""

e = html.escape


def _rich(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(text))


def cv_html(draft: dict, profile: dict, changed: set[str] | None = None) -> str:
    """changed: bullet texts to highlight (edited by you since generation)."""
    ctx = cv_context(draft, profile)
    changed = changed or set()
    out =[CSS, '<div class="page">', f'<p class="name">{e(ctx["personal"]["name"].upper())}</p>',
           f'<div class="contact">{e(ctx["personal"]["contact_line"])}</div>', '<div class="sec">EDUCATION</div>']
    for ed in ctx["education"]:
        loc = f" | {e(ed['location'])}" if ed["location"] else ""
        out.append(f'<div class="hdr"><span class="l">{e(ed["institution"])}{loc}</span><span class="r">{e(ed["dates"])}</span></div>')
        out.append(f'<div class="b"><span class="t"><b>{e(ed["degree"])}</b></span><span class="rt">{e(ed["gpa"])}</span></div>')
        out += [f'<div class="b"><span class="t">{e(d)}</span></div>' for d in ed["details"]]
        if ed["coursework"]:
            out.append(f'<div><b>Relevant Coursework:</b> {e(ed["coursework"])}</div>')

    def bullets(texts):
        return [f'<div class="b"><span class="t{" changed" if t in changed else ""}">{_rich(t)}</span></div>' for t in texts]

    out.append('<div class="sec">EXPERIENCE</div>')
    for job in profile["experience"]:
        out.append(f'<div class="hdr"><span class="l">{e(job["company"])} | {e(job["title"])} | {e(job["location"])}</span>'
                   f'<span class="r">{e(job["dates"])}</span></div>')
        out += bullets([b["text"] for b in draft["experience"].get(job["id"], [])])
    out.append('<div class="sec">PROJECTS</div>')
    projects = {p["id"]: p for p in profile["projects"]}
    for p in draft["projects"]:
        meta = projects[p["id"]]
        out.append(f'<div class="hdr"><span class="l">{e(meta["name"])}</span><span class="r b">{e(meta.get("stack", ""))}</span></div>')
        out += bullets([b["text"] for b in p["bullets"]])
    out.append('<div class="sec">SKILLS &amp; INTERESTS</div>')
    for s in ctx["skills"]:
        out.append(f'<div class="b"><span class="t"><b>{e(s["category"])}:</b> {e(s["list"])}</span></div>')
    if ctx["languages_line"]:
        out.append(f'<div class="b"><span class="t"><b>{e(ctx["languages_label"])}:</b> {e(ctx["languages_line"])}</span></div>')
    out.append("</div>")
    return "".join(out)


def cl_html(draft: dict, profile: dict, company: str, recipient: str = "", address: str = "") -> str:
    ctx = cl_context(draft, profile, company, recipient, address)
    p = ctx["personal"]
    out = [CSS, '<div class="page cl">', f'<p class="name">{e(p["name"].upper())}</p>',
           f'<div class="contact">{e(p["contact_line"])}</div>', f"<p>{e(ctx['date'])}</p>",
           "<p>" + "<br>".join(e(l) for l in ctx["recipient_lines"]) + "</p>", f"<p>{e(ctx['salutation'])}</p>"]
    out += [f"<p>{e(par)}</p>" for par in ctx["paragraphs"]]
    out.append(f"<p class='sign'>{e(ctx['closing'])}</p><p>{e(p['name'])}</p></div>")
    return "".join(out)


def word_diff(source: str, new: str) -> str:
    """HTML diff for st.markdown: words removed from the source struck through in red, words added in green."""
    a, b = source.replace("**", "").split(), new.replace("**", "").split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(safe_html(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del style='color:#b42318'>{safe_html(' '.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins style='color:#067647;text-decoration:none;background:#dcfae6'>{safe_html(' '.join(b[j1:j2]))}</ins>")
    return " ".join(out)


def similarity(source: str, new: str) -> float:
    return difflib.SequenceMatcher(a=source.replace("**", "").lower(), b=new.replace("**", "").lower()).ratio()
