"""Shared UI helpers: settings, styling, small components."""
from __future__ import annotations

import os
import signal
import threading

import altair as alt
import streamlit as st

from cvdrafter import config, fit
from cvdrafter import profile as prof
from cvdrafter.textutil import safe, safe_html

SERIES = "#54b8d3"
CSS = """
<style>
:root{--ink:#e5edf4;--muted:#91a0ae;--line:#293744;--paper:#121b24;--wash:#0b1117;--teal:#37b7d2;--teal-dark:#86d9e9;--mint:#122932}
html,body,[class*="css"]{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:var(--ink)}.stApp{background:var(--wash)}
[data-testid="stHeader"]{background:transparent;height:2.25rem}
[data-testid="stSidebar"]{background:#0e151d;border-right:1px solid var(--line)}[data-testid="stSidebar"]>div:first-child{padding-top:1rem}
[data-testid="stSidebar"][aria-expanded="true"]{min-width:220px!important;max-width:220px!important}[data-testid="stSidebarNav"]{padding-top:.2rem}[data-testid="stSidebarNav"] li{margin:.02rem .35rem}
[data-testid="stSidebarNav"] a{border-radius:5px;padding:.3rem .65rem;color:#9cabb8;font-weight:550;font-size:.92rem}
[data-testid="stSidebarNav"] a:hover{background:#15232e;color:#d9e4eb}[data-testid="stSidebarNav"] a[aria-current="page"]{background:#142934;color:#6dd0e4;border-left:2px solid var(--teal)}
.block-container{padding-top:2.6rem;padding-bottom:2.5rem;max-width:1500px}
h1,h2,h3{font-family:Inter,ui-sans-serif,system-ui,sans-serif!important;color:var(--ink)!important;letter-spacing:-.025em}h1{font-size:1.9rem!important;font-weight:700!important}h2{font-size:1.3rem!important;font-weight:650!important}h3{font-size:1.05rem!important;font-weight:650!important}h4{font-size:.98rem!important;font-weight:650!important}
[data-testid="stCaptionContainer"]{color:var(--muted)}
[data-testid="stMetric"]{background:var(--paper);border:1px solid var(--line);padding:.85rem 1rem;border-radius:7px}
[data-testid="stMetricLabel"]{color:var(--muted);font-weight:550}[data-testid="stMetricValue"]{font-family:ui-monospace,SFMono-Regular,monospace;color:var(--ink);font-size:1.65rem}
.stButton>button,.stDownloadButton>button{border-radius:5px;border:1px solid #344554;font-weight:600;min-height:2.35rem;transition:all .12s ease;background:#151f29;color:#dce6ed}
.stButton>button[kind="primary"],.stDownloadButton>button[kind="primary"]{background:#167c95;border-color:#218da6;color:#f4fbfd}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--teal);color:#a8e9f3}.stButton>button[kind="primary"]:hover{background:#1b8ca5;color:white}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-testid="stSelectbox"] [role="combobox"],[data-testid="stMultiSelect"] [role="combobox"]{border-radius:5px;background:#1b2834!important;border-color:#4a6074;color:var(--ink)}
[data-testid="stTextInput"] [data-baseweb="input"],[data-testid="stNumberInput"] [data-baseweb="input"],[data-testid="stTextArea"] [data-baseweb="textarea"],[data-testid="stDateInput"] [data-baseweb="input"],[data-testid="stSelectbox"] [data-baseweb="select"]>div,[data-testid="stMultiSelect"] [data-baseweb="select"]>div{border:1px solid #56708a!important;border-radius:5px;background:#1b2834!important}
[data-testid="stTextInput"] [data-baseweb="input"]:focus-within,[data-testid="stNumberInput"] [data-baseweb="input"]:focus-within,[data-testid="stTextArea"] [data-baseweb="textarea"]:focus-within,[data-testid="stDateInput"] [data-baseweb="input"]:focus-within,[data-testid="stSelectbox"] [data-baseweb="select"]>div:focus-within,[data-testid="stMultiSelect"] [data-baseweb="select"]>div:focus-within{border-color:var(--teal)!important}
[data-testid="stExpander"]{background:#101820;border:1px solid var(--line);border-radius:6px}
[data-testid="stTabs"] [role="tab"]{font-weight:600;color:var(--muted)}[data-testid="stTabs"] [aria-selected="true"]{color:#70d1e4}
[data-testid="stDataFrame"],[data-testid="stTable"]{border:1px solid var(--line);border-radius:6px;overflow:hidden}
[data-testid="stChatMessage"]{background:#111a23;border:1px solid var(--line);border-radius:6px;padding:.75rem .9rem;margin-bottom:.5rem}
[data-testid="stChatInput"] textarea{border-radius:6px;background:#1b2834;border-color:#4a6074}div[data-testid="stAlert"]{border-radius:5px}
.kpi{border:1px solid var(--line);border-radius:6px;padding:14px 15px;background:#111a23;min-height:98px}
.kpi .v{font-family:ui-monospace,SFMono-Regular,monospace;font-size:1.65rem;font-weight:650;line-height:1.1;color:var(--ink)}.kpi .l{font-size:.68rem;font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px}.kpi .s{font-size:.76rem;color:var(--muted);margin-top:5px}
.card{border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:7px;background:#111a23}.card .t{font-weight:650;color:var(--ink)}.card .m{font-size:.78rem;color:var(--muted);margin-top:3px}
.pill{display:inline-block;border-radius:4px;padding:3px 8px;font-size:.72rem;font-weight:550;margin:2px 3px 2px 0;border:1px solid #344554}.pill.ok{background:#102a2f;color:#7dd4cf;border-color:#24535b}.pill.no{background:#2c1b20;color:#e89a9a;border-color:#61333b}
.src{font-size:.76rem;color:var(--muted);margin:-6px 0 10px}.eyebrow{font-family:ui-monospace,SFMono-Regular,monospace;font-size:.69rem;font-weight:650;letter-spacing:.12em;text-transform:uppercase;color:#68cce0}
.hero{background:#101a23;border:1px solid #2b3a47;border-left:3px solid #36afc8;border-radius:6px;padding:22px 25px;margin:.2rem 0 1.1rem}.hero-title{font-family:Inter,ui-sans-serif,system-ui,sans-serif;font-size:1.55rem;font-weight:700;letter-spacing:-.03em;color:var(--ink);margin:.3rem 0 .35rem}.hero-copy{color:#9aabb8;max-width:700px;font-size:.9rem;line-height:1.5}
.fit{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-left:4px solid var(--c);border-radius:6px;padding:10px 14px;margin:6px 0 8px;background:#111a23}.fit .b{font-weight:700;color:var(--c);font-size:1.05rem;white-space:nowrap}.fit .n{font-family:ui-monospace,SFMono-Regular,monospace;font-weight:650;color:var(--c);font-size:1.4rem;line-height:1}.fit .m{font-size:.8rem;color:var(--muted)}.fit.strong{--c:#5fd39a}.fit.good{--c:#54b8d3}.fit.stretch{--c:#e3b85b}.fit.no{--c:#e88686}.fit.unclear{--c:#91a0ae}
@media(max-width:800px){.block-container{padding-top:1rem}.hero{padding:18px}.hero-title{font-size:1.3rem}}
</style>"""


