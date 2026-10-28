"""Apply: paste a posting into the chat. It's logged, analysed and drafted below, where you edit everything."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import streamlit as st

from cvdrafter import analyze, answers, assistant, config, default_cv, doctext, fit, generate, memory
from cvdrafter import profile as prof
from cvdrafter.matches import match_map
from cvdrafter.llm import LLMError
from cvdrafter.preview import cl_html, cv_html
from cvdrafter.textutil import plain, safe, safe_html, word_count
from cvdrafter.verify import errors, iter_items
from ui.common import APP_DETAILS, CONFIDENCE, FIT_COLOR, auto_write_toggle, blocked_panel, company_confidence, confirm_company_button, cost_line, download_buttons, fit_card, load_chat, load_profile_or_stop, model_controls, models, paid_toggle, pills, render_checked, save_chat, setup, show_issues

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
WORKSPACE_HEIGHT, CHAT_HEIGHT = 700, 340  # the editor and preview scroll on their own inside one workspace
st.markdown('<div class="eyebrow">APPLICATION WORKSPACE</div>', unsafe_allow_html=True)
S.setdefault("chat", load_chat("apply", [{"role": "assistant", "content":
    "**Paste a job posting** (link included if you have it). I log it, find the company and check your fit. "
    "Say *write it* for your CV + cover letter; they appear below, where you can edit everything.\n\n"
    "After that, just talk to me: *applied* · *status interview* · *company is Optiver* · *make the letter shorter* · *pdf*"}]))
S.setdefault("make_pdf", True)
S.setdefault("ver", {})
S.setdefault("issues", {})


def say(text: str) -> None:
    S["chat"].append({"role": "assistant", "content": text})


def bump(app_id: int) -> None:
    """Fresh widget keys, so the workspace shows what the chat just changed."""
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
        say(f"You already have this posting: {app_label(dup)}. It's open below; nothing new was logged.")
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
                "posting text differs. It's open below and nothing new was logged. If it really is a "
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
        say("Say *write it* or click **Write CV + letter** below when you want the documents (~25-50k tokens).")


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
    say(f"Cover letter for {safe(app['company'])} is ready below, with {cv}. "
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
    say(f"Answered {len(new)} question(s) for {safe(app['company'])}. They're under **Application questions** "
        "below, ready to copy." + (f" {n} problem(s) are left; they're shown under the answers." if n else ""))


def do_render(app_id: int, override: bool = False) -> dict | None:
    """None if the check found errors; the panel below then lists them."""
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
            say("I didn't render it: the check found problems in the text. They're listed below.")
            return
        say("Documents are ready to download below." + (" The CV runs past one page." if files.get("cv_pages", 1) > 1 else ""))
        return
    say(safe(act["reply"]) or "Done.")


# ---------------------------------------------------------------- the workspace: everything about the active application

def save_text(app_id: int, draft: dict, prefix: str) -> None:
    edits = {loc: S.get(f"{prefix}{loc}", item["text"]) for loc, _, item, _ in iter_items(draft)}
    removed = {loc for loc, text in edits.items() if not text.strip()}
    memory.update(app_id, final=generate.apply_edits(draft, edits, removed))
    if removed:  # numbering shifts once a bullet is gone
        bump(app_id)


def save_doc(app_id: int, key: str, doc: str, profile: dict) -> None:
    """The whole-document editor lost focus: read your text. Text that can't be understood is kept in the box and explained."""
    draft = draft_of(memory.get(app_id))
    text = S.get(key, "")
    if doc == "CV":
        new, problems = doctext.text_to_cv(text, draft, profile)
    else:
        new, problems = doctext.text_to_letter(text, draft), []
    S[f"doc_problems::{app_id}::{doc}"] = problems
    if new is not None and new != draft:
        memory.update(app_id, final=new)


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


def status_line(app: dict) -> str:
    """Small colour badges for what needs attention: fit, status, company, fact check, files."""
    a, app_id = app["analysis"] or {}, app["id"]
    badges = []
    if a.get("requirements"):
        f = fit.assess(a, profile)
        badges.append(f":{FIT_COLOR[f['level']]}-badge[{f['verdict']}{'' if f['score'] is None else ' ' + str(f['score'])}]")
    badges.append(f":gray-badge[{app['status'].title()}]")
    if company_confidence(app) in ("low", "medium"):
        badges.append(":orange-badge[Confirm the company]")
    if app_id in S["issues"]:
        n = len(errors(S["issues"][app_id]))
        badges.append(f":red-badge[{n} fact-check error(s)]" if n else ":green-badge[Fact check passed]")
    files = app["files"] or {}
    if any(k.endswith(("_docx", "_pdf")) for k in files):
        badges.append(":red-badge[CV over one page]" if files.get("cv_pages", 1) > 1 else ":green-badge[Files rendered]")
    return " ".join(badges)


