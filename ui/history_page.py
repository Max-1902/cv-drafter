"""History: each application's details and timeline, every CV and cover letter generated, then every one rendered,
newest first. Nothing generated or rendered is ever overwritten."""
from __future__ import annotations

import subprocess
import sys

import streamlit as st

from cvdrafter import files, memory
from cvdrafter.preview import word_diff
from cvdrafter.render import cv_text
from cvdrafter.textutil import safe
from ui.common import DOCX_MIME, application_card, blocked_panel, bullets, load_profile_or_stop, render_checked, setup, show_issues

setup()
st.title("History")


def stamp(at: str | None) -> str:
    """'2026-10-22T20:51:00' -> '2026-10-22 20:51'"""
    return (at or "").replace("T", " ")[:16]


apps = memory.list_all()
if apps:
    st.subheader("Application details")
    labels = {a["id"]: f"#{a['id']} {a['company'] or '?'} · {a['role']} ({a['status']})" for a in apps}
    ids = list(labels)
    active = st.session_state.get("active")  # the one open on the Apply page
    chosen = st.selectbox("Application", ids, ids.index(active) if active in labels else 0, format_func=labels.get,
                          label_visibility="collapsed")
    application_card(memory.get(chosen), f"h{chosen}")
    timeline = memory.events(chosen)
    if timeline:
        with st.expander(f"Timeline ({len(timeline)})"):
            st.markdown(bullets(f"{stamp(e['at'])}: {e['detail'] or e['kind']}" for e in reversed(timeline)))
    st.divider()

entries = files.history()

# ---------------------------------------------------------------- generated (rendered or not)

generated = memory.drafts()
st.subheader("Generated CVs + cover letters")
st.caption("Every draft the writer produced, with when it was written. Regenerating adds a new one; earlier ones stay.")
if not generated:
    st.info("Each CV + cover letter you generate appears here, even before you create the Word files.")
else:
    profile = load_profile_or_stop()
    latest = {}  # app id -> its newest draft id
    for d in generated:
        latest.setdefault(d["app_id"], d["id"])
    for i, d in enumerate(generated):
        renders = sorted(e["rendered_at"] for e in entries if e.get("app_id") == d["app_id"] and e.get("rendered_at", "") >= d["at"])
        state = f"✅ Word files created {stamp(renders[0])}" if renders else "📝 No Word files yet"
        with st.expander(f"{stamp(d['at'])} · {d['company'] or '?'} · {d['role']} · {state}", expanded=i == 0):
            app = memory.get(d["app_id"])
            # The newest draft of an application carries the edits you made since; older ones render as written.
            edited = d["id"] == latest[d["app_id"]] and app["final"]
            draft = app["final"] if edited else d["draft"]
            key = f"gen{d['id']}"
            render_now = st.button("Create Word files" + (" (with your edits)" if edited else ""), key=key, type="primary")
            if render_now or blocked_panel(key):
                out = render_checked(app, draft, profile, key, override=not render_now, make_pdf=True)
                if out:
                    memory.update(app["id"], files=out, status="ready" if app["status"] == "draft" else app["status"])
                st.rerun()
            t1, t2 = st.tabs(["CV", "Cover letter"])
            with t1:
                for section in cv_text(draft, profile):
                    if section["bullets"]:
                        st.markdown(f"**{safe(section['heading'])}**")
                        st.markdown(bullets(section["bullets"]))
            with t2:
                paragraphs = [p["text"].replace("**", "") for p in draft["cover_letter"]]
                for par in paragraphs:
                    st.markdown(safe(par))
                with st.popover("Copy text"):
                    st.code("\n\n".join(paragraphs), language=None, wrap_lines=True)
st.divider()

# ---------------------------------------------------------------- rendered

st.subheader("Rendered documents")
st.caption("Every render is saved as a new version in output/. Your fact bank and templates are never changed by it.")

if not entries:
    st.info("Rendered CVs and cover letters will appear here, one entry per render.")
    st.stop()

BADGES = {"verified": "✅ Verified", "warnings": "🟡 Warnings", "unverified": "❔ AI check didn't run",
          "errors": "⛔ Has errors", None: "❔ Not checked"}


def open_flags(e: dict, kind: str | None = None) -> list[dict]:
    return [f for f in e.get("flags") or [] if not f["resolved"] and kind in (None, f.get("doc"))]


def badge(e: dict, kind: str) -> str:
    ev = e.get("evaluation") or {}
    text = "⛔ Rendered despite errors" if ev.get("overridden") else BADGES.get(ev.get("status"))
    n = len(open_flags(e, kind))
    return text + (f" · 🚩 {n} flag(s)" if n else "")


flagged = sum(len(open_flags(e)) for e in entries)
risky = sum((e.get("evaluation") or {}).get("status") != "verified" for e in entries)
c1, c2, c3 = st.columns(3)
c1.metric("Renders", len(entries))
c2.metric("Not fully verified", risky, help="Errors overridden, warnings, or the AI fact-check didn't run.")
c3.metric("Open flags", flagged)
only = st.toggle("Only show renders that need a look", value=False)

companies = sorted({e.get("company", "") for e in entries})
pick = st.multiselect("Company", companies, placeholder="All companies")
shown = [e for e in entries if (not pick or e.get("company") in pick)
         and (not only or (e.get("evaluation") or {}).get("status") != "verified" or open_flags(e))]


def when(e: dict) -> str:
    return stamp(e.get("rendered_at"))


def previous(e: dict, kind: str) -> dict | None:
    """The render before this one for the same application, to show what changed."""
    same = [x for x in entries if x["folder"].parent == e["folder"].parent and x.get(kind)]
    later = [x for x in same if x.get("rendered_at", "") < e.get("rendered_at", "")]
    return later[0] if later else None


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


t_cv, t_cl = st.tabs([f"CVs ({sum(bool(e.get('cv')) for e in shown)})",
                      f"Cover letters ({sum(bool(e.get('cover_letter')) for e in shown)})"])

with t_cv:
    for i, e in enumerate(x for x in shown if x.get("cv")):
        with st.expander(f"{when(e)} · {e.get('company', '')} · {e.get('role', '')} · {badge(e, 'cv')}", expanded=i == 0):
            downloads(e, "cv", f"cv{i}")
            review(e, "cv", f"cv{i}")
            prev = previous(e, "cv")
            diff = prev and st.toggle("Show changes since the previous render", key=f"cvd{i}")
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
    for i, e in enumerate(x for x in shown if x.get("cover_letter")):
        with st.expander(f"{when(e)} · {e.get('company', '')} · {e.get('role', '')} · {badge(e, 'cl')}", expanded=i == 0):
            downloads(e, "cl", f"cl{i}")
            review(e, "cl", f"cl{i}")
            prev = previous(e, "cover_letter")
            if prev and st.toggle("Show changes since the previous render", key=f"cld{i}"):
                st.markdown(word_diff("\n\n".join(prev["cover_letter"]), "\n\n".join(e["cover_letter"])), unsafe_allow_html=True)
            else:
                for par in e["cover_letter"]:
                    st.markdown(safe(par))
            with st.popover("Copy text"):
                st.code("\n\n".join(e["cover_letter"]), language=None, wrap_lines=True)