def setup() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def load_chat(name: str, default: list[dict]) -> list[dict]:
    """A chat as it was when you last used it, so closing and reopening the app loses nothing."""
    import json
    try:
        turns = json.loads((config.DATA_DIR / "chats" / f"{name}.json").read_text(encoding="utf-8"))
        if isinstance(turns, list) and turns and all(isinstance(t, dict) and "content" in t for t in turns):
            return turns
    except (OSError, ValueError):
        pass
    return default


def save_chat(name: str, turns: list[dict]) -> None:
    """Undo buttons stay in this session only: after a restart the fact bank may have changed since."""
    import json
    folder = config.DATA_DIR / "chats"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{name}.json.tmp"
    tmp.write_text(json.dumps([{k: v for k, v in t.items() if k != "undo"} for t in turns[-200:]], ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(folder / f"{name}.json")  # never a half-written file


def bullets(items) -> str:
    """A markdown bullet list of untrusted (AI or posting) text."""
    return "\n".join(f"- {safe(x)}" for x in items or [])


ROLES = {"writer": ("Writer", "Analyses postings and writes the CV + cover letter. Quality matters most here."),
         "checker": ("Fact-checker", "Checks every statement against your facts. A different, stronger model catches more."),
         "fast": ("Chat commands", "Turns short chat messages into actions. A small, fast model is enough.")}


def models() -> dict:
    """The models picked in the app (saved in data/settings.json), loaded into the session once."""
    s = st.session_state
    saved = config.load_settings()
    s.setdefault("allow_paid", saved["allow_paid"])
    s.setdefault("auto_write", saved["auto_write"])
    for role in ROLES:
        s.setdefault(f"{role}_model", saved["models"].get(role) or config.default_model(role))
    s.setdefault("llm_check", True)
    return {"writer": s["writer_model"], "checker": s["checker_model"], "fast": s["fast_model"], "llm_check": s["llm_check"]}


def _save_models() -> None:
    from cvdrafter import pricing
    s = st.session_state
    if not s["allow_paid"]:  # paid off: any paid pick falls back to a free model
        free = [m for m in pricing.options(False, bool(config.api_key()))]
        for role in ROLES:
            if pricing.is_paid(s[f"{role}_model"]) and free:
                default = config.default_model(role)
                s[f"{role}_model"] = default if default in free else free[0]
    config.save_settings({"allow_paid": s["allow_paid"], "models": {r: s[f"{r}_model"] for r in ROLES},
                          "auto_write": s.get("auto_write", False)})


def reset_models() -> None:
    for role in ROLES:
        st.session_state[f"{role}_model"] = config.default_model(role)
    _save_models()


def auto_write_toggle(container) -> None:
    container.toggle("Auto-write CV + letter", key="auto_write", on_change=_save_models,
                     help="Off: a pasted posting is only logged and analysed (~10k tokens). Writing the CV + letter "
                          "and fact-checking it costs another ~25-50k tokens, so it waits until you ask.")


def paid_toggle(container) -> None:
    container.toggle("Paid models", key="allow_paid", on_change=_save_models,
                     help="Off: only calls that cost nothing extra run (Claude Code on your own Claude plan, OpenRouter "
                          "':free' models). Your Anthropic or OpenRouter key bills per call, so it needs this on.")


def cost_line() -> str:
    from cvdrafter.llm import usage_summary
    u = usage_summary()
    b = config.backend()
    if b == "claude":
        plan = (config.claude_auth().get("subscriptionType") or "").title()
        mode = f"Claude Code on your {plan} plan" if plan else "Claude Code"
    else:
        mode = {"anthropic": "your Anthropic key", "openrouter": "your OpenRouter key"}.get(b, "no AI connected yet")
    return f"{mode} · billed ${u['billed_month']:.2f} this month"


def model_controls(container) -> None:
    """Model pickers with prices, the fact-check switch, and what you've spent."""
    from cvdrafter import pricing
    from cvdrafter.llm import USAGE, usage_summary
    s = st.session_state
    opts = pricing.options(s["allow_paid"], bool(config.api_key()))
    with container:
        for role, (label, help_text) in ROLES.items():
            current = s[f"{role}_model"]
            st.selectbox(label, opts if current in opts else [current, *opts], key=f"{role}_model", format_func=pricing.label,
                         help=help_text + " You can also type any OpenRouter id (vendor/model).",
                         accept_new_options=True, on_change=_save_models)
        if any(s[f"{role}_model"] != config.default_model(role) for role in ROLES):
            st.warning("You've changed the models. The app is built and tested for Claude Sonnet: other models "
                       "write weaker CVs and letters and can miss mistakes in the fact-check.")
            st.button("Back to recommended models", on_click=reset_models, type="primary")
        st.checkbox("AI fact-check pass", key="llm_check", help="Rule-based checks always run.")
        if not s["allow_paid"]:
            st.caption("Paid models are off, so only free models are listed. Turn on 'Paid models' to see OpenRouter's priced models.")
        elif not (config.api_key() or config.anthropic_key()):
            st.caption("Add an API key on the Connect AI page to use API models.")
        u = usage_summary()
        # "\\$": a bare $ starts LaTeX in st.markdown
        st.markdown(f"**Billed:** \\${u['billed_today']:.2f} today · \\${u['billed_month']:.2f} this month · "
                    f"\\${u['billed_ever']:.2f} in total")
        if u["calls"]:
            st.caption(f"This session: {u['calls']} call(s), {(u['input'] + u['cached'] + u['output']) / 1000:.1f}k tokens "
                       f"({u['cached'] / max(1, u['input'] + u['cached']):.0%} of input from cache). Free calls would have cost "
                       f"\\${u['api_value']:.2f} at API prices.")
            st.dataframe([{"task": x["task"], "model": x["model"], "via": x["via"], "tokens": x["input"] + x["cached"] + x["output"],
                           "billed $": round(x["billed"], 4)} for x in reversed(USAGE)], hide_index=True, width="stretch")


def settings_sidebar() -> None:
    """Loads the saved model settings. The sidebar holds only the menu (and Quit when started from the app)."""
    models()
    if os.getenv("CVD_APP"):  # started from CV Drafter.app: the server runs in the background until you quit
        with st.sidebar:
            quit_button(st)


def quit_button(container) -> None:
    if container.button("Quit CV Drafter", help="Stops the app. Open CV Drafter again any time; nothing is lost."):
        st.info("CV Drafter has stopped. You can close this tab.")
        threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        st.stop()


def load_profile_or_stop() -> dict:
    if not config.PROFILE_PATH.exists():
        st.info("**Welcome!** Two steps before your first application: connect an AI, then drop your CV into the "
                "chat on Your profile. It becomes your fact bank, the list of true facts every CV and letter is "
                "written from.")
        c1, c2 = st.columns(2)
        c1.page_link("ui/connect_page.py", label="1. Connect AI", icon="🔌")
        c2.page_link("ui/files_page.py", label="2. Add your CV (Your profile → drop it into the chat)", icon="📄")
        st.stop()
    try:
        return prof.load_profile()
    except Exception as e:
        st.error(f"Could not read data/profile.yaml: {e}. Fix it on the Files page.")
        st.stop()


def kpi(col, label: str, value, sub: str = "") -> None:
    col.markdown(f'<div class="kpi"><div class="l">{label}</div><div class="v">{value}</div><div class="s">{sub}</div></div>',
                 unsafe_allow_html=True)


def pills(terms: list[str], kind: str = "") -> str:
    return " ".join(f'<span class="pill {kind}">{safe(t)}</span>' for t in terms)


@st.cache_data(show_spinner=False, max_entries=20)
def _pdf_pages(path: str, mtime: float) -> list[bytes]:  # mtime: re-render when the file changes
    from cvdrafter.pdf import page_images
    return page_images(path)


def document_previews(app: dict, profile: dict) -> None:
    """Cover letter on top, CV below. The rendered PDF when there is one (exact), else the HTML preview of the saved text."""
    from pathlib import Path

    from cvdrafter.preview import cl_html, cv_html
    draft = app["final"] or app["generated"]
    files = app.get("files") or {}
    for key, title, html in (("cl", "Cover letter", lambda: cl_html(draft, profile, app["company"])),
                             ("cv", "CV", lambda: cv_html(draft, profile))):
        st.markdown(f"**{title}**")
        pdf = files.get(f"{key}_pdf")
        pages = _pdf_pages(pdf, Path(pdf).stat().st_mtime) if pdf and Path(pdf).exists() else []
        if pages:
            st.caption(f"The rendered PDF ({len(pages)} page{'s' if len(pages) > 1 else ''}).")
            for img in pages:
                st.image(img, width="stretch")
        elif draft:
            st.caption("Preview of the saved text (no PDF yet; render it in the editor for the exact file). "
                       + ("Recipient and address aren't saved, so the letter shows the default greeting." if key == "cl" else ""))
            st.html(f"<div style='zoom:.75;overflow-x:auto'>{html()}</div>")
        else:
            st.caption("Nothing generated yet.")


def match_panel(draft: dict, analysis: dict, profile: dict, key: str) -> None:
    """Every posting keyword and requirement, and the exact CV / letter lines that cover it."""
    from cvdrafter.matches import match_map
    m = match_map(draft, analysis, profile)
    kws, reqs = m["keywords"], m["requirements"]
    used = [k for k in kws if k["state"] == "used"]
    have = [k for k in kws if k["state"] != "blocked"]
    must = [r for r in reqs if r["importance"] == "must"]
    must_in = [r for r in must if r["where"]]
    c1, c2 = st.columns(2)
    c1.metric("Keywords used", f"{len(used)}/{len(have)}", help="Posting keywords you have, and how many this draft uses.")
    c2.metric("Must-haves shown", f"{len(must_in)}/{len(must)}", help="Must-have requirements a CV/letter line cites evidence for.")

    filt = st.radio("Show", ["Gaps first", "All", "Matched only"], horizontal=True, label_visibility="collapsed",
                    key=f"mfilter_{key}")
    st.markdown("**Keywords**")
    order = {"unused": 0, "blocked": 1, "used": 2} if filt == "Gaps first" else {"used": 0, "unused": 1, "blocked": 2}
    icon = {"used": "✅", "unused": "🟡", "blocked": "⛔"}
    note = {"unused": "you have it, not used in this draft", "blocked": "not in your profile, kept out"}
    for k in sorted(kws, key=lambda k: order[k["state"]]):
        if filt == "Matched only" and k["state"] != "used":
            continue
        tag = "" if k["weight"] == "required" else f" · {k['weight']}"
        detail = ", ".join(k["where"]) if k["where"] else note[k["state"]]
        st.markdown(f"{icon[k['state']]} **{safe(k['term'])}**{safe(tag)}  \n<small>{safe(detail)}</small>", unsafe_allow_html=True)

    st.markdown("**Requirements**")
    r_icon = {"covered": "✅", "partial": "🟡", "missing": "❌"}
    rank = {"missing": 0, "partial": 1, "covered": 2}
    for r in sorted(reqs, key=lambda r: (rank.get(r["status"], 0) if filt == "Gaps first" else 0, r["importance"] != "must")):
        if filt == "Matched only" and not r["where"]:
            continue
        flag = "" if r["shown"] else " · ⚠️ you have evidence, but it's not in this draft"
        where = ", ".join(r["where"]) or (r.get("comment") or "no evidence in your profile")
        st.markdown(f"{r_icon.get(r['status'], '•')} {safe(r['text'])} <small>({safe(r['importance'])}){safe(flag)}</small>"
                    f"  \n<small>{safe(where)}</small>", unsafe_allow_html=True)


APP_DETAILS = [("location", "Location"), ("work_mode", "Work mode"), ("employment_type", "Type"), ("salary", "Pay"),
               ("deadline", "Deadline"), ("job_ref", "Job id")]
CONFIDENCE = {"high": "company confirmed", "medium": "company: please check", "low": "company: please check"}


def company_confidence(app: dict) -> str:
    return app.get("company_confidence") or ((app.get("analysis") or {}).get("company_check") or {}).get("confidence", "")


def confirm_company_button(app: dict, key: str) -> None:
    """Rendering is blocked while the company name is a guess; this confirms it without retyping."""
    from cvdrafter import memory
    if company_confidence(app) in ("low", "medium") and st.button(
            f"“{app['company'] or '?'}” is the right company", key=f"cc_{key}", disabled=not app["company"],
            help="Documents won't render until you confirm it."):
        memory.update(app["id"], company_confidence="high")
        st.rerun()


def _save_field(app_id: int, field: str, key: str) -> None:
    from datetime import date

    from cvdrafter import memory
    value = st.session_state[key]
    if value is None and field == "status":  # clicking the selected status again deselects it; keep the old one
        return
    value = value.isoformat() if isinstance(value, date) else value
    extra = {"company_confidence": "high"} if field == "company" else {}
    memory.update(app_id, **{field: value}, **extra)
    if field == "status" and value == "sent" and not memory.get(app_id).get("sent_at"):
        memory.mark_applied(app_id)


def application_card(app: dict, key: str) -> None:
    """Status, company, role, dates and job details of one application. Every field saves as you edit it."""
    from datetime import date

    from cvdrafter import memory
    app_id = app["id"]
    k = lambda name: f"card::{key}::{name}"  # noqa: E731

    def text(col, field, label):
        col.text_input(label, app.get(field) or "", key=k(field), on_change=_save_field, args=(app_id, field, k(field)))

    def day(col, field, label):
        col.date_input(label, date.fromisoformat(app[field][:10]) if app.get(field) else None, key=k(field),
                       on_change=_save_field, args=(app_id, field, k(field)))

    with st.container(border=True):
        st.markdown(f"#### {safe(app['company']) or '?'} · {safe(app['role'])}")
        st.caption(f"\\#{app_id} · logged {app['created_at'][:10]} · {CONFIDENCE.get(company_confidence(app), '')}")
        st.segmented_control("Status", memory.STATUSES, default=app["status"], key=k("status"),
                             format_func=lambda x: x.title(), label_visibility="collapsed",
                             on_change=_save_field, args=(app_id, "status", k("status")))
        c = st.columns([3, 3, 2, 2])
        text(c[0], "company", "Company")
        text(c[1], "role", "Role")
        day(c[2], "sent_at", "Applied on")
        day(c[3], "follow_up_at", "Follow up")
        c = st.columns(len(APP_DETAILS) + 1)
        for col, (field, label) in zip(c, APP_DETAILS):
            text(col, field, label)
        text(c[-1], "url", "URL")
        confirm_company_button(app, key)


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def download_buttons(files: dict | None, key: str) -> bool:
    """Word files first (the default), PDFs as a secondary option. False if nothing has been rendered yet."""
    from pathlib import Path
    docs = {k: Path(v) for k, v in (files or {}).items() if k.endswith(("_docx", "_pdf")) and Path(v).exists()}
    if not docs:
        return False
    cols = st.columns(4)
    for col, (k, label) in zip(cols, (("cv_docx", "CV"), ("cl_docx", "Cover letter"), ("cv_pdf", "CV"), ("cl_pdf", "Cover letter"))):
        path = docs.get(k)
        if path:
            docx = k.endswith("_docx")
            col.download_button(f"{label} (.{'docx' if docx else 'pdf'})", path.read_bytes(), file_name=path.name,
                                mime=DOCX_MIME if docx else "application/pdf", type="primary" if docx else "secondary",
                                key=f"dl_{key}_{k}", width="stretch")
    return True


def show_issues(issues: list[dict]) -> None:
    if not issues:
        st.success("No problems found. Every statement traces back to your fact bank.")
    for i in issues:
        (st.error if i["level"] == "error" else st.warning)(f"**{safe(i['location'])}**: {safe(i['message'])}")


def hbar(rows: list[dict], label: str, value: str, title: str = "", tooltip: list | None = None, height_per_row: int = 24,
         keep_order: bool = False):
    """Single-series horizontal bar chart: one hue, thin bars, value labels, hover tooltip."""
    if not rows:
        st.caption("No data yet.")
        return
    if not keep_order:
        rows = sorted(rows, key=lambda r: -r[value])
    order = [r[label] for r in rows]
    base = alt.Chart(alt.Data(values=rows)).encode(
        y=alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False, ticks=False)),
        x=alt.X(f"{value}:Q", title=title, axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.3)),
        tooltip=tooltip or [f"{label}:N", f"{value}:Q"],
    )
    bars = base.mark_bar(color=SERIES, cornerRadiusEnd=4, size=12)
    text = base.mark_text(align="left", dx=4, fontSize=11).encode(text=f"{value}:Q")
    st.altair_chart((bars + text).properties(height=alt.Step(height_per_row)), width="stretch")


