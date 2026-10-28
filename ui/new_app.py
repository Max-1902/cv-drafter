"""New application: analyse, generate, edit with live preview, then render."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from cvdrafter import analyze, config, generate, memory
from cvdrafter import profile as prof
from cvdrafter.llm import LLMError
from cvdrafter.preview import cl_html, cv_html, similarity, word_diff
from cvdrafter.textutil import safe, safe_html
from cvdrafter.verify import errors, iter_items, run_checks
from ui.common import blocked_panel, bullets, download_buttons, fit_card, kpi, load_profile_or_stop, match_panel, models, pills, render_checked, setup, show_issues

setup()
LIMITS = config.load_slots()["limits"]
M = models()
profile = load_profile_or_stop()


# ---------------------------------------------------------------- state helpers

def set_editor_state(app_id: int, draft: dict) -> None:
    for loc, _, item, _ in iter_items(draft):
        st.session_state[f"ed::{app_id}::{loc}"] = item["text"]
        st.session_state[f"inc::{app_id}::{loc}"] = True


def current_draft(app_id: int, draft: dict) -> dict:
    edits, excluded = {}, set()
    for loc, _, item, _ in iter_items(draft):
        edits[loc] = st.session_state.get(f"ed::{app_id}::{loc}", item["text"])
        if not st.session_state.get(f"inc::{app_id}::{loc}", True):
            excluded.add(loc)
    return generate.apply_edits(draft, edits, excluded)


def promote(app_id: int) -> dict:
    """draft -> ready; never moves an application that's already sent or further back."""
    return {"status": "ready"} if memory.get(app_id)["status"] == "draft" else {}


def open_existing(app_id: int) -> None:
    """Load a saved application (from the Applications page) back into the editor."""
    app = memory.get(app_id)
    st.session_state["cur"] = dict(app_id=app["id"], company=app["company"], role=app["role"], jd=app["jd"],
                                   notes=app["notes"] or "", recipient="", address="", extra="", analysis=app["analysis"])
    draft = app["final"] or app["generated"]
    if draft:
        st.session_state["result"] = {"draft": draft, "issues": [], "fix_rounds": 0, "examples_used": 0}
        set_editor_state(app["id"], draft)
    else:
        st.session_state.pop("result", None)
    st.session_state["files"] = app["files"]


if "open_app_id" in st.session_state:
    st.session_state.pop("dup_pending", None)
    open_existing(st.session_state.pop("open_app_id"))


# ---------------------------------------------------------------- step 1: input

st.title("New application")
cur = st.session_state.get("cur")

with st.expander("1 · Posting", expanded=cur is None):
    with st.form("job"):
        c1, c2 = st.columns(2)
        company = c1.text_input("Company", help="Leave empty: the AI finds it in the posting and checks it against the text and URL.")
        role = c2.text_input("Role")
        recipient = c1.text_input("Recipient name (optional)", help="Used for 'Dear …,'. If empty, uses 'Dear Hiring Manager,'.")
        url = c2.text_input("Posting URL (optional)")
        address = c1.text_area("Company address (optional)", height=68)
        notes = c2.text_area("Your notes about the company (optional)", height=68,
                             help="Things you know that the posting doesn't say. The AI may use them in the cover letter.")
        jd = st.text_area("Job posting", height=260, placeholder="Paste the full job description…")
        extra = st.text_input("Extra instructions (optional)", placeholder="e.g. lead with the RAG project")
        go = st.form_submit_button("Analyse posting", type="primary")

def log_new(company, role, jd, notes, url, recipient, address, extra, analysis) -> dict:
    app_id = memory.create_application(company, role, "en", jd, notes, analysis, url=url)
    st.session_state.pop("result", None)
    st.session_state.pop("files", None)
    st.session_state["cur"] = dict(app_id=app_id, company=company, role=role, jd=jd, notes=notes,
                                   recipient=recipient, address=address, extra=extra, analysis=analysis)
    return st.session_state["cur"]


def label(app: dict) -> str:
    return f"#{app['id']} {app['company'] or '?'} · {app['role']} ({app['status']})"


