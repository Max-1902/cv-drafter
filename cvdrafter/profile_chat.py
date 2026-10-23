"""Profile chat: drop in files or tell the AI something about you, and it goes straight into your fact bank.

The AI only proposes changes as small operations; this module applies them in code, so a bad reply can never
corrupt profile.yaml. Every save keeps the previous version, so any change can be undone.
"""
from __future__ import annotations

import copy
import re

import yaml

from . import config, files, importer, prompts
from .llm import chat_json
from .profile import ENTRY_SECTIONS, facts_for_prompt, profile_from_raw, validate_profile
from .textutil import safe, slug

FILE_TYPES = ["pdf", "docx", "txt", "md"]
MAX_DOC_CHARS = 30000  # per file: a long transcript still fits, a 200-page PDF doesn't flood the prompt
HEADER_FIELDS = {"experience": ("company", "title", "location", "dates"), "projects": ("name", "stack"),
                 "education": ("institution", "degree", "location", "dates", "gpa", "coursework")}
LABEL = {"experience": "company", "projects": "name", "education": "institution"}
LISTS = ("languages", "activities", "certifications")
PERSONAL = ("name", "location", "linkedin", "github", "website", "graduation")
CV_NAME = re.compile(r"\b(cv|resume|résumé|lebenslauf|curriculum)\b", re.I)
HEADER = ("# Your fact bank: every true fact about you. CVs and cover letters are only written from what's here.\n"
          "# Edit it on the Your profile page, or just tell the AI chat there.\n\n")


class ChatError(RuntimeError):
    """A problem to show the person in plain words."""


def read_file(name: str, data: bytes) -> str:
    """The text of one dropped file, or ChatError with what to do instead."""
    if not name.lower().endswith(tuple(f".{t}" for t in FILE_TYPES)):
        raise ChatError(f"I can't read {name}. Drop a PDF, Word (.docx) or text file.")
    try:
        text = importer.extract_text(data, name)
    except Exception:
        raise ChatError(f"I couldn't open {name}. Is it a damaged or password-protected file? Try saving it again "
                        "as PDF or Word.") from None
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(text) < 20:
        raise ChatError(f"{name} has no readable text (it may be a scan or photo). Copy the text into the chat "
                        "instead, or export it as a text PDF.")
    return text[:MAX_DOC_CHARS]


def _raw_text() -> str:
    return files.read("profile.yaml")


def _load_raw(text: str) -> dict:
    try:
        raw = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError:
        raise ChatError("Your fact bank file has a typing error, so I can't add to it safely. Fix it under "
                        "'Edit files', or restore an older version under 'Version history'.") from None
    return raw if isinstance(raw, dict) else {}


def _entry_id(entry: dict, section: str) -> str:
    return str(entry.get("id") or slug(" ".join(str(entry.get(k) or "") for k in ENTRY_SECTIONS[section])))


def _fact_list(raw: dict, fact_id: str) -> tuple[list, int, str] | None:
    """(the list holding this fact, its index, the parent id) for a fact id, the way profile.py numbers them."""
    holders = [(raw.get("general_facts") or [], "general")]
    holders += [(e.get("facts") or [], _entry_id(e, s)) for s in ENTRY_SECTIONS for e in raw.get(s) or []
                if isinstance(e, dict)]
    for facts, parent in holders:
        for i, f in enumerate(facts, 1):
            if f is None:
                continue
            fid = str(f.get("id") or f"{parent}.{i}") if isinstance(f, dict) else f"{parent}.{i}"
            if fid == fact_id:
                return facts, i - 1, parent
    return None


def _pin_ids(facts: list, parent: str) -> None:
    """Give every fact its current id explicitly, so removing one doesn't renumber the rest (old drafts cite them)."""
    for i, f in enumerate(facts, 1):
        if f is None:
            continue
        if not isinstance(f, dict):
            facts[i - 1] = {"id": f"{parent}.{i}", "text": str(f)}
        elif not f.get("id"):
            f["id"] = f"{parent}.{i}"


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _new_fact(text: str, skills) -> dict | str:
    skills = [_clean(s) for s in (skills or []) if _clean(s)] if isinstance(skills, list) else []
    return {"text": text, "skills": skills} if skills else text


def _known_texts(raw: dict) -> set[str]:
    out = set()
    for facts in [raw.get("general_facts") or []] + [e.get("facts") or [] for s in ENTRY_SECTIONS
                                                      for e in raw.get(s) or [] if isinstance(e, dict)]:
        out |= {_clean(f.get("text") if isinstance(f, dict) else f).lower() for f in facts if f is not None}
    return out


