"""One application's versions: the drafts the writer produced and the Word files rendered from them, newest first with
the older ones tucked underneath. Nothing generated or rendered is ever overwritten. Shown in the Applications detail."""
from __future__ import annotations

import subprocess
import sys

import streamlit as st

from cvdrafter import config, docstate, files, generate, memory
from cvdrafter.llm import LLMError
from cvdrafter.preview import word_diff
from cvdrafter.render import cv_text
from cvdrafter.textutil import safe
from cvdrafter.verify import errors
from ui.common import DOCX_MIME, blocked_panel, bullets, models, render_checked, show_issues


def stamp(at: str | None) -> str:
    """'2026-10-22T20:51:00' -> '2026-10-22 20:51'"""
    return (at or "").replace("T", " ")[:16]


def earlier(items: list, noun: str, label, key: str, show) -> None:
    """The newest of `items` is shown by the caller; the rest sit in one expander, one picked at a time."""
    if not items:
        return
    with st.expander(f"Earlier {noun} ({len(items)})"):
        pos = st.selectbox(noun.capitalize(), range(len(items)), format_func=lambda i: label(items[i]), key=f"{key}_pick",
                           label_visibility="collapsed")
        show(items[pos])


# ---------------------------------------------------------------- drafts (the text, rendered or not)

def letter_problems(key: str) -> list[dict]:
    """The errors that stopped the last render and that a rewrite of the letter can fix (not the CV or the company)."""
    ev = st.session_state.get(f"blocked_{key}") or {}
    return [i for i in errors(ev.get("issues", [])) if not str(i["location"]).startswith((*generate.CV_SLOTS, "company"))]


def letter_fixer(app: dict, draft: dict, key: str, profile: dict) -> None:
    """Let the writer rewrite the letter: fix what the check found and follow your instructions. Saved as a new draft."""
    problems = letter_problems(key)
    with st.container(border=True):
        st.markdown("**Rewrite the cover letter**")
        st.caption("Only the letter is rewritten; the CV stays as it is. The new letter is checked again and saved as a "
                   "new draft at the top of this list. This one stays.")
        ask = st.text_area("Instructions (optional)", key=f"{key}_ask", height=80,
                           placeholder="e.g. leave out the coursework, say when I graduate, one project per paragraph")
        label = (f"Rewrite to fix {len(problems)} problem(s)" + (" + follow my instructions" if ask.strip() else "")
                 if problems else "Rewrite with my instructions")
        if not st.button(label, key=f"{key}_rewrite", type="primary", disabled=not problems and not ask.strip()):
            return
        m = models()
        ctx = dict(company=app["company"], role=app["role"], jd=app["jd"] or "", notes=app["notes"] or "", extra="",
                   analysis=app["analysis"] or {}, profile=profile, app_id=app["id"])
        status = st.status("Rewriting the cover letter…", expanded=True)
        try:
            result = generate.rewrite_letter(ctx, draft, m["writer"], m["checker"] if m["llm_check"] else None, problems,
                                             ask, progress=status.write)
        except LLMError as e:
            status.update(label="Failed", state="error")
            st.error(str(e))
            return
        memory.update(app["id"], generated=result["draft"], final=None)  # final: the old edits belong to the old letter
        st.session_state[f"rewrote_{app['id']}"] = result["issues"]
        st.rerun()


def rewrite_result(app_id: int) -> None:
    """What the check said about the last rewrite, shown on the draft it produced."""
    issues = st.session_state.get(f"rewrote_{app_id}")
    if issues is None:
        return
    left = [i for i in errors(issues) if str(i["location"]).startswith("cover_letter")]
    if left:
        st.warning(f"Rewritten, but the check still finds {len(left)} problem(s) in the letter. Edit them in the "
                   "Cover letter tab, or rewrite again with an instruction.")
        show_issues(left)
    else:
        st.success("Rewritten. The check found no problems in the letter. Create the Word files when it reads right.")


def letter_editor(app: dict, draft: dict, key: str, limits: dict) -> None:
    """The newest letter, editable in place. Saved as your edit of this draft; the CV is untouched."""
    paragraphs = draft["cover_letter"]
    before = [p["text"].replace("**", "") for p in paragraphs]
    texts = [st.text_area(f"Paragraph {n}", text, key=f"{key}_p{n}", height=150) for n, text in enumerate(before, 1)]
    lo, hi = limits["cl_words"]["min"], limits["cl_words"]["max"]
    words = sum(len(t.split()) for t in texts)
    c1, c2 = st.columns([1, 3])
    (c2.success if lo <= words <= hi else c2.warning)(f"{words} words (target {lo}–{hi})")
    if c1.button("Save my edits", key=f"{key}_save", disabled=texts == before, width="stretch"):
        letter = [{**p, "text": t.strip()} for p, t in zip(paragraphs, texts) if t.strip()]
        memory.update(app["id"], final={**draft, "cover_letter": letter})
        st.session_state.pop(f"blocked_{key}", None)  # the problems were about the old text; rendering checks again
        st.session_state.pop(f"rewrote_{app['id']}", None)
        st.rerun()


