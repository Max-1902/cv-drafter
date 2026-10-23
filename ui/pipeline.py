"""Pipeline: track every application, follow up, prep interviews, learn from your edits."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from cvdrafter import insights, learn, memory, prep
from cvdrafter.llm import LLMError
from cvdrafter.preview import word_diff
from cvdrafter.textutil import safe, safe_html
from ui.common import blocked_panel, bullets, document_previews, download_buttons, match_panel, load_profile_or_stop, models, render_checked, setup

setup()
M = models()
st.title("Pipeline")

apps = memory.list_all()
if not apps:
    st.info("No applications yet. Paste a posting on the Apply page.")
    st.stop()

# ---------------------------------------------------------------- board

f1, f2 = st.columns([2, 3])
query = f1.text_input("Search", placeholder="company or role").lower()
shown = [a for a in apps if query in f"{a['company']} {a['role']}".lower()]
due = {a["id"] for a in insights.follow_ups_due(apps)}
if due:
    f2.warning(f" {len(due)} follow-up(s) due: " + ", ".join(f"\\#{a['id']} {safe(a['company'])}" for a in apps if a["id"] in due))

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

st.divider()

# ---------------------------------------------------------------- detail

labels = {a["id"]: f"#{a['id']} {a['company']} · {a['role']} ({a['status']})" for a in shown}
if not labels:
    st.stop()
app = memory.get(st.selectbox("Application", list(labels), format_func=labels.get))

head, actions = st.columns([3, 2])
head.subheader(f"{safe(app['company'])} · {safe(app['role'])}")
if str(app.get("url") or "").startswith(("https://", "http://")):
    head.link_button("Open posting", app["url"])
if actions.button("Open in editor", type="primary"):
    st.session_state["open_app_id"] = app["id"]
    st.switch_page("ui/new_app.py")

t_track, t_docs, t_prep, t_learn, t_post = st.tabs(["Tracking", "Documents", "Interview prep", "Learn from edits", "Posting"])

with t_track:
    details = [app.get(k) for k in memory.JOB_FIELDS if app.get(k)]
    if details:
        st.caption(" · ".join(safe(d) for d in details))
    with st.form(f"track_{app['id']}"):
        c1, c2, c3 = st.columns(3)
        status = c1.selectbox("Status", memory.STATUSES, index=memory.STATUSES.index(app["status"]))
        sent = c2.date_input("Sent on", value=date.fromisoformat(app["sent_at"][:10]) if app.get("sent_at") else None)
        follow = c3.date_input("Follow up on", value=date.fromisoformat(app["follow_up_at"][:10]) if app.get("follow_up_at") else None,
                               help="Defaults to 7 days after sending.")
        url = st.text_input("Posting URL", app.get("url") or "")
        notes = st.text_area("Notes (contacts, recruiter names, interview dates…)", app["user_notes"] or "", height=120)
        if st.form_submit_button("Save"):
            if status == "sent" and not sent:
                sent = date.today()
            if sent and not follow and status in ("sent",):
                follow = sent + timedelta(days=7)
            memory.update(app["id"], status=status, user_notes=notes, url=url,
                          sent_at=sent.isoformat() if sent else None, follow_up_at=follow.isoformat() if follow else None)
            st.toast("Saved")
            st.rerun()
    if app["status"] in ("sent", "interview") and st.button("Followed up, remind me again in 7 days"):
        memory.update(app["id"], follow_up_at=(date.today() + timedelta(days=7)).isoformat())
        st.rerun()
    if app["status"] in ("draft", "ready") and st.button("I applied today"):
        memory.mark_applied(app["id"])
        st.rerun()
    timeline = memory.events(app["id"])
    if timeline:
        st.markdown("**Timeline**")
        st.markdown(bullets(f"{e['at'].replace('T', ' ')[:16]}: {e['detail'] or e['kind']}" for e in timeline))

with t_docs:
    draft = app["final"] or app["generated"]
    if not download_buttons(app["files"], f"pl{app['id']}"):
        if not draft:
            st.caption("Nothing generated yet. Open in editor to write the CV and cover letter.")
        else:
            render_now = st.button("Create Word files (.docx)", type="primary", key=f"render_{app['id']}")
            if blocked_panel(f"pl{app['id']}") or render_now:
                files = render_checked(app, draft, load_profile_or_stop(), f"pl{app['id']}", override=not render_now)
                if files:
                    memory.update(app["id"], files=files, status="ready" if app["status"] == "draft" else app["status"])
                st.rerun()
    if draft and draft.get("cover_letter"):
        with st.expander("Cover letter text (copy for online forms)"):
            st.code("\n\n".join(p["text"] for p in draft["cover_letter"]), language=None, wrap_lines=True)
    if draft:
        if app.get("analysis"):
            with st.sidebar.expander("Posting ↔ your CV: exact matches", expanded=False):
                match_panel(draft, app["analysis"], load_profile_or_stop(), key=f"pl{app['id']}")
        document_previews(app, load_profile_or_stop())

with t_prep:
    st.caption("Likely questions with answers built only from your facts, claims they'll probe, and questions to ask them.")
    if st.button("Generate interview prep" if not app.get("prep") else "Regenerate interview prep"):
        with st.spinner("Preparing…"):
            try:
                memory.update(app["id"], prep=prep.interview_prep(app, load_profile_or_stop(), M["writer"]))
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

with t_learn:
    if not (app["generated"] and app["final"]):
        st.caption("Edit and save a draft first. The app learns from the differences between its draft and what you sent.")
    else:
        pairs = learn.edit_pairs(app["generated"], app["final"])
        st.caption(f"You changed {len(pairs)} text(s) before sending.")
        for loc, before, after in pairs[:8]:
            st.markdown(f"<div class='card'><div class='m'>{safe_html(loc)}</div>{word_diff(before, after)}</div>", unsafe_allow_html=True)
        if pairs and st.button("Propose style rules from these edits"):
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
            if proposed and st.button("Add selected rules to style_rules.md"):
                learn.append_rules(chosen, f"application #{app['id']} ({app['company']})")
                st.success(f"Added {len(chosen)} rule(s). Every future draft follows them.")

with t_post:
    st.text(app["jd"])
    with st.popover("Delete application"):
        st.write("This removes it from the history (documents in output/ stay).")
        if st.button("Delete permanently", type="primary"):
            memory.delete(app["id"])
            st.rerun()