if go:
    st.session_state.pop("dup_pending", None)
    if not jd.strip():
        st.error("Paste a job posting first.")
        st.stop()
    dup = memory.find_duplicate(jd, url)
    if dup and (dup["jd"] or "").strip():
        st.info(f"You already have this posting: {label(dup)}. Opened it below; nothing new was logged.")
        open_existing(dup["id"])
        cur = st.session_state["cur"]
        go = False
if go:
    with st.spinner("Analysing posting against your fact bank…"):
        try:
            analysis = analyze.analyze_job(jd, company, role, profile, M["writer"], url=url)
        except LLMError as e:
            st.error(str(e))
            st.stop()
    company = company or analysis.get("company", "")
    role = role or analysis.get("role", "")
    same = dup or memory.find_same_job(company, role)
    if same and not (same["jd"] or "").strip():
        memory.attach_posting(same["id"], jd, analysis, url)
        st.success(f"That's {label(same)}, which you logged without its posting. The posting is attached to it now; "
                   "nothing new was logged.")
        open_existing(same["id"])
        cur = st.session_state["cur"] = {**st.session_state["cur"], "recipient": recipient, "address": address, "extra": extra}
    elif same:
        st.session_state["dup_pending"] = dict(match=same["id"], company=company, role=role, jd=jd, notes=notes, url=url,
                                               recipient=recipient, address=address, extra=extra, analysis=analysis)
        open_existing(same["id"])
        cur = st.session_state["cur"]
    else:
        cur = log_new(company, role, jd, notes, url, recipient, address, extra, analysis)

if pending := st.session_state.get("dup_pending"):
    match = memory.get(pending["match"])
    st.warning(f"This looks like a job you already have: {label(match) if match else '#' + str(pending['match'])}, same "
               "company and role, though the posting text differs. Opened it below; nothing new was logged.")
    if st.button("It's a different opening: log it as new"):
        st.session_state.pop("dup_pending")
        log_new(**{k: v for k, v in pending.items() if k != "match"})
        st.rerun()

if not cur:
    st.info("Paste a posting above. You'll get a fit analysis first, then a tailored CV + cover letter.")
    st.stop()


# ---------------------------------------------------------------- step 2: analysis

a = cur["analysis"]
saved = memory.get(cur["app_id"])  # company/role may have been corrected on another page since
cur["company"], cur["role"] = saved["company"], saved["role"]
st.subheader(f"{safe(cur['role'])} · {safe(cur['company'])}")
st.caption(f"\\#{cur['app_id']} · " + " · ".join(safe(a.get(k, "")) for k in ("role_type", "seniority", "summary")))
job = {k: v for k, v in (a.get("job") or {}).items() if v}
if job:
    st.caption(" · ".join(safe(v) for v in job.values()))
check = a.get("company_check") or {}
conf = saved.get("company_confidence") or check.get("confidence")
if conf in ("low", "medium"):
    st.warning(f"Company “{safe(cur['company']) or '?'}” is a {conf}-confidence guess ({safe(check.get('source'))}). "
               "Documents won't render until you confirm it, or correct it on the Applications page.")
    if st.button(f"“{cur['company'] or '?'}” is the right company", disabled=not cur["company"]):
        memory.update(cur["app_id"], company_confidence="high")
        st.rerun()
if check.get("via_agency"):
    st.info("This looks like a recruiting agency posting for a client.")

elig = a.get("eligibility") or {}
if elig.get("excludes_visa_holders"):
    st.error(f"Posting appears to exclude visa holders or offer sponsorship: “{safe(elig.get('work_authorization'))}”")
elif elig.get("work_authorization"):
    st.warning(f"Work authorization mentioned: “{safe(elig['work_authorization'])}”. The letter states it from your profile (personal.work_authorization), or a placeholder if that is empty.")
if elig.get("graduation_requirement"):
    st.warning(f"Graduation requirement: “{safe(elig['graduation_requirement'])}”. "
               f"Your graduation: {safe(prof.graduation(profile) or ' not in your profile')}.")
if elig.get("timing"):
    st.info(f"Timing: {safe(elig['timing'])}")
if a.get("fit_warning"):
    st.warning(f"Fit: {safe(a['fit_warning'])}")

fit_card(a, profile, f"ed{cur['app_id']}")
s = analyze.coverage_stats(a)
c = st.columns(4)
kpi(c[0], "Must-haves covered", f"{s['must_covered']}/{s['must_total']}")
kpi(c[1], "Covered", s["covered"])
kpi(c[2], "Partial", s["partial"])
kpi(c[3], "Missing", s["missing"])