def newest_draft(app: dict, d: dict, renders: list[dict], profile: dict, limits: dict) -> None:
    """The latest draft with everything you can do to it. An older draft is only shown (see old_draft)."""
    # The newest draft carries the edits you made since; earlier ones render as written.
    edited = app["final"]
    draft = app["final"] if edited else d["draft"]
    key = f"gen{d['id']}"
    done = sorted(e["rendered_at"] for e in renders if e.get("rendered_at", "") >= d["at"])
    st.markdown(f"**Draft written {stamp(d['at'])}** · " + (f"✅ Word files created {stamp(done[0])}" if done else "📝 No Word files yet"))
    b1, b2, _ = st.columns([2, 2, 5])
    render_now = b1.button("Create Word files" + (" (with your edits)" if edited else ""), key=key, type="primary",
                           width="stretch")
    if b2.button("Open in the editor", key=f"{key}_open", width="stretch",
                 help="Edit every CV bullet and letter paragraph with a live preview on the Apply page."):
        st.session_state["open_app_id"] = app["id"]
        st.switch_page("ui/new_app.py")
    if render_now or blocked_panel(key):
        out = render_checked(app, draft, profile, key, override=not render_now, make_pdf=True)
        if out:
            memory.update(app["id"], files=out, status="ready" if app["status"] == "draft" else app["status"])
        st.rerun()
    fix_here = st.container()  # the rewrite box sits right under the problems when a render was stopped
    rewrite_result(app["id"])
    t_cv, t_cl = st.tabs(["CV", "Cover letter"])
    with t_cv:
        for section in cv_text(draft, profile):
            if section["bullets"]:
                st.markdown(f"**{safe(section['heading'])}**")
                st.markdown(bullets(section["bullets"]))
    with t_cl:
        with fix_here if letter_problems(key) else t_cl:
            letter_fixer(app, draft, key, profile)
        letter_editor(app, draft, key, limits)
        with st.popover("Copy text"):
            st.code("\n\n".join(p["text"].replace("**", "") for p in draft["cover_letter"]), language=None, wrap_lines=True)


def old_draft(d: dict, profile: dict) -> None:
    st.caption("An earlier draft, kept as it was written. Edit or rewrite the newest one.")
    t_cv, t_cl = st.tabs(["CV", "Cover letter"])
    with t_cv:
        for section in cv_text(d["draft"], profile):
            if section["bullets"]:
                st.markdown(f"**{safe(section['heading'])}**")
                st.markdown(bullets(section["bullets"]))
    with t_cl:
        for p in d["draft"]["cover_letter"]:
            st.markdown(safe(p["text"].replace("**", "")))


# ---------------------------------------------------------------- renders (the Word files)

def review(e: dict, kind: str, key: str) -> None:
    """The check results for this render, your flags, and a form to flag a new problem."""
    ev = e.get("evaluation")
    with st.popover("Check results"):
        if not ev:
            st.write("This render wasn't checked.")
        else:
            st.caption(f"Checked {ev['checked_at'].replace('T', ' ')[:16]} · rules" + (f" + AI fact-check ({ev['checker']})" if ev["ai_checked"] else " only"))
            if ev.get("overridden"):
                st.error("Rendered with the override, despite the errors below.")
            show_issues(ev["issues"])
    for i, f in enumerate(e.get("flags") or []):
        if f.get("doc") != kind:
            continue
        if f["resolved"]:
            st.caption(f"~~🚩 {safe(f['where'])}: {safe(f['note'])}~~ (resolved)")
            continue
        a, b = st.columns([5, 1])
        a.warning(f"🚩 **{safe(f['where'])}**: {safe(f['note'])}")
        if b.button("Resolved", key=f"{key}_res{i}"):
            files.resolve_flag(e["folder"], i)
            st.rerun()
    places = ["Whole document"] + ([s["heading"] for s in e.get("cv") or [] if s["bullets"]] if kind == "cv"
                                   else [f"Paragraph {n}" for n in range(1, len(e.get("cover_letter") or []) + 1)])
    with st.popover("🚩 Flag a problem"):
        with st.form(f"{key}_flag", clear_on_submit=True):
            where = st.selectbox("Where", places)
            note = st.text_area("What's wrong", placeholder="e.g. says the map was presented to management; it was senior engineers")
            rule = st.checkbox("Also add this as a rule the writer and fact-checker must follow from now on")
            if st.form_submit_button("Save flag", type="primary") and note.strip():
                files.add_flag(e["folder"], kind, where, note.strip())
                if rule:
                    problems = files.add_claim_guard(f"{where}: {note.strip()}" if where != "Whole document" else note.strip())
                    if problems:
                        st.error("Couldn't add the rule: " + "; ".join(problems))
                        st.stop()
                st.rerun()


