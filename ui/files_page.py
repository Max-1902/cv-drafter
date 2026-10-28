"""Files: edit your fact bank and every config file, with validation and version history."""
from __future__ import annotations

import difflib
import shutil
import subprocess
import sys

import streamlit as st
import yaml

from cvdrafter import config, files, importer, profile_chat
from cvdrafter.profile import ENTRY_SECTIONS
from cvdrafter.textutil import slug
from cvdrafter.llm import LLMError
from cvdrafter.textutil import safe
from ui.common import bullets, load_chat, models, save_chat, setup

setup()
st.markdown("<style>textarea:not([data-testid=\"stChatInputTextArea\"]){font-family:ui-monospace,SFMono-Regular,Menlo,monospace!important;font-size:.85rem!important}</style>",
            unsafe_allow_html=True)
M = models()
st.title("Your profile")

t_chat, t_bank, t_raw, t_hist, t_out, t_import = st.tabs(["Add info (AI chat)", "Fact bank editor", "Edit files",
                                                            "Version history", "Generated documents", "Import CV"])


def leading_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            lines.append(line)
        else:
            break
    return "\n".join(lines).rstrip() + "\n\n" if lines else ""


def save_with_feedback(name: str, text: str) -> bool:
    problems = files.validate(name, text)
    if problems:
        for p in problems:
            st.error(p)
        return False
    backup = files.save(name, text)
    st.toast(f"Saved {name}" + (" (previous version kept)" if backup else ""))
    st.session_state.pop(f"raw_{name}", None)
    return True


# ---------------------------------------------------------------- AI chat: drop files, add context

def forget_editor_state() -> None:
    """The fact bank changed underneath the editor: drop its unsaved widget state so it shows the new facts."""
    for k in [k for k in st.session_state if str(k).startswith(("de_", "h_", "ls_", "rm_", "raw_profile"))]:
        del st.session_state[k]


with t_chat:
    S = st.session_state
    S.setdefault("profile_chat", load_chat("profile", [{"role": "assistant", "content": (
        "Hi! Drop in your **CV** to get started, and anything else that shows what you've done: reference letters, "
        "certificates, transcripts, a LinkedIn PDF. Or just tell me, e.g. *I also tutored maths for two years* or "
        "*I speak Spanish (B2)*. I add it to your fact bank right away; every change can be undone."
        if not config.PROFILE_PATH.exists() else
        "Tell me anything new about you, or drop in a file (reference letter, certificate, transcript, a newer "
        "CV…). I add it to your fact bank right away; every change can be undone.")}]))
    for n, turn in enumerate(S["profile_chat"]):
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("done"):
                st.markdown(bullets(turn["done"]))
            if turn.get("undo") is not None and n == len(S["profile_chat"]) - 1:
                if st.button("Undo this change", key=f"undo_{n}"):
                    profile_chat.undo(turn["undo"])
                    turn["undo"] = None
                    turn["done"] = [f"(undone) {x}" for x in turn["done"]]
                    S["profile_chat"].append({"role": "assistant", "content": "Done: your fact bank is back to how "
                                                                              "it was before that change."})
                    forget_editor_state()
                    save_chat("profile", S["profile_chat"])
                    st.rerun()

    said = st.chat_input("Tell me something about you, or drop in files…", accept_file="multiple",
                         file_type=profile_chat.FILE_TYPES, key="profile_chat_input")
    if said:
        message, dropped = (said.text or "").strip(), list(said.files or [])
        names = ", ".join(f.name for f in dropped)
        S["profile_chat"].append({"role": "user", "content": safe(message) + (f"\n\n📎 {safe(names)}" if names else "")})
        docs, problems = [], []
        for f in dropped:
            try:
                docs.append((f.name, profile_chat.read_file(f.name, f.getvalue())))
            except profile_chat.ChatError as e:
                problems.append(str(e))
        result = {"reply": "", "done": [], "undo": None}
        if docs or message:
            try:
                with st.spinner("Reading and adding to your fact bank…"):
                    result = profile_chat.handle(message, docs, S["profile_chat"][:-1], M["writer"])
            except (profile_chat.ChatError, LLMError) as e:
                problems.append(str(e))
            except Exception as e:  # never lose the conversation over an unexpected error
                problems.append(f"Something went wrong ({type(e).__name__}). Nothing was changed; please try again.")
        reply = "\n\n".join(x for x in [result["reply"], *(f"⚠️ {safe(p)}" for p in problems)] if x)
        S["profile_chat"].append({"role": "assistant", "content": reply or "Nothing to add.", "done": result["done"],
                                  "undo": result["undo"]})
        if result["done"]:
            forget_editor_state()
        save_chat("profile", S["profile_chat"])
        st.rerun()


# ---------------------------------------------------------------- structured fact-bank editor