# ---------------------------------------------------------------- render gate

def render_checked(app: dict, draft: dict, profile: dict, key: str, override: bool = False, **kw) -> dict | None:
    """Re-check the exact text, then render. Returns None (and remembers why) if errors remain and override is off."""
    import hashlib
    import json

    from cvdrafter import memory, render
    from cvdrafter.verify import evaluate, issue
    m = models()
    checker = m["checker"] if m["llm_check"] else None
    digest = hashlib.sha256(json.dumps([draft, checker, app.get("jd")], sort_keys=True, default=str).encode()).hexdigest()
    cache = st.session_state.setdefault("evaluations", {})
    ev = cache.get(digest)
    if not ev:  # the same text is never checked twice, so an override doesn't pay for a second AI call
        with st.spinner("Checking every statement against your facts…"):
            ev = evaluate(draft, profile, app.get("analysis"), app.get("jd") or "", app.get("notes") or "",
                          config.load_slots()["limits"], checker)
        if ev["ai_checked"] or not checker:  # a fact-check that couldn't run is retried next time
            cache[digest] = ev
    saved = memory.get(app["id"]) or app  # the name as corrected on any page, not a stale copy
    app = {**app, "company": saved["company"], "role": saved["role"]}
    conf = saved.get("company_confidence") or ((saved.get("analysis") or {}).get("company_check") or {}).get("confidence")
    if conf in ("low", "medium"):
        ev = {**ev, "status": "errors", "errors": ev["errors"] + 1,
              "issues": [issue("error", "company", f"“{app['company'] or '?'}” is a guess. Confirm or correct the company name."),
                         *ev["issues"]]}
    if ev["errors"] and not override:
        st.session_state[f"blocked_{key}"] = ev
        return None
    st.session_state.pop(f"blocked_{key}", None)
    with st.spinner("Rendering… (Word may take a few seconds for the PDF)"):
        files = render.render_documents(draft, profile, app["company"], app["role"], app_id=app["id"],
                                        evaluation={**ev, "overridden": bool(ev["errors"])}, **kw)
    memory.log_event(app["id"], "rendered", "Word files created" + (" (despite errors)" if ev["errors"] else ""))
    return files