def actions(app: dict, draft: dict | None) -> None:
    app_id = app["id"]
    b = st.columns(4)
    if b[0].button("Rewrite" if draft else "Write CV + letter", type="primary" if not draft else "secondary", key=f"rg_{app_id}",
                   width="stretch"):
        S["pending"] = {"kind": "draft", "app_id": app_id, "extra": ""}
        st.rerun()
    if b[1].button("Render CV + letter", disabled=not draft, key=f"rd_{app_id}", width="stretch"):
        do_render(app_id)
        st.rerun()
    if b[2].button("I applied today", type="primary" if draft else "secondary", disabled=app["status"] not in ("draft", "ready"),
                   key=f"ap_{app_id}", width="stretch"):
        memory.mark_applied(app_id)
        bump(app_id)
        say(f"Logged: applied to {safe(app['company'])} today. Follow-up reminder in 7 days.")
        st.rerun()
    if b[3].button("Detailed editor", disabled=not draft, key=f"ed_{app_id}", width="stretch"):
        S["open_app_id"] = app_id
        st.switch_page("ui/new_app.py")
    confirm_company_button(app, f"chat{app_id}")
    if blocked_panel(f"chat{app_id}"):
        do_render(app_id, override=True)
        st.rerun()
    if download_buttons(app["files"], f"chat{app_id}", app_id, can_pull=True):
        if (app["files"] or {}).get("cv_pages", 1) > 1:
            st.error("The CV runs past one page. Clear a bullet below (empty text removes it) and render again.")


def whole_document(app_id: int, draft: dict, doc: str) -> None:
    """The CV or the letter as one text you edit like a document. The preview on the right follows when you click out."""
    key = f"doc::{app_id}::{S['ver'].get(app_id, 0)}::{doc}"
    if doc == "CV":
        over = [loc for loc, _, item, _ in iter_items(draft) if loc.startswith(("experience/", "projects/"))
                and len(item["text"].replace("**", "")) > LIMITS["bullet_chars"]]
        st.caption("One heading per job or project, one line per bullet, **bold** stays bold. Skills are “Category: a, b”. "
                   "Delete a line to remove it; a new line cites the same facts as the bullet above it."
                   + (f" **{len(over)} bullet(s) over the length limit.**" if over else ""))
    else:
        words = sum(len(p["text"].replace("**", "").split()) for p in draft["cover_letter"])
        lo, hi = LIMITS["cl_words"]["min"], LIMITS["cl_words"]["max"]
        st.caption(f"Paragraphs are separated by a blank line. {words} words (target {lo}–{hi}).")
    st.text_area(doc, doctext.cv_to_text(draft, profile) if doc == "CV" else doctext.letter_to_text(draft), key=key,
                 height=WORKSPACE_HEIGHT - 150, label_visibility="collapsed", on_change=save_doc, args=(app_id, key, doc, profile))
    for problem in S.get(f"doc_problems::{app_id}::{doc}") or []:
        st.error(safe(problem))
    st.caption("Your edits show on the right when you click out of the text (or press ⌘/Ctrl+Enter).")


