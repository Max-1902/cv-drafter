"""Apply: paste a posting into the chat. It's logged, analysed and drafted on the right, where you edit everything."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import streamlit as st

from cvdrafter import analyze, answers, assistant, config, default_cv, fit, generate, memory
from cvdrafter import profile as prof
from cvdrafter.matches import match_map
from cvdrafter.llm import LLMError
from cvdrafter.preview import cl_html, cv_html
from cvdrafter.textutil import plain, safe, safe_html, word_count
from cvdrafter.verify import errors, iter_items
from ui.common import APP_DETAILS, CONFIDENCE, auto_write_toggle, blocked_panel, company_confidence, confirm_company_button, cost_line, download_buttons, fit_card, load_chat, load_profile_or_stop, model_controls, models, paid_toggle, pills, render_checked, save_chat, setup, show_issues

setup()
st.markdown("""<style>
.block-container{padding-top:2.6rem;max-width:1680px}
.hint{font-size:.75rem;color:#91a0ae}
div[data-testid="stChatMessage"]{padding:.55rem .7rem}
</style>""", unsafe_allow_html=True)
M = models()
profile = load_profile_or_stop()
LIMITS = config.load_slots()["limits"]
S = st.session_state
st.markdown('<div class="eyebrow">APPLICATION WORKSPACE</div><h1 style="margin:.15rem 0 0">Create and manage applications</h1>'
            '<p style="color:#91a0ae;margin:.2rem 0 1rem">Analyse a posting, review the draft, and track its status.</p>',
            unsafe_allow_html=True)
S.setdefault("chat", load_chat("apply", [{"role": "assistant", "content":
    "**Paste a job posting** (link included if you have it). I log it, find the company and check your fit. "
    "Say *write it* for your CV + cover letter; they appear on the right, where you can edit everything.\n\n"
    "After that, just talk to me: *applied* · *status interview* · *company is Optiver* · *make the letter shorter* · *pdf*"}]))
S.setdefault("make_pdf", True)
S.setdefault("ver", {})
S.setdefault("issues", {})


def say(text: str) -> None:
    S["chat"].append({"role": "assistant", "content": text})


def bump(app_id: int) -> None:
    """Fresh widget keys, so the right side shows what the chat just changed."""
    S["ver"][app_id] = S["ver"].get(app_id, 0) + 1


def draft_of(app: dict) -> dict | None:
    return app["final"] or app["generated"]


def logged_message(app: dict) -> str:
    a = app["analysis"] or {}
    details = " · ".join(safe(app.get(k)) for k, _ in APP_DETAILS if app.get(k))
    lines = [f"**Logged \\#{app['id']}: {safe(app['company']) or '?'} · {safe(app['role'])}**"]
    if details:
        lines.append(details)
    lines.append(f"**Fit: {safe(fit.assess(a, profile)['summary'])}**")
    check = a.get("company_check") or {}
    if check.get("confidence") != "high":
        lines.append(f"I'm not sure about the company ({safe(check.get('source'))}). Tell me the right name if it's wrong.")
    elig = a.get("eligibility") or {}
    if elig.get("excludes_visa_holders"):
        lines.append(f"No visa sponsorship: “{safe(elig.get('work_authorization'))}”")
    if a.get("fit_warning"):
        lines.append(f" {safe(a['fit_warning'])}")
    choice = default_cv.decide(a, profile)
    lines.append(f"CV: {'needs tailoring' if choice['tailor'] else 'your default fits'}, {safe(choice['reason'])}.")
    if app["status"] == "sent":
        lines.append("Marked as applied today.")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- actions (the chat and the buttons share these)

def app_label(app: dict) -> str:
    sent = f", applied {app['sent_at'][:10]}" if app.get("sent_at") else ""
    return f"**\\#{app['id']} {safe(app['company']) or '?'} · {safe(app['role'])}** ({app['status']}{sent})"


def attach(app: dict, text: str, analysis: dict, url: str) -> None:
    """The posting of an application you logged without it: fill that one in instead of logging a new one."""
    app = memory.attach_posting(app["id"], text, analysis, url)
    S["active"] = app["id"]
    bump(app["id"])
    say(f"That's {app_label(app)}, which you logged without its posting. I attached the posting to it, so it "
        f"has a fit verdict now: {safe(fit.assess(analysis, profile)['summary'])}. Nothing new was logged.")


def do_posting(text: str, write, force: bool = False, analysis: dict | None = None) -> None:
    """force: log it even if it looks like one you have (analysis: reuse the one already made for it)."""
    url = assistant.find_url(text)
    S.pop("dup_pending", None)
    dup = None if force else memory.find_duplicate(text, url)
    if dup and (dup["jd"] or "").strip():
        S["active"] = dup["id"]
        say(f"You already have this posting: {app_label(dup)}. It's open on the right; nothing new was logged.")
        return
    if analysis is None:
        write("Reading the posting…")
        analysis = analyze.analyze_job(text, "", "", profile, M["writer"], url=url)
    if not force:
        again = memory.find_duplicate(text, url)  # the same paste, sent twice, finished first
        if again and (again["jd"] or "").strip():
            S["active"] = again["id"]
            say(f"You already have this posting: {app_label(again)}.")
            return
        same = dup or memory.find_same_job(analysis.get("company", ""), analysis.get("role", ""))
        if same and not (same["jd"] or "").strip():
            attach(same, text, analysis, url)
            return
        if same:
            S["active"] = same["id"]
            S["dup_pending"] = {"text": text, "analysis": analysis}
            say(f"This looks like a job you already have: {app_label(same)}, same company and role, though the "
                "posting text differs. It's open on the right and nothing new was logged. If it really is a "
                "different opening, say *log it anyway*.")
            return
    app_id = memory.create_application(analysis.get("company", ""), analysis.get("role", ""), "en", text, "", analysis, url=url)
    if assistant.says_applied(text):
        memory.mark_applied(app_id)
    S["active"] = app_id
    say(logged_message(memory.get(app_id)))
    if S["auto_write"]:
        S["pending"] = {"kind": "draft", "app_id": app_id, "extra": ""}
    else:
        say("Say *write it* or click **Write CV + letter** on the right when you want the documents (~25-50k tokens).")


def do_draft(app_id: int, extra: str, write, tailor: bool | None = None) -> None:
    app = memory.get(app_id)
    ctx = dict(app_id=app_id, company=app["company"], role=app["role"], jd=app["jd"], notes=app["notes"] or "",
               extra=extra, analysis=app["analysis"], profile=profile)
    result = generate.draft_application(ctx, M["writer"], M["checker"] if M["llm_check"] else None, progress=write,
                                        tailor_cv=tailor)
    memory.update(app_id, generated=result["draft"], final=None)
    S["issues"][app_id] = result["issues"]
    bump(app_id)
    n = len(errors(result["issues"]))
    cv = ("a tailored CV" if result["tailored_cv"]
          else "your default CV (say *tailor the CV* if this firm deserves a rewrite)")
    say(f"Cover letter for {safe(app['company'])} is ready on the right, with {cv}. "
        + (f"{n} fact-check problem(s) left; see the Fact check tab. " if n else "Every statement traces back to your facts. ")
        + "Edit anything there, or tell me what to change.")


def do_answers(app_id: int, questions: list[str], write) -> None:
    app = memory.get(app_id)
    ctx = dict(company=app["company"], role=app["role"], jd=app["jd"], notes=app["notes"] or "",
               analysis=app["analysis"], profile=profile)
    new = answers.answer_questions(ctx, questions, M["writer"], M["checker"] if M["llm_check"] else None, progress=write)
    asked = {a["question"] for a in new}
    memory.update(app_id, answers=new + [a for a in app["answers"] or [] if a["question"] not in asked])
    n = sum(len(errors(a["issues"])) for a in new)
    say(f"Answered {len(new)} question(s) for {safe(app['company'])}. They're under **Application questions** on the "
        "right, ready to copy." + (f" {n} problem(s) are left; they're shown under the answers." if n else ""))


def do_render(app_id: int, override: bool = False) -> dict | None:
    """None if the check found errors; the panel on the right then lists them."""
    app = memory.get(app_id)
    files = render_checked(app, draft_of(app), profile, f"chat{app_id}", override=override, make_pdf=S["make_pdf"])
    if files:
        memory.update(app_id, files=files, status="ready" if app["status"] == "draft" else app["status"])
    return files


LOG_ANYWAY = re.compile(r"^\s*(log|add|save)( it| this)?( as new| as a new one)?( anyway)?|it'?s (a )?different|new (one|opening|application)\b", re.I)


def do_command(text: str, write) -> None:
    if S.get("dup_pending") and LOG_ANYWAY.search(text):
        pending = S.pop("dup_pending")
        do_posting(pending["text"], write, force=True, analysis=pending["analysis"])
        return
    app = memory.get(S["active"]) if S.get("active") else None
    act = assistant.route(text, app, memory.list_all(), M["fast"])
    kind = act["action"]
    if kind == "switch" and memory.get(int(act.get("app_id") or 0)):
        S["active"] = int(act["app_id"])
        say(safe(act["reply"]) or "Switched.")
        return
    if not app:
        say(safe(act["reply"]) if kind == "chat" and act["reply"] else "Paste a job posting first and I'll get started.")
        return
    if kind == "update" and act["fields"]:
        fields = act["fields"]
        if "company" in fields:
            fields["company_confidence"] = "high"
        memory.update(app["id"], **fields)
        if fields.get("status") == "sent" and not app.get("sent_at"):
            memory.mark_applied(app["id"])
        bump(app["id"])
    elif kind == "applied":
        try:
            on = date.fromisoformat(act.get("applied_on") or "")
        except ValueError:
            on = date.today()
        memory.mark_applied(app["id"], on)
        bump(app["id"])
    elif kind == "write":
        S["pending"] = {"kind": "draft", "app_id": app["id"], "extra": "", "tailor": act.get("tailor")}
        return
    elif kind == "regenerate":
        S["pending"] = {"kind": "draft", "app_id": app["id"], "extra": act.get("instruction") or text}
    elif kind == "render":
        if not draft_of(app):
            say("There's no draft yet. Say *write it* first.")
            return
        write("Rendering…")
        files = do_render(app["id"])
        if not files:
            say("I didn't render it: the check found problems in the text. They're listed on the right.")
            return
        say("Documents are ready to download on the right." + (" The CV runs past one page." if files.get("cv_pages", 1) > 1 else ""))
        return
    say(safe(act["reply"]) or "Done.")


# ---------------------------------------------------------------- right side: everything about the active application

def save_text(app_id: int, draft: dict, prefix: str) -> None:
    edits = {loc: S.get(f"{prefix}{loc}", item["text"]) for loc, _, item, _ in iter_items(draft)}
    removed = {loc for loc, text in edits.items() if not text.strip()}
    memory.update(app_id, final=generate.apply_edits(draft, edits, removed))
    if removed:  # numbering shifts once a bullet is gone
        bump(app_id)


def match_view(app: dict, draft: dict | None) -> None:
    """Each requirement of the posting, the facts of yours that answer it, and where this draft shows it."""
    a = app["analysis"] or {}
    facts = prof.index_facts(profile)
    shown = {r["text"]: r["where"] for r in match_map(draft, a, profile)["requirements"]} if draft else {}
    icon = {"covered": "✅", "partial": "🟡", "missing": "❌"}
    rank = {"covered": 0, "partial": 1, "missing": 2}
    st.markdown("**How you match this posting**")
    reqs = fit.apply_interpretations(a.get("requirements", []), prof.load_interpretations(), facts)
    for r in sorted(reqs, key=lambda r: (r["importance"] != "must", rank.get(r["status"], 2))):
        evidence = [facts[i]["text"].split(" (skills:")[0] for i in r["fact_ids"] if i in facts]
        lines = [f"You: {'; '.join(evidence)}" if evidence else (r.get("comment") or "Nothing in your profile yet.")]
        if evidence and r.get("evidence") == "transferable" and r.get("comment"):
            lines.append(f"Interpreted: {r['comment']}")
        if draft and r["status"] != "missing":
            lines.append("In this draft: " + (", ".join(shown.get(r["text"]) or []) or "not used yet"))
        st.markdown(f'<div class="card"><div class="t">{icon.get(r["status"], "•")} {safe_html(r["text"])} '
                    f'<span class="hint">({safe_html(r["importance"])})</span></div>'
                    + "".join(f'<div class="m">{safe_html(x)}</div>' for x in lines) + "</div>", unsafe_allow_html=True)


def answers_box(app: dict) -> None:
    """Short answers to the application form's questions, saved with the application."""
    app_id, saved = app["id"], app["answers"] or []
    with st.expander(f"Application questions · {len(saved)} answered" if saved else "Application questions",
                     expanded=bool(saved)):
        if not (app["jd"] or "").strip():
            st.caption("Paste this job's posting in the chat first; the answers are built from it and your facts.")
            return
        with st.form(f"qa_{app_id}", clear_on_submit=True, border=False):
            text = st.text_area("The form's questions, one per line", height=90,
                                placeholder="Why are you interested in this position?\n"
                                            "What AI-specific technologies are you comfortable with? (max 500 characters)")
            asked = st.form_submit_button("Answer")
        if asked and (questions := answers.split_questions(text)):
            S["chat"].append({"role": "user", "content": f" *{len(questions)} application question(s)*"})
            S["pending"] = {"kind": "answers", "app_id": app_id, "questions": questions}
            st.rerun()
        if (S.get("pending") or {}).get("kind") == "answers" and S["pending"].get("app_id") == app_id:
            st.info("Answering… they show up here in a moment.")
        for a in saved:
            st.markdown(f"**{safe(a['question'])}**")
            st.code(a["answer"] or "(no answer)", language=None, wrap_lines=True)
            text = plain(a["answer"])
            st.caption(f"{word_count(text)} words · {len(text)} characters · sources: {safe(', '.join(a['sources'])) or 'none'}")
            for i in a.get("issues") or []:
                (st.error if i["level"] == "error" else st.warning)(safe(i["message"]))
        if saved and st.button("Clear answers", key=f"qa_clear_{app_id}"):
            memory.update(app_id, answers=None)
            st.rerun()


def panel(app: dict) -> None:
    app_id, v = app["id"], S["ver"].get(app["id"], 0)
    a = app["analysis"] or {}
    s = analyze.coverage_stats(a)
    st.markdown(f"#### {safe(app['company']) or '?'} · {safe(app['role'])}")
    if a.get("requirements"):
        fit_card(a, profile, f"chat{app_id}")
    st.markdown(f'<span class="hint">{app["status"].title()} · {CONFIDENCE.get(company_confidence(app), "")}'
                " · details and status on the History page</span>", unsafe_allow_html=True)
    confirm_company_button(app, f"chat{app_id}")
    draft = draft_of(app)
    with st.container(height=360, border=False):
        match_view(app, draft)

    b = st.columns(4)
    if b[0].button("I applied today", type="primary", disabled=app["status"] not in ("draft", "ready"), key=f"ap_{app_id}"):
        memory.mark_applied(app_id)
        bump(app_id)
        say(f"Logged: applied to {safe(app['company'])} today. Follow-up reminder in 7 days.")
        st.rerun()
    if b[1].button("Render CV + letter", disabled=not draft, key=f"rd_{app_id}"):
        do_render(app_id)
        st.rerun()
    if b[2].button("Rewrite" if draft else "Write CV + letter", key=f"rg_{app_id}"):
        S["pending"] = {"kind": "draft", "app_id": app_id, "extra": ""}
        st.rerun()
    if b[3].button("Detailed editor", disabled=not draft, key=f"ed_{app_id}"):
        S["open_app_id"] = app_id
        st.switch_page("ui/new_app.py")

    if blocked_panel(f"chat{app_id}"):
        do_render(app_id, override=True)
        st.rerun()
    if download_buttons(app["files"], f"chat{app_id}"):
        if (app["files"] or {}).get("cv_pages", 1) > 1:
            st.error("The CV runs past one page. Clear a bullet below (empty text removes it) and render again.")

    with st.expander(f"Fit details · {s['missing']} requirement(s) missing"):
        icon = {"covered": "✅", "partial": "🟡", "missing": "❌"}  # same markers as the match panel
        for r in a.get("requirements", []):
            st.markdown(f"{icon.get(r['status'], '•')} {safe(r['text'])} *({r['importance']})*")
        kw = a.get("keywords", [])
        st.markdown(pills([k["term"] for k in kw if k["in_profile"]], "ok") + " "
                    + pills([k["term"] for k in kw if not k["in_profile"]], "no"), unsafe_allow_html=True)

    answers_box(app)

    if not draft:
        pending = S.get("pending") or {}
        if pending.get("kind") == "draft" and pending.get("app_id") == app_id:
            st.info("Writing your CV + cover letter… (it appears here when it's done)")
        return

    prefix = f"ed::{app_id}::{v}::"
    issues = S["issues"].get(app_id, [])
    t_cv, t_cl, p_cv, p_cl, t_chk = st.container(height=560, border=False).tabs(["CV", "Cover letter", "CV preview", "Letter preview",
                                             f"Fact check ({len(errors(issues))})"])

    def editor(loc: str, item: dict, height: int, limit: int) -> None:
        st.text_area(loc, item["text"], key=prefix + loc, height=height, label_visibility="collapsed",
                     on_change=save_text, args=(app_id, draft, prefix), help="Sources: " + ", ".join(item["sources"]))
        n = len(item["text"].replace("**", ""))
        st.markdown(f'<div class="src">{n}/{limit} chars{" · over limit" if n > limit else ""}</div>', unsafe_allow_html=True)

    with t_cv:
        st.caption("Edits save automatically. Empty a bullet to remove it. **bold** stays bold.")
        for job in profile["experience"]:
            st.markdown(f"**{safe(job['company'])}** · {safe(job['title'])}")
            for i, item in enumerate(draft["experience"].get(job["id"], []), 1):
                editor(f"experience/{job['id']}/{i}", item, 70, LIMITS["bullet_chars"])
        names = {p["id"]: p["name"] for p in profile["projects"]}
        for proj in draft["projects"]:
            st.markdown(f"**{safe(names.get(proj['id'], proj['id']))}** · project")
            for i, item in enumerate(proj["bullets"], 1):
                editor(f"projects/{proj['id']}/{i}", item, 70, LIMITS["bullet_chars"])
    with t_cl:
        words = sum(len(p["text"].replace("**", "").split()) for p in draft["cover_letter"])
        lo, hi = LIMITS["cl_words"]["min"], LIMITS["cl_words"]["max"]
        st.caption(f"{words} words (target {lo}–{hi}). Edits save automatically.")
        for i, item in enumerate(draft["cover_letter"], 1):
            editor(f"cover_letter/{i}", item, 160, LIMITS["cl_paragraph_chars"])
    with p_cv:
        st.html(f"<div style='zoom:.62;overflow-x:auto'>{cv_html(draft, profile)}</div>")
    with p_cl:
        st.html(f"<div style='zoom:.62;overflow-x:auto'>{cl_html(draft, profile, app['company'])}</div>")
    with t_chk:
        if app_id in S["issues"]:
            show_issues(issues)
        else:
            st.caption("Fact-check results show here after the app writes a draft in this session. "
                       "The detailed editor can re-check your edits.")


# ---------------------------------------------------------------- layout

left, right = st.columns([4, 7], gap="large")
with left:
    history = st.container(height=600, border=False)
    busy = bool(S.get("pending"))  # a new message mid-job would restart the page and cut the job off
    prompt = st.chat_input("Working… one moment" if busy else "Paste a job posting, or tell me what to change…", disabled=busy)
    o1, o2, o3 = st.columns([4, 3, 3])
    auto_write_toggle(o1)
    paid_toggle(o2)
    model_controls(o3.popover("Models & cost", width="stretch"))
    o1.markdown(f'<span class="hint">{cost_line()}</span>', unsafe_allow_html=True)
    if o3.button("New chat", width="stretch"):
        S["chat"] = S["chat"][:1]
        S.pop("active", None)
        save_chat("apply", S["chat"])
        st.rerun()

if prompt and prompt.strip():
    is_posting = assistant.is_posting(prompt)
    shown = f" *Posting pasted ({len(prompt.split())} words)* {safe(assistant.find_url(prompt))}" if is_posting else safe(prompt)
    S["chat"].append({"role": "user", "content": shown})
    S["pending"] = {"kind": "posting" if is_posting else "command", "text": prompt}
    save_chat("apply", S["chat"])
    st.rerun()  # redraw with the chat box locked before the job starts

with history:
    for m in S["chat"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])  # already escaped when added