with t_bank:
    text = files.read("profile.yaml")
    if not text.strip():
        st.info("No fact bank yet. Drop your CV into the **Add info (AI chat)** tab to create it.")
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        st.error(f"profile.yaml has a YAML error. Fix it in 'Edit files': {e}")
        st.stop()
    st.caption("Edit facts like a spreadsheet: add rows at the bottom, select rows and delete them. Headers (company, "
               "title, dates…) are printed on the CV exactly as written. Only facts here can ever appear in your "
               "applications. Saving keeps the previous version.")
    HEADER_KEYS = {"experience": ["company", "title", "location", "dates", "max_bullets"],
                   "projects": ["name", "stack", "max_bullets"],
                   "education": ["institution", "degree", "location", "dates", "gpa", "coursework"]}

    def header_inputs(keys, values, key):
        out = {}
        for col, k in zip(st.columns(len(keys)), keys):
            out[k] = col.text_input(k.replace("_", " "), str(values.get(k, "") if values.get(k) is not None else ""), key=f"{key}_{k}")
        return out

    def clean_header(header):
        out = {}
        for k, v in header.items():
            v = v.strip()
            if v:
                out[k] = int(v) if k == "max_bullets" and v.isdigit() else v
        return out

    def fact_rows(facts_list):
        rows = []
        for f in facts_list or []:
            f = {"text": f} if isinstance(f, str) else f
            rows.append({"id": f.get("id", ""), "text": f.get("text", ""), "skills": ", ".join(f.get("skills") or [])})
        return rows

    def to_facts(rows):
        out = []
        for r in rows:
            if not str(r.get("text") or "").strip():
                continue
            fact = {}
            if str(r.get("id") or "").strip():
                fact["id"] = str(r["id"]).strip()
            fact["text"] = str(r["text"]).strip()
            skills = [s.strip() for s in str(r.get("skills") or "").split(",") if s.strip()]
            if skills:
                fact["skills"] = skills
            out.append(fact if set(fact) != {"text"} else fact["text"])
        return out

    col_cfg = {"id": st.column_config.TextColumn("id", width="small", help="Leave empty to auto-number."),
               "text": st.column_config.TextColumn("fact", width="large"),
               "skills": st.column_config.TextColumn("skills (comma-separated)", width="medium")}
    edited_sections, edited_headers, removed = {}, {}, set()
    for section, label_key in (("experience", "company"), ("projects", "name"), ("education", "institution")):
        st.subheader(section.title())
        for i, entry in enumerate(raw.get(section) or []):
            with st.expander(f"{entry.get(label_key, '?')}  ·  {entry.get('title') or entry.get('degree') or entry.get('stack') or ''}"):
                edited_headers[(section, i)] = header_inputs(HEADER_KEYS[section], entry, f"h_{section}_{i}")
                rows = st.data_editor(fact_rows(entry.get("facts")), key=f"de_{section}_{i}", num_rows="dynamic",
                                      width="stretch", column_config=col_cfg)
                edited_sections[(section, i)] = rows
                if st.checkbox("Remove this entry when I save", key=f"rm_{section}_{i}"):
                    removed.add((section, i))
        with st.popover(f"Add {section.rstrip('s') if section != 'education' else 'school'}"):
            with st.form(f"add_{section}", clear_on_submit=True):
                header = header_inputs(HEADER_KEYS[section], {}, f"new_{section}")
                facts_text = st.text_area("Facts, one per line (exactly what you did)")
                if st.form_submit_button("Add and save") and header[label_key].strip():
                    new = clean_header(header) | {"facts": [x.strip() for x in facts_text.splitlines() if x.strip()]}
                    raw.setdefault(section, []).append(new)
                    if save_with_feedback("profile.yaml", leading_comments(text) + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=200)):
                        st.rerun()
    st.caption("Adding an entry saves straight away, so save your other edits first.")
    st.subheader("General facts (cover letter only)")
    general_rows = st.data_editor(fact_rows(raw.get("general_facts")), key="de_general", num_rows="dynamic",
                                  width="stretch", column_config=col_cfg)
    st.subheader("Skills")
    skill_rows = st.data_editor([{"category": c, "skills": ", ".join(v or [])} for c, v in (raw.get("skills") or {}).items()],
                                key="de_skills", num_rows="dynamic", width="stretch",
                                column_config={"skills": st.column_config.TextColumn(width="large")})

    st.subheader("Languages, activities, certifications (one per line)")
    list_cols = st.columns(3)
    lists = {name: col.text_area(name, "\n".join(map(str, raw.get(name) or [])), key=f"ls_{name}", height=120)
             for col, name in zip(list_cols, ("languages", "activities", "certifications"))}

    if st.button("Save fact bank", type="primary"):
        for (section, i), rows in edited_sections.items():
            entry = raw[section][i]
            # Fact ids come from the entry id. Pin it before a header edit, so past drafts and learned facts still match.
            entry.setdefault("id", slug(" ".join(str(entry.get(k, "")) for k in ENTRY_SECTIONS[section])))
            header = clean_header(edited_headers[(section, i)])
            for k in HEADER_KEYS[section]:
                entry.pop(k, None)
            raw[section][i] = {"id": entry["id"], **header, **{k: v for k, v in entry.items() if k not in header and k != "id"}}
            raw[section][i]["facts"] = to_facts(rows)
        for section in HEADER_KEYS:
            raw[section] = [e for i, e in enumerate(raw.get(section) or []) if (section, i) not in removed]
        for name, value in lists.items():
            raw[name] = [x.strip() for x in value.splitlines() if x.strip()]
        raw["general_facts"] = to_facts(general_rows)
        raw["skills"] = {r["category"].strip(): [s.strip() for s in str(r.get("skills") or "").split(",") if s.strip()]
                         for r in skill_rows if str(r.get("category") or "").strip()}
        new_text = leading_comments(text) + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=200)
        save_with_feedback("profile.yaml", new_text)


