"""Insights: what each direction you apply to asks for, what you have, what to build next, and what works."""
from __future__ import annotations

import altair as alt
import streamlit as st

from cvdrafter import insights, memory
from cvdrafter import profile as prof
from cvdrafter.llm import LLMError
from cvdrafter.textutil import safe
from ui.common import hbar, kpi, load_profile_or_stop, models, setup

setup()
st.title("Insights")
apps = memory.list_all()
analysed = [a for a in apps if a["analysis"]]
if not analysed:
    st.info("Insights appear once you've analysed a few postings.")
    st.stop()

profile = load_profile_or_stop()
facts = prof.index_facts(profile)
M = models()
HAVE, GAP = "You have it", "Gap"
COLORS = alt.Scale(domain=[HAVE, GAP], range=["#3987e5", "#d95926"])  # validated for CVD on the dark surface
INK, MUTED, SURFACE = "#e5edf4", "#91a0ae", "#0b1117"
ALL = "All directions"


@st.cache_data(show_spinner=False)
def demand(ids: tuple, model: str) -> tuple[dict, bool]:
    """Recomputed only when an application is added or changed (ids carries each one's id + analysis size)."""
    rows = [a for a in memory.list_all() if a["analysis"]]
    mapping, mapped = insights.skill_map(rows, model)
    text = prof.corpus(prof.load_profile())
    by_dir = insights.direction_demand(rows, mapping, text)
    by_dir[ALL] = insights.direction_demand(rows, mapping, text, group=lambda a: ALL)[ALL]
    return by_dir, mapped


def demand_chart(rows: list[dict], n: int) -> None:
    data = [{"skill": r["skill"], "share": round(100 * r["share"]), "have": HAVE if r["have"] else GAP,
             "postings": f"{r['postings']} of {n}", "companies": ", ".join(dict.fromkeys(r["companies"])),
             "spellings": ", ".join(r["spellings"])} for r in rows]
    base = alt.Chart(alt.Data(values=data)).encode(
        y=alt.Y("skill:N", sort=[d["skill"] for d in data], title=None,
                axis=alt.Axis(labelLimit=240, labelOverlap=False, ticks=False, domain=False, labelColor=INK)),
        x=alt.X("share:Q", title="% of postings asking for it", scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(grid=True, gridOpacity=0.15, tickCount=5, labelColor=MUTED, titleColor=MUTED)),
        tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("share:Q", title="% of postings"),
                 alt.Tooltip("postings:N", title="Postings"), alt.Tooltip("have:N", title="You"),
                 alt.Tooltip("companies:N", title="Asked by"), alt.Tooltip("spellings:N", title="As written")],
    )
    bars = base.mark_bar(size=12, cornerRadiusEnd=4).encode(
        color=alt.Color("have:N", scale=COLORS, title=None, legend=alt.Legend(orient="top", labelColor=INK)))
    labels = base.mark_text(align="left", dx=5, fontSize=11, color=MUTED).encode(text=alt.Text("share:Q", format=".0f"))
    st.altair_chart((bars + labels).properties(height=alt.Step(24)).configure_view(stroke=None), width="stretch")


def compare_chart(by_dir: dict, names: list[str], top: int = 14) -> None:
    """Top skills (rows) x directions (columns): the share of each direction's postings asking for it."""
    best: dict[str, float] = {}
    for d in names:
        for r in by_dir[d]["skills"]:
            if r["category"] not in insights.NOT_BUILDABLE:
                best[r["skill"]] = max(best.get(r["skill"], 0), r["share"])
    skills = sorted(best, key=lambda s: -best[s])[:top]
    data = []
    for d in names:
        share = {r["skill"]: r for r in by_dir[d]["skills"]}
        for s in skills:
            r = share.get(s)
            data.append({"skill": s, "direction": f"{d} ({by_dir[d]['postings']})", "share": round(100 * r["share"]) if r else 0,
                         "have": (HAVE if r["have"] else GAP) if r else "", "label": f"{round(100 * r['share'])}%" if r else ""})
    base = alt.Chart(alt.Data(values=data)).encode(
        x=alt.X("direction:N", title=None, axis=alt.Axis(orient="top", labelAngle=0, labelColor=INK, labelLimit=200, ticks=False, domain=False)),
        y=alt.Y("skill:N", sort=skills, title=None, axis=alt.Axis(labelColor=INK, ticks=False, domain=False, labelLimit=220)),
        tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("direction:N", title="Direction"),
                 alt.Tooltip("share:Q", title="% of postings"), alt.Tooltip("have:N", title="You")],
    )
    cells = base.mark_rect(cornerRadius=3, stroke=SURFACE, strokeWidth=2).encode(
        color=alt.Color("share:Q", scale=alt.Scale(domain=[0, 100], range=["#141f2a", "#3987e5"]), legend=None))
    text = base.mark_text(fontSize=11, color=INK).encode(text="label:N")
    st.altair_chart((cells + text).properties(height=alt.Step(26)).configure_view(stroke=None), width="stretch")


# ---------------------------------------------------------------- what each direction asks for

ids = tuple((a["id"], len(str(a["analysis"]))) for a in analysed)
with st.spinner("Grouping posting keywords into skills…"):
    by_dir, mapped = demand(ids, M["fast"])