with st.expander("Requirements and keywords", expanded="result" not in st.session_state):
    st.dataframe(
        [{"Status": r["status"].title(), "Requirement": r["text"], "Type": r["importance"],
          "Kind": r.get("kind", "skill"), "Evidence": ", ".join(r["fact_ids"]),
          "Match": "interpreted" if r.get("evidence") == "transferable" else "direct", "Why": r["comment"]}
         for r in a["requirements"]],
        hide_index=True, width="stretch",
    )
    kw = a.get("keywords", [])
    st.markdown("**You have:** " + pills([k["term"] for k in kw if k["in_profile"]], "ok"), unsafe_allow_html=True)
    st.markdown("**Not in your profile (blocked from the CV):** " + pills([k["term"] for k in kw if not k["in_profile"]], "no"),
                unsafe_allow_html=True)

gaps = [r for r in a["requirements"] if r["status"] != "covered"]
if gaps:
    with st.expander(f"Fill a gap: do you actually have experience for any of the {len(gaps)} gaps?"):
        st.caption("Only add things you really did. They're saved to learned_facts.yaml and used from now on.")
        targets = {"general": "General (cover letter only)"}
        targets |= {e["id"]: f"{e.get('company') or e.get('name')}" for sec in ("experience", "projects") for e in profile[sec]}
        with st.form("gap", clear_on_submit=True):
            st.selectbox("Requirement", [g["text"] for g in gaps])
            target = st.selectbox("Where did you do it?", list(targets), format_func=targets.get)
            text = st.text_area("What exactly did you do? (one true, specific sentence)")
            skills = st.text_input("Skills/tools this shows (comma-separated)")
            if st.form_submit_button("Add fact") and text.strip():
                item = prof.add_learned_fact(target, text, [x.strip() for x in skills.split(",") if x.strip()])
                st.success(f"Added {item['id']}. Re-analyse to use it.")
        if st.button("Re-analyse with updated facts"):
            with st.spinner("Re-analysing…"):
                try:
                    cur["analysis"] = analyze.analyze_job(cur["jd"], cur["company"], cur["role"], prof.load_profile(), M["writer"])
                    memory.update(cur["app_id"], analysis=cur["analysis"])
                    st.rerun()
                except LLMError as e:
                    st.error(str(e))

st.divider()


# ---------------------------------------------------------------- step 3: generate

def run_generation(extra: str) -> None:
    status = st.status("Working…", expanded=True)
    try:
        result = generate.draft_application({**cur, "extra": extra, "profile": profile}, M["writer"],
                                            M["checker"] if M["llm_check"] else None, progress=status.write)
    except LLMError as e:
        status.update(label="Failed", state="error")
        st.error(str(e))
        return
    status.update(label=f"Draft ready · {result['fix_rounds']} fix round(s) · {result['examples_used']} past example(s) used",
                  state="complete", expanded=False)
    memory.update(cur["app_id"], generated=result["draft"])
    st.session_state["result"] = result
    st.session_state.pop("files", None)
    set_editor_state(cur["app_id"], result["draft"])


result = st.session_state.get("result")
gc1, gc2 = st.columns([1, 3])
if gc1.button("Generate CV + cover letter" if not result else "Regenerate", type="primary"):
    run_generation(cur.get("extra", ""))
    result = st.session_state.get("result")
tweak = gc2.text_input("Instruction for regenerating", placeholder="e.g. shorter cover letter, lead with the Java rule engine",
                       label_visibility="collapsed")
if tweak and gc2.button("Regenerate with this instruction"):
    run_generation(f"{cur.get('extra', '')}\n{tweak}".strip())
    result = st.session_state.get("result")

if not result:
    st.stop()


# ---------------------------------------------------------------- step 4: edit + preview

draft, app_id = result["draft"], cur["app_id"]
facts = prof.index_facts(profile)
edited = current_draft(app_id, draft)

with st.sidebar.expander("Posting ↔ your CV: exact matches", expanded=True):
    match_panel(edited, cur["analysis"], profile, key=f"ed{app_id}")