# ---------------------------------------------------------------- raw editor

with t_raw:
    name = st.selectbox("File", list(files.EDITABLE), format_func=lambda n: f"{n}: {files.EDITABLE[n][2]}")
    saved = files.read(name)
    key = f"raw_{name}"
    if key not in st.session_state:
        st.session_state[key] = saved
    st.text_area(name, key=key, height=560, label_visibility="collapsed")
    current = st.session_state[key]
    problems = files.validate(name, current)
    for p in problems:
        st.error(p)
    if current != saved:
        st.info("Unsaved changes")
        with st.expander("Diff vs saved"):
            diff = "\n".join(difflib.unified_diff(saved.splitlines(), current.splitlines(), "saved", "edited", lineterm=""))
            st.code(diff or "(no changes)", language="diff")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Save", type="primary", disabled=bool(problems) or current == saved):
        if save_with_feedback(name, current):
            st.rerun()
    if c2.button("Discard changes", disabled=current == saved):
        st.session_state.pop(key)
        st.rerun()
    if name == "slots.yaml":
        st.caption("Tip: lowering bullet_chars or bullets_per_job keeps the CV on one page.")


# ---------------------------------------------------------------- version history

with t_hist:
    name = st.selectbox("File ", list(files.EDITABLE))
    vers = files.versions(name)
    if not vers:
        st.caption("No earlier versions yet. Every save keeps the previous version here.")
    else:
        v = st.selectbox("Version", vers, format_func=lambda p: p.stem.replace("_", " "))
        old = v.read_text(encoding="utf-8")
        diff = "\n".join(difflib.unified_diff(old.splitlines(), files.read(name).splitlines(), "that version", "current", lineterm=""))
        st.code(diff or "(identical to current)", language="diff")
        if st.button(f"Restore this version of {name}"):
            if save_with_feedback(name, old):
                st.rerun()


# ---------------------------------------------------------------- outputs

with t_out:
    folders = files.output_folders()
    if not folders:
        st.caption("Rendered CVs and cover letters will appear here.")
    else:
        st.caption("Every render is kept as its own version. Each application's Versions tab shows them.")
    for folder in folders:
        fid = f"{folder.parent.name}_{folder.name}"
        with st.expander(f"{folder.parent.name} · {folder.name.replace('_', ' ')}"):
            for f in sorted(x for x in folder.iterdir() if x.suffix != ".json"):
                c1, c2 = st.columns([4, 1])
                c1.write(f.name)
                c2.download_button("", f.read_bytes(), file_name=f.name, key=f"o_{fid}_{f.name}")
            c1, c2 = st.columns(2)
            if sys.platform == "darwin" and c1.button("Show in Finder", key=f"open_{fid}"):
                subprocess.run(["open", str(folder)])
            with c2.popover("Delete folder"):
                if st.button("Delete permanently", key=f"del_{fid}"):
                    shutil.rmtree(folder)
                    st.rerun()
    st.divider()
    st.caption("Templates live in templates/. You can open cv_template.docx in Word and adjust fonts, spacing or colors by hand "
               "(keep the {{ }} tags intact). Rebuilding restores the default design.")
    with st.popover("Rebuild templates from scratch"):
        st.write("Overwrites templates/cv_template.docx and cl_template.docx with the default design.")
        if st.button("Rebuild"):
            r = subprocess.run([sys.executable, str(config.ROOT / "scripts" / "make_templates.py")], capture_output=True, text=True)
            (st.success if r.returncode == 0 else st.error)(r.stdout or r.stderr)


# ---------------------------------------------------------------- import

with t_import:
    st.caption("Draft a fact bank from a CV file (.docx/.pdf), for example a newer version of your CV. "
               "Review it, then save it over profile.yaml (the old version is kept in history). "
               "Your email and phone number are removed before the text is sent to the AI and filled back in here.")
    up = st.file_uploader("CV file", type=["docx", "pdf"])
    if up and st.button("Draft profile from this CV"):
        with st.spinner("Reading CV…"):
            try:
                st.session_state["imported_yaml"] = importer.draft_profile_yaml(importer.extract_text(up.getvalue(), up.name),
                                                                                M["writer"])
            except LLMError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Could not read this file: {e}")
    if "imported_yaml" in st.session_state:
        st.text_area("Draft profile.yaml", key="imported_yaml", height=480)
        if st.button("Save as profile.yaml"):
            save_with_feedback("profile.yaml", st.session_state["imported_yaml"])