if not mapped:
    demand.clear()  # don't keep a half-grouped result; the next load retries the model
names = [d for d in insights.DIRECTIONS.values() if d in by_dir]

st.subheader("What each direction asks for")
st.caption("Every posting you paste is sorted into a direction. For each skill: the share of that direction's postings "
           "asking for it (required or preferred). Degrees, job titles and soft skills are left out of the charts.")
if not mapped:
    st.caption("Some keywords aren't grouped yet (the chat-commands model didn't answer), so near-duplicates may show "
               "separately. Reload the page to retry.")
tiles = st.columns(len(names))
for col, d in zip(tiles, names):
    top = [r for r in by_dir[d]["skills"] if r["category"] not in insights.NOT_BUILDABLE][:10]
    kpi(col, d, by_dir[d]["postings"], f"posting(s) · you have {sum(r['have'] for r in top)}/{len(top)} of its top skills")

pick = st.segmented_control("Direction", [ALL, *names], default=max(names, key=lambda d: by_dir[d]["postings"]),
                            label_visibility="collapsed", key="ins_dir") or ALL
d = by_dir[pick]
rows = [r for r in d["skills"] if r["category"] not in insights.NOT_BUILDABLE]
if d["postings"] < 5:
    st.caption(f"Only {d['postings']} posting(s) here: every skill is still near 100% or 1-in-{d['postings']}. "
               "The averages become meaningful from about 5 postings.")

left, right = st.columns([3, 2], gap="large")
with left:
    st.markdown(f"**Most asked for · {pick}** ({d['postings']} posting{'s' if d['postings'] != 1 else ''})")
    demand_chart(rows[:20], d["postings"])
with right:
    gaps = [r for r in rows if not r["have"]][:8]
    st.markdown("**Build next: most-asked skills you don't show yet**")
    if not gaps:
        st.success("Your profile covers every skill these postings ask for.")
    for r in gaps:
        st.markdown(f"- **{safe(r['skill'])}** · {r['share']:.0%} of postings "
                    f"<small>({safe(', '.join(dict.fromkeys(r['companies'])))})</small>", unsafe_allow_html=True)
    ideas_key = ("ideas", pick, tuple(r["skill"] for r in gaps))
    if gaps and st.button("Suggest projects that close these gaps", type="primary"):
        with st.spinner("Thinking of projects…"):
            try:
                st.session_state[ideas_key] = insights.suggest_projects(pick, d, profile, M["writer"])
            except LLMError as e:
                st.error(str(e))
    for p in st.session_state.get(ideas_key, []):
        with st.container(border=True):
            st.markdown(f"**{safe(p['title'])}** · ~{safe(p.get('weeks', '?'))} weeks")
            st.markdown(safe(p.get("pitch", "")))
            st.caption("Shows: " + safe(", ".join(map(str, p.get("skills") or []))))
            if p.get("first_step"):
                st.caption(f"First step: {safe(p['first_step'])}")

if len(names) > 1:
    st.markdown("**Directions compared** · share of each direction's postings asking for the skill")
    compare_chart(by_dir, names)

with st.expander("Soft skills and other requirements"):
    soft = [r for r in d["skills"] if r["category"] in insights.NOT_BUILDABLE][:25]
    st.dataframe([{"Asked for": r["skill"], "Kind": "soft skill" if r["category"] == "soft" else "requirement",
                   "% of postings": round(100 * r["share"]), "You": HAVE if r["have"] else GAP} for r in soft],
                 hide_index=True, width="stretch")

with st.expander("All skills as a table"):
    st.dataframe([{"Skill": r["skill"], "Category": r["category"], "% of postings": round(100 * r["share"]),
                   "Postings": r["postings"], "You": HAVE if r["have"] else GAP, "Asked by": ", ".join(dict.fromkeys(r["companies"])),
                   "As written": ", ".join(r["spellings"])} for r in rows], hide_index=True, width="stretch")

# ---------------------------------------------------------------- what works

st.divider()
c3, c4 = st.columns(2, gap="large")
with c3:
    st.subheader("Your most-used facts")
    st.caption("How often the AI chose each fact, and how many of those applications got an interview.")
    usage = insights.fact_usage(apps)
    rows = [{"fact": u["fact"], "applications": u["applications"], "interviews": u["interviews"],
             "text": facts.get(u["fact"], {}).get("text", "")[:120]} for u in usage[:15]]
    hbar(rows, "fact", "applications", "applications using it", tooltip=["fact:N", "applications:Q", "interviews:Q", "text:N"])
    unused = [f for f in facts if facts[f]["section"] in ("experience", "projects") and f not in {u["fact"] for u in usage}
              and facts[f]["parent"] != f]
    if unused and usage:
        st.caption("Never used so far: " + ", ".join(unused))
with c4:
    st.subheader("Funnel")
    stats = memory.stats()
    order = ["ready", "sent", "interview", "offer", "rejected"]
    hbar([{"status": s, "applications": stats["by_status"][s]} for s in order], "status", "applications", "applications", keep_order=True)
    st.subheader("Must-have coverage per application")
    cov = [{"application": f"#{a['id']} {a['company']}", "coverage %": round(100 * insights.coverage(a))}
           for a in analysed if insights.coverage(a) is not None][:15]
    hbar(cov, "application", "coverage %", "% of must-haves covered")