def workspace(app: dict, draft: dict) -> None:
    """The editor and the live preview of the same document, side by side. Each scrolls on its own."""
    app_id = app["id"]
    prefix = f"ed::{app_id}::{S['ver'].get(app_id, 0)}::"

    def editor(loc: str, item: dict, height: int, limit: int) -> None:
        st.text_area(loc, item["text"], key=prefix + loc, height=height, label_visibility="collapsed",
                     on_change=save_text, args=(app_id, draft, prefix), help="Sources: " + ", ".join(item["sources"]))
        n = len(item["text"].replace("**", ""))
        st.markdown(f'<div class="src">{n}/{limit} chars{" · over limit" if n > limit else ""}</div>', unsafe_allow_html=True)

    pick, how = st.columns([3, 4], vertical_alignment="center")
    doc = pick.segmented_control("Document", ["CV", "Cover letter"], default="CV", key="doc_view", label_visibility="collapsed") or "CV"
    layout = how.segmented_control("Edit as", ["Whole document", "Per bullet"], default="Whole document", key="edit_layout",
                                   label_visibility="collapsed") or "Whole document"
    edit_col, view_col = st.columns(2, gap="medium")
    with edit_col:
        with st.container(height=WORKSPACE_HEIGHT, border=True):
            if layout == "Whole document":
                whole_document(app_id, draft, doc)
            elif doc == "CV":
                st.caption("Edits save when you click out of a box. Empty a bullet to remove it. **bold** stays bold.")
                for job in profile["experience"]:
                    st.markdown(f"**{safe(job['company'])}** · {safe(job['title'])}")
                    for i, item in enumerate(draft["experience"].get(job["id"], []), 1):
                        editor(f"experience/{job['id']}/{i}", item, 70, LIMITS["bullet_chars"])
                names = {p["id"]: p["name"] for p in profile["projects"]}
                for proj in draft["projects"]:
                    st.markdown(f"**{safe(names.get(proj['id'], proj['id']))}** · project")
                    for i, item in enumerate(proj["bullets"], 1):
                        editor(f"projects/{proj['id']}/{i}", item, 70, LIMITS["bullet_chars"])
            else:
                words = sum(len(p["text"].replace("**", "").split()) for p in draft["cover_letter"])
                lo, hi = LIMITS["cl_words"]["min"], LIMITS["cl_words"]["max"]
                st.caption(f"{words} words (target {lo}–{hi}). Edits save when you click out of a box.")
                for i, item in enumerate(draft["cover_letter"], 1):
                    editor(f"cover_letter/{i}", item, 160, LIMITS["cl_paragraph_chars"])
    with view_col:
        with st.container(height=WORKSPACE_HEIGHT, border=True):
            html = cv_html(draft, profile) if doc == "CV" else cl_html(draft, profile, app["company"])
            st.html(f"<div style='zoom:.66;overflow-x:auto'>{html}</div>")


def details(app: dict, draft: dict | None) -> None:
    """Match evidence, fit, fact check and form answers: out of the way until you want them."""
    app_id, a = app["id"], app["analysis"] or {}
    issues = S["issues"].get(app_id, [])
    saved = len(app["answers"] or [])
    t_match, t_fit, t_chk, t_qa = st.tabs(["Match details", "Fit", f"Fact check ({len(errors(issues))})",
                                           f"Application questions ({saved})" if saved else "Application questions"])
    with t_match:
        kw = a.get("keywords", [])
        st.markdown(pills([k["term"] for k in kw if k["in_profile"]], "ok") + " "
                    + pills([k["term"] for k in kw if not k["in_profile"]], "no"), unsafe_allow_html=True)
        match_view(app, draft)
    with t_fit:
        if a.get("requirements"):
            fit_card(a, profile, f"chat{app_id}")
        else:
            st.caption("No posting analysed for this application yet.")
    with t_chk:
        if app_id in S["issues"]:
            show_issues(issues)
        else:
            st.caption("Fact-check results show here after the app writes a draft in this session. "
                       "The detailed editor can re-check your edits.")
    with t_qa:
        answers_box(app)


def panel(app: dict) -> None:
    draft = draft_of(app)
    actions(app, draft)
    if draft:
        workspace(app, draft)
    else:
        pending = S.get("pending") or {}
        if pending.get("kind") == "draft" and pending.get("app_id") == app["id"]:
            st.info("Writing your CV + cover letter… (it appears here when it's done)")
    details(app, draft)


# ---------------------------------------------------------------- layout

apps = memory.list_all()
if S.get("active") not in {a["id"] for a in apps}:
    S.pop("active", None)
busy = bool(S.get("pending"))  # a new message mid-job would restart the page and cut the job off
active_app = memory.get(S.get("active") or apps[0]["id"]) if apps else None
# Once there is a draft to work on, the chat folds away so the editor and preview get the screen. It opens again while a job runs.
compact = bool(active_app and draft_of(active_app)) and not busy

with st.expander("Chat · paste a posting, or tell me what to change", expanded=not compact,
                 key="chat_compact" if compact else "chat_open"):
    history = st.container(height=CHAT_HEIGHT, border=False)
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
    pick, badges = st.columns([4, 6], vertical_alignment="center")
    chosen = pick.selectbox("Working on", ids, ids.index(active), format_func=lambda i: labels.get(i, f"#{i}"),
                            label_visibility="collapsed")
    if chosen != S.get("active"):
        S["active"] = chosen
    app = memory.get(chosen)
    badges.markdown(status_line(app))
    panel(app)

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
