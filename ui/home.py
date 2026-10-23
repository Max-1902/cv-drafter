"""Overview: a calm command center for the job search."""
from __future__ import annotations

import streamlit as st

from cvdrafter import insights, memory
from cvdrafter import profile as prof
from cvdrafter.textutil import safe, safe_html
from ui.common import hbar, kpi, load_profile_or_stop, pills, setup

setup()
profile = load_profile_or_stop()
apps = memory.list_all()
stats = memory.stats()
first = profile["personal"].get("name", "").split(" ")[0]

st.markdown('<div class="eyebrow">YOUR JOB SEARCH WORKSPACE</div>', unsafe_allow_html=True)
st.markdown(f'<div class="hero"><div class="hero-title">Application overview</div>'
            f'<div class="hero-copy">{safe_html(first)} · Track applications, follow-ups and outcomes. Create role-specific CVs and cover letters from verified profile facts.</div></div>',
            unsafe_allow_html=True)
action, helper = st.columns([1, 4])
if action.button("New application", type="primary", width="stretch"):
    st.switch_page("ui/chat.py")
helper.caption("Analyse a job description, review fit, and generate tailored application documents from your profile.")

c = st.columns(4)
kpi(c[0], "Applications", stats["total"], f'{sum(a["status"] == "draft" for a in apps)} in progress')
kpi(c[1], "Applied", stats["sent"], "applications sent")
kpi(c[2], "Interviews", stats["by_status"]["interview"] + stats["by_status"]["offer"], "including offers")
kpi(c[3], "Interview rate", f'{stats["interview_rate"]:.0%}' if stats["interview_rate"] is not None else "–", "of sent applications")

due = insights.follow_ups_due(apps)
if due:
    st.warning("**Follow-ups to send:** " + ", ".join(f'#{a["id"]} {safe(a["company"])}' for a in due))

left, right = st.columns([1.55, 1], gap="large")
with left:
    st.subheader("Recent applications")
    if not apps:
        st.markdown('<div class="card"><div class="t">Your workspace is ready</div><div class="m">Start with a posting. We’ll analyse the role, check your fit and prepare a tailored draft.</div></div>', unsafe_allow_html=True)
    else:
        for app in apps[:6]:
            status = app["status"].replace("_", " ").title()
            coverage = insights.coverage(app)
            detail = f'{status} · {app["created_at"][:10]}'
            if coverage is not None:
                detail += f' · {coverage:.0%} must-have coverage'
            st.markdown(f'<div class="card"><div class="t">{safe_html(app["company"] or "Company to confirm")} <span style="color:#91a0ae;font-weight:500">· {safe_html(app["role"])}</span></div><div class="m">{safe_html(detail)}</div></div>', unsafe_allow_html=True)
        if st.button("View application pipeline"):
            st.switch_page("ui/pipeline.py")
    st.subheader("Application activity")
    weekly = insights.weekly_counts(apps)
    if any(w["applications"] for w in weekly):
        import altair as alt
        chart = alt.Chart(alt.Data(values=weekly)).mark_bar(color="#147d72", cornerRadiusEnd=5, size=20).encode(
            x=alt.X("week:O", title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("applications:Q", title=None, axis=alt.Axis(tickMinStep=1, gridOpacity=.18)),
            tooltip=["week:O", "applications:Q"],
        ).properties(height=190)
        st.altair_chart(chart, width="stretch")
    else:
        st.caption("Your weekly activity will appear here once you add applications.")

with right:
    st.subheader("Your profile at a glance")
    facts = prof.index_facts(profile)
    st.markdown(f'<div class="card"><div class="t">{len(facts)} citeable facts</div><div class="m">{len(profile["experience"])} roles · {len(profile["projects"])} projects · {sum(len(v) for v in profile["skills"].values())} skills · {len(prof.load_learned())} learned facts</div></div>', unsafe_allow_html=True)
    problems = prof.validate_profile(profile)
    for problem in problems:
        st.warning(problem)
    with st.expander("How your fact bank works"):
        st.write("Every CV bullet and cover letter claim is tied to a fact you provided. Add experience or correct a detail on the Your profile page.")
        if st.button("Open fact bank"):
            st.switch_page("ui/files_page.py")
    st.subheader("Skills in demand")
    top = insights.keyword_demand(apps)[:12]
    if top:
        st.markdown(pills([k["term"] for k in top if k["in_profile"]], "ok") + " " +
                    pills([k["term"] for k in top if not k["in_profile"]], "no"), unsafe_allow_html=True)
    else:
        st.caption("As you analyse postings, frequently requested skills will show here.")
    st.subheader("Opportunity gaps")
    st.caption("Skills requested across postings that may be worth adding to your profile.")
    hbar(insights.gap_radar(apps, 7), "term", "postings", "postings asking for it")