def apply_changes(raw: dict, changes) -> tuple[dict, list[str]]:
    """The fact bank with the AI's changes applied, and a plain-words line per change. Invalid changes are skipped."""
    raw = copy.deepcopy(raw)
    done: list[str] = []
    known = _known_texts(raw)
    entries = {_entry_id(e, s): (s, e) for s in ENTRY_SECTIONS for e in raw.get(s) or [] if isinstance(e, dict)}

    for ch in changes if isinstance(changes, list) else []:
        if not isinstance(ch, dict):
            continue
        op = ch.get("op")
        text = _clean(ch.get("text"))
        if op == "add_fact" and text and text.lower() not in known:
            target = entries.get(str(ch.get("entry_id") or ""))
            if not target:  # an entry the AI made up: keep the fact as a general one rather than lose it
                op = "add_general_fact"
            else:
                section, entry = target
                entry["id"] = _entry_id(entry, section)  # pin it, so a later header edit keeps the fact ids
                entry.setdefault("facts", [])
                if entry["facts"] is None:
                    entry["facts"] = []
                entry["facts"].append(_new_fact(text, ch.get("skills")))
                known.add(text.lower())
                done.append(f"Added to {entry.get(LABEL[section]) or entry['id']}: {text}")
                continue
        if op == "add_general_fact" and text and text.lower() not in known:
            if raw.get("general_facts") is None:
                raw["general_facts"] = []
            raw["general_facts"].append(_new_fact(text, ch.get("skills")))
            known.add(text.lower())
            done.append(f"Added a general fact: {text}")
        elif op == "add_entry" and ch.get("section") in HEADER_FIELDS:
            section = ch["section"]
            fields = ch.get("fields") if isinstance(ch.get("fields"), dict) else {}
            entry = {k: _clean(fields.get(k)) for k in HEADER_FIELDS[section] if _clean(fields.get(k))}
            if not entry.get(LABEL[section]):
                continue
            eid, n = _entry_id(entry, section), 2
            while eid in entries:
                eid, n = f"{_entry_id(entry, section)}-{n}", n + 1
            facts = [_clean(f) for f in (ch.get("facts") or []) if isinstance(f, str) and _clean(f)]
            entry = {"id": eid, **entry, "facts": facts}
            if raw.get(section) is None:
                raw[section] = []
            raw[section].append(entry)
            entries[eid] = (section, entry)
            known |= {f.lower() for f in facts}
            what = {"experience": "job", "projects": "project", "education": "school"}[section]
            done.append(f"Added {what}: {entry[LABEL[section]]}" + (f" ({len(facts)} facts)" if facts else ""))
        elif op == "add_skills":
            category = _clean(ch.get("category")) or "Skills"
            if not isinstance(raw.get("skills"), dict):
                raw["skills"] = {}
            have = {str(s).lower() for items in raw["skills"].values() for s in items or []}
            new = [s for s in dict.fromkeys(_clean(x) for x in (ch.get("items") or []) if isinstance(x, str))
                   if s and s.lower() not in have]
            if new:
                raw["skills"][category] = [*(raw["skills"].get(category) or []), *new]
                done.append(f"Added skills ({category}): {', '.join(new)}")
        elif op == "add_item" and ch.get("list") in LISTS and text:
            items = raw.get(ch["list"]) or []
            if text.lower() not in {str(x).lower() for x in items}:
                raw[ch["list"]] = [*items, text]
                done.append(f"Added to {ch['list']}: {text}")
        elif op == "set_personal" and ch.get("field") in PERSONAL and _clean(ch.get("value")):
            if not isinstance(raw.get("personal"), dict):
                raw["personal"] = {}
            value = _clean(ch["value"])
            if raw["personal"].get(ch["field"]) != value:
                raw["personal"][ch["field"]] = value
                done.append(f"Set your {ch['field']}: {value}")
        elif op in ("edit_fact", "remove_fact"):
            found = _fact_list(raw, str(ch.get("fact_id") or ""))
            if not found:
                continue
            facts, i, parent = found
            _pin_ids(facts, parent)
            if op == "remove_fact":
                done.append(f"Removed: {_clean(facts[i].get('text'))}")
                facts.pop(i)
            elif text:
                facts[i]["text"] = text
                done.append(f"Changed a fact to: {text}")
    return raw, done


def _dump(raw: dict, old_text: str) -> str:
    comments = []
    for line in old_text.splitlines():
        if line.startswith("#") or not line.strip():
            comments.append(line)
        else:
            break
    head = "\n".join(comments).rstrip() + "\n\n" if any(c.strip() for c in comments) else HEADER
    return head + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=200)