with right:
    apps = memory.list_all()
    if S.get("active") not in {a["id"] for a in apps}:
        S.pop("active", None)
    if (S.get("pending") or {}).get("kind") == "posting":
        st.info("Reading the posting… it shows up here in a moment.")
    elif not apps:
        with st.container(border=True):
            st.markdown("#### Your application shows up here")
            st.markdown("1. **Paste a posting** in the chat. It's logged with company, role, location, pay and deadline.\n"
                        "2. **Your CV + cover letter** get written from your fact bank and fact-checked. Edit them right here.\n"
                        "3. **Say *applied*** when you've sent it. You get a follow-up reminder a week later.")
    else:
        labels = {a["id"]: f"#{a['id']} {a['company'] or '?'} · {a['role']} ({a['status']})" for a in apps[:50]}
        active = S.get("active") or apps[0]["id"]
        ids = list(labels) if active in labels else [active, *labels]
        chosen = st.selectbox("Working on", ids, ids.index(active), format_func=lambda i: labels.get(i, f"#{i}"),
                              label_visibility="collapsed")
        if chosen != S.get("active"):
            S["active"] = chosen
        panel(memory.get(chosen))

job = S.pop("pending", None)
if job:
    labels = {"posting": "Logging and analysing the posting…", "command": "On it…", "draft": "Writing your CV + cover letter…",
              "answers": "Answering the application questions…"}
    with history, st.chat_message("assistant"), st.status(labels[job["kind"]], expanded=True) as box:
        def note(msg) -> None:
            try:
                box.write(msg)
            except BaseException:  # the tab was closed mid-job: finish anyway, so the work is saved
                pass
        try:
            if job["kind"] == "posting":
                do_posting(job["text"], note)
            elif job["kind"] == "command":
                do_command(job["text"], note)
            elif job["kind"] == "answers":
                do_answers(job["app_id"], job["questions"], note)
            else:
                do_draft(job["app_id"], job.get("extra", ""), note, job.get("tailor"))
            box.update(state="complete")
        except LLMError as e:
            box.update(label="Failed", state="error")
            say(f" {safe(e)}")
        finally:
            save_chat("apply", S["chat"])
    st.rerun()