for title, items, fn in (("Warnings", draft.get("warnings"), st.error),
                         ("Left out (not supported by your CV)", draft.get("left_out"), None),
                         ("Check before sending", draft.get("check_before_sending"), None)):
    if items:
        if fn:
            fn("**Warnings**\n" + bullets(items))
        else:
            with st.expander(f"{title} ({len(items)})"):
                st.markdown(bullets(items))

n_err = len(errors(result["issues"]))
with st.expander(f"Fact check: {n_err} error(s), {len(result['issues']) - n_err} warning(s)", expanded=n_err > 0):
    show_issues(result["issues"])

left, right = st.columns([11, 10], gap="large")

with left:
    def editor(loc: str, item: dict, height: int) -> None:
        src = "\n\n".join(safe(f"[{x}] {facts[x]['text']}" if x in facts else f"[{x}]") for x in item["sources"])
        c1, c2 = st.columns([20, 1])
        c1.text_area(loc, key=f"ed::{app_id}::{loc}", height=height, label_visibility="collapsed",
                     help=f"Sources:\n\n{src}")
        c2.checkbox("Include", key=f"inc::{app_id}::{loc}", label_visibility="collapsed", help="Include this text")
        text = st.session_state.get(f"ed::{app_id}::{loc}", "")
        limit = LIMITS["cl_paragraph_chars"] if loc.startswith("cover") else LIMITS["bullet_chars"]
        n = len(text.replace("**", ""))
        flag = " · over limit" if n > limit else ""
        st.markdown(f'<div class="src">{n}/{limit} chars{flag} · sources: {safe_html(", ".join(item["sources"]))}</div>', unsafe_allow_html=True)

    def chat_panel() -> None:
        """Ask the AI to change texts: its edits land in the boxes below, then the fact check runs again.
        Runs before the boxes are drawn, so it may still set their values."""
        S, key = st.session_state, f"ed::{app_id}::"
        log = S.setdefault(f"edchat::{app_id}", [])
        with st.expander("Ask AI to change something", expanded=True):
            for m in log[-6:]:
                with st.chat_message(m["role"]):
                    st.markdown(safe(m["content"]))
            if S.get(f"edundo::{app_id}") and st.button("Undo last AI change", key=f"edundo_btn{app_id}"):
                S.update({key + loc: text for loc, text in S.pop(f"edundo::{app_id}").items()})
                log.append({"role": "assistant", "content": "Undone."})
                st.rerun()
            ask = st.chat_input("e.g. shorten the second bullet of my latest job, warmer opening in the letter", key=f"edask{app_id}")
        if not ask:
            return
        texts = {loc: S.get(key + loc, item["text"]) for loc, _, item, _ in iter_items(draft)}
        with st.spinner("Editing and re-checking…"):
            try:
                out = generate.revise_texts({**cur, "profile": profile}, generate.apply_edits(draft, texts), ask, log,
                                            M["writer"])
            except LLMError as e:
                st.error(str(e))
                return
            log.append({"role": "user", "content": ask})
            reply = out["reply"] or ("Done." if out["edits"] else "I didn't change anything.")
            if out["edits"]:
                S[f"edundo::{app_id}"] = {loc: texts[loc] for loc in out["edits"]}
                S.update({key + loc: text for loc, text in out["edits"].items()})
                issues = run_checks(current_draft(app_id, draft), profile, cur["analysis"], cur["jd"], cur["notes"],
                                    LIMITS, M["checker"] if M["llm_check"] else None)
                S["result"] = {**result, "issues": issues}
                reply += f"\n\nChanged: {', '.join(out['edits'])}."
                if errors(issues):
                    reply += f" The fact check found {len(errors(issues))} error(s); see Fact check above, or Undo."
        log.append({"role": "assistant", "content": reply})
        st.rerun()

    chat_panel()
    t_cv, t_cl, t_diff = st.tabs(["CV text", "Cover letter", "What changed vs. your facts"])
    with t_cv:
        st.caption("Only these texts change; everything else is locked from your profile. **bold** stays bold. "
                   "Untick the box to drop a bullet.")
        for job in profile["experience"]:
            st.markdown(f"**{job['company']}** · {job['title']}")
            for i, b in enumerate(draft["experience"].get(job["id"], []), 1):
                editor(f"experience/{job['id']}/{i}", b, 76)
        projects = {p["id"]: p for p in profile["projects"]}
        for p in draft["projects"]:
            st.markdown(f"**{projects[p['id']]['name']}** · project")
            for i, b in enumerate(p["bullets"], 1):
                editor(f"projects/{p['id']}/{i}", b, 76)
        st.markdown("**Skills (order chosen for this posting)**")
        for sk in draft["skills"]:
            st.markdown(f"- **{sk['category']}:** {', '.join(sk['items'])}")
    with t_cl:
        words = sum(len(p["text"].replace("**", "").split()) for p in edited["cover_letter"])
        lo, hi = LIMITS["cl_words"]["min"], LIMITS["cl_words"]["max"]
        (st.success if lo <= words <= hi else st.warning)(f"{words} words (target {lo}–{hi})")
        for i, p in enumerate(draft["cover_letter"], 1):
            editor(f"cover_letter/{i}", p, 170)
    with t_diff:
        st.caption("Each text compared with the facts it cites: red = removed from your wording, green = added. "
                   "Big green additions deserve a second look.")
        for loc, kind, item, _ in iter_items(edited):
            if kind == "cl":
                continue
            src = " ".join(facts[x]["text"].split(" (skills:")[0] for x in item["sources"] if x in facts)
            sim = similarity(src, item["text"])
            st.markdown(f"<div class='card'><div class='m'>{safe_html(loc)} · {sim:.0%} similar to source</div>"
                        f"{word_diff(src, item['text'])}</div>", unsafe_allow_html=True)

