"""Applications: every application in one place. A list (or board, or editable table) with the state of each CV and
cover letter; open one to see its latest CV and cover letter first, then its versions, tracking, interview prep,
learning from your edits and the posting."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from cvdrafter import docstate, files, fit, insights, learn, memory, prep
from cvdrafter.llm import LLMError
from cvdrafter.preview import word_diff
from cvdrafter.textutil import safe, safe_html
from ui.common import (FIT_COLOR, applied_box, application_card, blocked_panel, bullets, document_previews, download_buttons, fit_card,
                       load_profile_or_stop, match_panel, models, render_checked, setup)
from ui import versions

setup()
M = models()
profile = load_profile_or_stop()
VIEWS = ["List", "Board", "Table"]


def open_app(app_id: int) -> None:
    st.query_params["app"] = str(app_id)


def close_app() -> None:
    st.query_params.pop("app", None)


def doc_cell(col, app: dict, renders: list[dict], kind: str) -> None:
    """The state of one document in a list row: the check of its newest render, or 'Draft' / '–'."""
    state = docstate.state(app, renders, kind)
    flags = len(docstate.open_flags(renders[0], kind)) if renders else 0
    col.markdown(docstate.LABELS[state] + (f" · 🚩{flags}" if flags else ""))


def latest_activity(app: dict, renders: list[dict], activity: dict[int, str]) -> str:
    """The newest of: last logged event, last render, the day it was added."""
    return max(activity.get(app["id"], ""), renders[0].get("rendered_at", "") if renders else "", app["created_at"] or "")


def fit_badge(col, app: dict) -> None:
    f = fit.assess(app.get("analysis") or {}, profile)
    col.badge(f["verdict"] + (f" {f['score']}" if f["score"] is not None else ""), color=FIT_COLOR[f["level"]])


# ---------------------------------------------------------------- list, board, table

LIST_COLS = [3.1, 1.4, 1.7, 1.7, 1.3, 1.1, 0.9]


def list_view(shown: list[dict], due: set[int], renders: dict, activity: dict) -> None:
    head = st.columns(LIST_COLS)
    for col, label in zip(head, ("Company · Role", "Status", "CV", "Letter", "Fit", "Latest activity", "")):
        col.caption(label)
    for a in shown:
        mine = renders.get(a["id"], [])
        with st.container(border=True):
            c = st.columns(LIST_COLS, vertical_alignment="center")
            c[0].markdown(f"**{safe(a['company']) or 'Company to confirm'}** · {safe(a['role'])}  \n\\#{a['id']}")
            c[1].markdown(a["status"].title() + ("  \n:red[**Follow-up due**]" if a["id"] in due else ""))
            doc_cell(c[2], a, mine, "cv")
            doc_cell(c[3], a, mine, "cl")
            fit_badge(c[4], a)
            c[5].markdown(docstate.when(latest_activity(a, mine, activity)))
            c[6].button("Open", key=f"open_{a['id']}", on_click=open_app, args=(a["id"],), width="stretch")


def board_view(shown: list[dict], due: set[int]) -> None:
    board = st.columns(len(memory.STATUSES))
    for col, status in zip(board, memory.STATUSES):
        group = [a for a in shown if a["status"] == status]
        col.markdown(f"**{status.title()}** · {len(group)}")
        for a in group:
            cov = insights.coverage(a)
            meta = f"{a['created_at'][:10]}" + (f" · {cov:.0%} must-haves" if cov is not None else "")
            follow_up = " · Follow-up due" if a["id"] in due else ""
            col.markdown(f'<div class="card"><div class="t">#{a["id"]} {safe_html(a["company"])}{follow_up}</div>'
                         f'<div class="m">{safe_html(a["role"])}<br>{meta}</div></div>', unsafe_allow_html=True)
            col.button("Open", key=f"open_{a['id']}", on_click=open_app, args=(a["id"],), width="stretch")


LOG_COLS = ["id", "company", "role", "status", "sent_at", "follow_up_at", "location", "work_mode", "employment_type",
            "salary", "deadline", "job_ref", "url", "company_confidence", "created_at"]
LOG_EDITABLE = [c for c in LOG_COLS if c not in ("id", "created_at", "company_confidence")]
LOG_DATES = ("sent_at", "follow_up_at")


def to_date(v):
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


def table_view(shown: list[dict]) -> None:
    """Every application in one editable table, with CSV export."""
    st.caption("Edit any cell to correct it. Company, role, location, pay and deadline come from the posting.")
    rows = [{c: (to_date(a.get(c)) if c in LOG_DATES else a.get(c) or "") for c in LOG_COLS} for a in shown]
    for r in rows:
        r["created_at"] = r["created_at"][:10]
        r["company_confidence"] = {"high": "Confirmed", "medium": "Review", "low": "Review"}.get(r["company_confidence"], "")
    edited = st.data_editor(
        rows, key="log_table", hide_index=True, width="stretch", disabled=["id", "created_at", "company_confidence"],
        column_config={
            "id": st.column_config.NumberColumn("#", width="small"),
            "status": st.column_config.SelectboxColumn("status", options=memory.STATUSES, required=True),
            "sent_at": st.column_config.DateColumn("applied on"),
            "follow_up_at": st.column_config.DateColumn("follow up"),
            "url": st.column_config.LinkColumn("url"),
            "company_confidence": st.column_config.TextColumn("company ok?", width="small",
                                                              help="How sure the app is about the company name."),
            "created_at": st.column_config.TextColumn("logged"),
        },
    )
    changes = []
    for old, new in zip(rows, edited):
        diff = {c: new[c] for c in LOG_EDITABLE if new[c] != old[c]}
        if diff:
            changes.append((old["id"], diff))
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button(f"Save {len(changes)} change(s)", type="primary", disabled=not changes):
        for app_id, diff in changes:
            fields = {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in diff.items()}
            if "company" in fields:
                fields["company_confidence"] = "high"  # you confirmed it
            memory.update(app_id, **fields)
        st.toast("Saved")
        st.rerun()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=LOG_COLS)
    writer.writeheader()
    writer.writerows({c: a.get(c) or "" for c in LOG_COLS} for a in shown)
    c2.download_button("Export CSV", buf.getvalue(), file_name=f"applications_{date.today().isoformat()}.csv", mime="text/csv")


def overview(apps: list[dict]) -> None:
    st.title("Applications")
    if not apps:
        st.info("No applications yet. Paste a posting under New application.")
        st.stop()
    due = {a["id"] for a in insights.follow_ups_due(apps)}
    renders = docstate.by_app(files.history())
    activity = memory.last_activity()
    f1, f2, f3, f4 = st.columns([2.4, 1.8, 2, 1.8])
    query = f1.text_input("Search", placeholder="company, role, location…").lower()
    statuses = f2.multiselect("Status", memory.STATUSES, placeholder="All statuses")
    docs = f3.multiselect("Documents", docstate.FILTERS, placeholder="Any state",
                          help="Verified: CV and letter passed every check. Needs a look: warnings, errors, an AI check that "
                               "didn't run, or open flags.")
    view = f4.segmented_control("View", VIEWS, default="List", key="apps_view") or "List"
    shown = [a for a in apps if a["status"] in (statuses or memory.STATUSES)
             and (not docs or docstate.summary(a, renders.get(a["id"], [])) in docs)
             and query in " ".join(str(a.get(c) or "") for c in LOG_COLS).lower()]
    shown.sort(key=lambda a: a["id"] not in due)  # follow-ups due first; the rest stays newest first
    if due:
        st.warning(f"{len(due)} follow-up(s) due: " + ", ".join(f"\\#{a['id']} {safe(a['company'])}" for a in apps if a["id"] in due))
    if not shown:
        st.caption("No application matches.")
    elif view == "Board":
        board_view(shown, due)
    elif view == "Table":
        table_view(shown)
    else:
        list_view(shown, due, renders, activity)


# ---------------------------------------------------------------- one application

def documents(app: dict, renders: list[dict]) -> None:
    """The current CV and cover letter: download buttons first, the preview one click away."""
    draft = app["final"] or app["generated"]
    key = f"app{app['id']}"
    with st.container(border=True):
        st.markdown("**CV and cover letter**")
        if renders:
            c1, c2, c3 = st.columns([2, 2, 3])
            c1.markdown(f"CV: {docstate.label(renders[0], 'cv')}")
            c2.markdown(f"Letter: {docstate.label(renders[0], 'cl')}")
            c3.caption(f"Latest Word files {versions.stamp(renders[0].get('rendered_at'))} · {len(renders)} version(s) under Versions")
        if not download_buttons(app["files"], key, app["id"], can_pull=True):
            if not draft:
                st.caption("Nothing generated yet. Open in editor to write the CV and cover letter.")
            else:
                render_now = st.button("Create Word files (.docx)", type="primary", key=f"render_{app['id']}")
                if blocked_panel(key) or render_now:
                    files = render_checked(app, draft, profile, key, override=not render_now)
                    if files:
                        memory.update(app["id"], files=files, status="ready" if app["status"] == "draft" else app["status"])
                    st.rerun()
        if draft:
            if app.get("analysis"):
                with st.sidebar.expander("Posting ↔ your CV: exact matches", expanded=False):
                    match_panel(draft, app["analysis"], profile, key=key)
            with st.expander("Preview CV and cover letter"):
                document_previews(app, profile)
            if draft.get("cover_letter"):
                with st.expander("Cover letter text (copy for online forms)"):
                    st.code("\n\n".join(p["text"] for p in draft["cover_letter"]), language=None, wrap_lines=True)


def tracking(app: dict) -> None:
    application_card(app, f"a{app['id']}")
    applied_box(app, f"a{app['id']}")
    if app["status"] in ("sent", "interview") and st.button("Followed up, remind me again in 7 days", key=f"fu_{app['id']}"):
        memory.update(app["id"], follow_up_at=(date.today() + timedelta(days=memory.FOLLOW_UP_DAYS)).isoformat())
        st.rerun()
    notes = st.text_area("Notes (contacts, recruiter names, interview dates…)", app["user_notes"] or "", height=120,
                         key=f"notes_{app['id']}")
    if st.button("Save notes", key=f"save_notes_{app['id']}", disabled=notes == (app["user_notes"] or "")):
        memory.update(app["id"], user_notes=notes)
        st.toast("Saved")
        st.rerun()
    timeline = memory.events(app["id"])
    if timeline:
        st.markdown("**Timeline**")
        st.markdown(bullets(f"{e['at'].replace('T', ' ')[:16]}: {e['detail'] or e['kind']}" for e in timeline))


def interview_prep(app: dict) -> None:
    st.caption("Likely questions with answers built only from your facts, claims they'll probe, and questions to ask them.")
    if st.button("Generate interview prep" if not app.get("prep") else "Regenerate interview prep", key=f"prep_{app['id']}"):
        with st.spinner("Preparing…"):
            try:
                memory.update(app["id"], prep=prep.interview_prep(app, profile, M["writer"]))
                st.rerun()
            except LLMError as e:
                st.error(str(e))
    p = app.get("prep")
    if p:
        for q in p.get("questions", []):
            with st.expander(safe(q.get("question", ""))):
                st.caption(safe(q.get("why_they_ask", "")))
                st.markdown(bullets(q.get("answer_points")))
                if q.get("fact_ids"):
                    st.caption("Facts: " + safe(", ".join(map(str, q["fact_ids"]))))
                if q.get("gap"):
                    st.warning(f"Gap: {safe(q['gap'])}")
        if p.get("defend"):
            st.markdown("**Be ready to defend**")
            for d in p["defend"]:
                st.markdown(f"- **{safe(d.get('claim', ''))}**: {safe(d.get('be_ready_to_explain', ''))}")
        if p.get("ask_them"):
            st.markdown("**Ask them**")
            st.markdown(bullets(p["ask_them"]))


def learn_from_edits(app: dict) -> None:
    if not (app["generated"] and app["final"]):
        st.caption("Edit and save a draft first. The app learns from the differences between its draft and what you sent.")
        return
    pairs = learn.edit_pairs(app["generated"], app["final"])
    st.caption(f"You changed {len(pairs)} text(s) before sending.")
    for loc, before, after in pairs[:8]:
        st.markdown(f"<div class='card'><div class='m'>{safe_html(loc)}</div>{word_diff(before, after)}</div>", unsafe_allow_html=True)
    if pairs and st.button("Propose style rules from these edits", key=f"propose_{app['id']}"):
        with st.spinner("Thinking…"):
            try:
                st.session_state[f"rules_{app['id']}"] = learn.propose_rules(app["generated"], app["final"], M["writer"])
            except LLMError as e:
                st.error(str(e))
    proposed = st.session_state.get(f"rules_{app['id']}")
    if proposed is not None:
        if not proposed:
            st.info("Nothing general to learn from these edits.")
        chosen = [r for i, r in enumerate(proposed) if st.checkbox(safe(r), True, key=f"rule_{app['id']}_{i}")]
        if proposed and st.button("Add selected rules to style_rules.md", key=f"add_rules_{app['id']}"):
            learn.append_rules(chosen, f"application #{app['id']} ({app['company']})")
            st.success(f"Added {len(chosen)} rule(s). Every future draft follows them.")


def posting(app: dict) -> None:
    st.text(app["jd"])
    with st.popover("Delete application"):
        st.write("This removes it from the history (documents in output/ stay).")
        if st.button("Delete permanently", type="primary", key=f"delete_{app['id']}"):
            memory.delete(app["id"])
            close_app()
            st.rerun()


def detail(app: dict) -> None:
    renders = docstate.by_app(files.history()).get(app["id"], [])
    st.button("← All applications", on_click=close_app)
    head, actions = st.columns([3, 2], vertical_alignment="center")
    head.subheader(f"{safe(app['company']) or 'Company to confirm'} · {safe(app['role'])}")
    with actions:
        a1, a2 = st.columns(2)
        if a1.button("Open in editor", type="primary", width="stretch"):
            st.session_state["open_app_id"] = app["id"]
            st.switch_page("ui/new_app.py")
        if str(app.get("url") or "").startswith(("https://", "http://")):
            a2.link_button("Open posting", app["url"], width="stretch")
    documents(app, renders)
    t_track, t_vers, t_fit, t_prep, t_learn, t_post = st.tabs(["Tracking", "Versions", "Fit", "Interview prep", "Learn from edits", "Posting"])
    with t_track:
        tracking(app)
    with t_vers:
        versions.panel(app, renders, profile)
    with t_fit:
        if app.get("analysis"):
            fit_card(app["analysis"], profile, f"a{app['id']}")
        else:
            st.caption("No posting saved for this application, so there is nothing to score.")
    with t_prep:
        interview_prep(app)
    with t_learn:
        learn_from_edits(app)
    with t_post:
        posting(app)


selected = st.query_params.get("app")
app = memory.get(int(selected)) if str(selected).isdigit() else None
if selected and not app:
    close_app()
if app:
    detail(app)
else:
    overview(memory.list_all())