def _save(raw: dict, old_text: str) -> None:
    try:
        profile_from_raw(raw)
    except Exception:
        raise ChatError("That change would have broken your fact bank, so I didn't save it. Try saying it "
                        "differently.") from None
    files.save("profile.yaml", _dump(raw, old_text))


def todo(raw: dict) -> list[str]:
    """What's still missing before the fact bank is good enough to write from, in plain words."""
    try:
        problems = validate_profile(profile_from_raw(raw))
    except Exception:
        return []
    plain = {"personal.name is empty.": "your name", "No experience entries.": "at least one job or internship",
             "No skills defined, so the skills section will be empty.": "your skills"}
    return [plain.get(p, p) for p in problems]


def _personal_summary(raw: dict) -> str:
    p = raw.get("personal") if isinstance(raw.get("personal"), dict) else {}
    return "\n".join(f"{k}: {'(saved)' if k in ('email', 'phone') else v}" for k, v in p.items() if v) or "(none yet)"


def _pick_cv(docs: list[tuple[str, str]]) -> int:
    """Which dropped file is the CV: one named like a CV, else the longest."""
    named = [i for i, (n, _) in enumerate(docs) if CV_NAME.search(n.replace("_", " ").replace("-", " "))]
    return named[0] if named else max(range(len(docs)), key=lambda i: len(docs[i][1]))


def handle(message: str, docs: list[tuple[str, str]], history: list[dict], model: str) -> dict:
    """One chat turn. docs: (file name, text). Returns {reply, done: [lines], undo: old profile text or None}.

    No fact bank yet and a file dropped: that file is imported as your CV first (the tested importer), then the
    rest of the message and the other files are added on top."""
    message = (message or "").strip()
    old_text = _raw_text()
    raw = _load_raw(old_text)
    done, replies = [], []
    docs = list(docs)

    if not config.PROFILE_PATH.exists() and docs:
        name, text = docs.pop(_pick_cv(docs))
        imported = yaml.safe_load(importer.draft_profile_yaml(text, model)) or {}
        if not isinstance(imported, dict) or not imported.get("experience") and not imported.get("education"):
            raise ChatError(f"I couldn't find a CV in {name}. Drop your CV first (PDF or Word), then other files.")
        raw = imported
        n = sum(len(e.get("facts") or []) for s in ENTRY_SECTIONS for e in raw.get(s) or [])
        done.append(f"Built your fact bank from {name}: {len(raw.get('experience') or [])} jobs, "
                    f"{len(raw.get('education') or [])} schools, {len(raw.get('projects') or [])} projects, {n} facts")
        replies.append(f"I read **{safe(name)}** and built your fact bank from it. Please look over it once under "
                       "*Fact bank editor*.")

    if message or docs:
        safe_docs = []
        for name, text in docs:
            redacted, _ = importer.redact_contacts(text)  # contact details never leave the Mac
            safe_docs.append(f"--- {name} ---\n{redacted}")
        msg, _ = importer.redact_contacts(message)
        bank = facts_for_prompt(profile_from_raw(raw)) if raw else "(empty: nothing added yet)"
        turns = "\n".join(f"{t['role']}: {t['content'][:600]}" for t in history[-6:]) or "(start)"
        data = chat_json(prompts.PROFILE_CHAT_SYSTEM, prompts.PROFILE_CHAT_USER.format(
            bank=bank, personal=_personal_summary(raw), history=turns, docs="\n\n".join(safe_docs) or "(none)",
            message=msg or "(no message: add what the attached documents show)"),
            model, temperature=0, max_tokens=16000, task="profile chat")
        raw, more = apply_changes(raw, data.get("changes"))
        done += more
        if str(data.get("reply") or "").strip():
            replies.append(safe(str(data["reply"]).strip()))  # AI text: no HTML, links or images

    if done:
        _save(raw, old_text)
    elif not replies:
        replies.append("I didn't find anything new to add. Tell me more, or drop in another file.")
    missing = todo(raw) if done else []
    if missing:
        replies.append("Still missing before you can apply: " + "; ".join(missing) + ".")
    return {"reply": "\n\n".join(replies), "done": done, "undo": old_text if done else None}


def undo(old_text: str) -> None:
    """Put the fact bank back to how it was before a chat change (the undone version stays in history)."""
    if old_text:
        files.save("profile.yaml", old_text)
    elif config.PROFILE_PATH.exists():  # there was no fact bank before: keep the file as a version, then remove it
        files.save("profile.yaml", "")
        config.PROFILE_PATH.unlink()