with right:
    p_cv, p_cl = st.tabs(["CV preview", "Cover letter preview"])
    changed = {i["text"] for loc, _, i, _ in iter_items(edited)} - {i["text"] for loc, _, i, _ in iter_items(draft)}
    with p_cv:
        st.caption("Live preview, updates as you type (yellow = your edits). The PDF is the source of truth.")
        st.html(f"<div style='zoom:.6;overflow-x:auto'>{cv_html(edited, profile, changed)}</div>")
    with p_cl:
        st.html(f"<div style='zoom:.6;overflow-x:auto'>{cl_html(edited, profile, cur['company'], cur['recipient'], cur['address'])}</div>")


# ---------------------------------------------------------------- step 5: check + render

st.divider()
c1, c2, c3, c4, c5 = st.columns(5)
if c1.button("Re-check my edits"):
    with st.spinner("Checking…"):
        issues = run_checks(edited, profile, cur["analysis"], cur["jd"], cur["notes"], LIMITS,
                            M["checker"] if M["llm_check"] else None)
    st.session_state["result"] = {**result, "issues": issues}
    st.rerun()
make_pdf = c2.toggle("Also export PDF", value=True, help="Uses Microsoft Word.")
render_now = c3.button("Render documents", type="primary")
override = blocked_panel(f"ed{app_id}")
if render_now or override:
    files = render_checked({"id": app_id, **cur}, edited, profile, f"ed{app_id}", override=override,
                           recipient=cur["recipient"], address=cur["address"], make_pdf=make_pdf)
    memory.update(app_id, final=edited, **({"files": files, **promote(app_id)} if files else {}))
    st.session_state["files"] = files
    st.rerun()
if c4.button("Save edits as final"):
    memory.update(app_id, final=edited, **promote(app_id))
    st.toast("Saved. Future applications will learn from this one.")
if c5.button("I applied today", help="Logs it as sent today and sets a follow-up reminder in 7 days."):
    memory.update(app_id, final=edited)
    memory.mark_applied(app_id)
    st.toast(f"Logged: applied to {cur['company']} today.")

files = st.session_state.get("files")
if files:
    folder = Path(files["cv_docx"]).parent
    st.success(f"Saved to `{folder}`")
    if files.get("cv_pages", 1) > 1:
        st.error(f"The CV runs to {files['cv_pages']} pages. Untick a bullet or shorten some text, then render again.")
    if make_pdf and "cv_pdf" not in files:
        st.warning("PDF export failed. Open the .docx in Word and use File > Save as PDF.")
    download_buttons(files, f"ed{app_id}", app_id)
    st.caption("Once you've sent it, click  I applied today. Outcomes decide which past letters become style examples.")