def blocked_panel(key: str) -> bool:
    """Shows why the last render was stopped. True when the user chooses to render anyway."""
    ev = st.session_state.get(f"blocked_{key}")
    if not ev:
        return False
    st.error(f"Not rendered: {ev['errors']} problem(s) in the text. Fix them (or rewrite), then render again.")
    show_issues([i for i in ev["issues"] if i["level"] == "error"])
    with st.popover("Render anyway"):
        st.write("Only if you've checked every problem above yourself. The render is marked as overridden in History.")
        return st.button("I checked them: render anyway", key=f"override_{key}", type="primary")


def fit_card(analysis: dict, profile: dict, key: str) -> dict:
    """The fit verdict for a posting as a coloured badge, with its reasons one click away."""
    f = fit.assess(analysis or {}, profile)
    detail = f["summary"].split(" · ", 1)[1] if " · " in f["summary"] else ""
    score = f'<span class="n">{f["score"]}</span>' if f["score"] is not None else ""
    st.markdown(f'<div class="fit {f["level"]}">{score}<div><div class="b">{f["verdict"]}</div>'
                f'<div class="m">{safe_html(detail)}</div></div></div>', unsafe_allow_html=True)
    with st.expander("Why this verdict", expanded=False, key=f"fitwhy_{key}"):
        for title, items in (("Blockers", f["blockers"]), ("Strengths", f["strengths"]), ("Gaps", f["gaps"]),
                             ("Worth checking", f["risks"])):
            if items:
                st.markdown(f"**{title}**\n" + bullets(items))
        st.caption("Score: must-haves count 3x, nice-to-haves 1x, soft skills half. Direct evidence earns full credit, "
                   "an interpretation (e.g. varsity sport for discipline) 80%, partial 50%. A missed must-have on "
                   "location, dates, graduation or authorization, or a mid/senior role, makes it Not a fit.")
        interpretation_form(profile, key)
    return f


def interpretation_form(profile: dict, key: str) -> None:
    """Say once that one of your facts shows a skill or trait; every posting that asks for it counts it as met."""
    facts = {i: f for i, f in prof.index_facts(profile).items() if f["section"] != "skills"}
    with st.form(f"interp_{key}", clear_on_submit=True, border=False):
        st.markdown("**Undersold? Count one of your facts for a skill or trait**")
        fact = st.selectbox("Your fact", list(facts), format_func=lambda i: facts[i]["text"][:110])
        shows = st.text_input("shows (comma-separated)", placeholder="leadership, teamwork")
        why = st.text_input("Why (optional)", placeholder="captained the team for two years")
        if st.form_submit_button("Confirm") and shows.strip():
            prof.add_interpretation(fact, [s.strip() for s in shows.split(",") if s.strip()], why)
            st.rerun()
    n = len(prof.load_interpretations())
    if n:
        st.caption(f"{n} confirmed interpretation(s). They count for the fit verdict only; your CV and letter still say "
                   "only what your facts say. Edit them in interpretations.yaml on Files & fact bank.")