def downloads(e: dict, kind: str, key: str) -> None:
    cols = st.columns(3)
    for col, ext in zip(cols, ("docx", "pdf")):
        path = e["paths"].get(f"{kind}_{ext}")
        if path:
            col.download_button(f".{ext}", path.read_bytes(), file_name=path.name, key=f"{key}_{ext}", width="stretch",
                                mime=DOCX_MIME if ext == "docx" else "application/pdf", type="primary" if ext == "docx" else "secondary")
    if sys.platform == "darwin" and cols[2].button("Show in Finder", key=f"{key}_open", width="stretch"):
        subprocess.run(["open", str(e["folder"])])


def render_view(e: dict, renders: list[dict], key: str) -> None:
    """One render: its CV and its letter, each with its check result, flags, downloads and the change since the render before."""
    t_cv, t_cl = st.tabs([f"CV · {docstate.label(e, 'cv')}" if e.get("cv") else "CV",
                          f"Cover letter · {docstate.label(e, 'cl')}" if e.get("cover_letter") else "Cover letter"])
    older = [x for x in renders if x.get("rendered_at", "") < e.get("rendered_at", "")]

    def before(kind: str) -> dict | None:
        return next((x for x in older if x.get(kind)), None)

    with t_cv:
        if not e.get("cv"):
            st.caption("No CV in this render.")
        else:
            downloads(e, "cv", f"{key}_cv")
            review(e, "cv", f"{key}_cv")
            prev = before("cv")
            diff = prev and st.toggle("Show changes since the previous render", key=f"{key}_cvd")
            for section in e["cv"]:
                if not section["bullets"]:
                    continue
                st.markdown(f"**{safe(section['heading'])}**")
                if diff:
                    old = next((s["bullets"] for s in prev["cv"] if s["heading"] == section["heading"]), [])
                    st.markdown(word_diff(" • ".join(old), " • ".join(section["bullets"])), unsafe_allow_html=True)
                else:
                    st.markdown(bullets(section["bullets"]))
    with t_cl:
        if not e.get("cover_letter"):
            st.caption("No cover letter in this render.")
        else:
            downloads(e, "cl", f"{key}_cl")
            review(e, "cl", f"{key}_cl")
            prev = before("cover_letter")
            if prev and st.toggle("Show changes since the previous render", key=f"{key}_cld"):
                st.markdown(word_diff("\n\n".join(prev["cover_letter"]), "\n\n".join(e["cover_letter"])), unsafe_allow_html=True)
            else:
                for par in e["cover_letter"]:
                    st.markdown(safe(par))
            with st.popover("Copy text"):
                st.code("\n\n".join(e["cover_letter"]), language=None, wrap_lines=True)


# ---------------------------------------------------------------- the tab

def panel(app: dict, renders: list[dict], profile: dict) -> None:
    """Latest draft and latest render first; every earlier one underneath."""
    limits = config.load_slots()["limits"]
    drafts = memory.drafts(app["id"])
    st.markdown("#### Draft")
    if not drafts:
        st.caption("Nothing generated yet. Open in editor to write the CV and cover letter.")
    else:
        newest_draft(app, drafts[0], renders, profile, limits)
        earlier(drafts[1:], "drafts", lambda d: stamp(d["at"]), f"drafts{app['id']}", lambda d: old_draft(d, profile))
    st.divider()
    st.markdown("#### Word files")
    st.caption("Every render is saved as a new version in output/. Your fact bank and templates are never changed by it.")
    if not renders:
        st.caption("No Word files yet.")
        return
    st.markdown(f"**Rendered {stamp(renders[0].get('rendered_at'))}**")
    render_view(renders[0], renders, f"r{app['id']}_0")
    earlier(renders[1:], "renders",
            lambda e: f"{stamp(e.get('rendered_at'))} · CV {docstate.LABELS[docstate.check(e, 'cv')]} · "
                      f"letter {docstate.LABELS[docstate.check(e, 'cl')]}",
            f"renders{app['id']}", lambda e: render_view(e, renders, f"r{app['id']}_{e['folder'].name}"))
