"""Editable config files with validation and automatic version history."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from . import config

VERSIONS_DIR = config.DATA_DIR / ".versions"

# name -> (path, kind, description)
EDITABLE = {
    "profile.yaml": (config.PROFILE_PATH, "yaml", "Fact bank: everything true about you. The AI can only use what's here."),
    "guardrails.yaml": (config.GUARDRAILS_PATH, "yaml", "Never-mention terms, banned phrases and claim guards. Enforced in code."),
    "style_rules.md": (config.STYLE_RULES_PATH, "markdown", "Voice and structure rules for every draft. Grows as the app learns from your edits."),
    "voice_sample.md": (config.VOICE_SAMPLE_PATH, "markdown", "A cover letter you wrote yourself. Drafts copy its voice and shape, and may reuse its lines about you."),
    "learned_facts.yaml": (config.LEARNED_FACTS_PATH, "yaml", "Facts you confirmed when answering gap questions."),
    "slots.yaml": (config.SLOTS_PATH, "yaml", "Layout limits (bullet length, counts, cover-letter words) and fixed labels."),
}


def read(name: str) -> str:
    path = EDITABLE[name][0]
    return path.read_text(encoding="utf-8") if path.exists() else ""


def validate(name: str, text: str) -> list[str]:
    """Problems that would break the app if this text were saved."""
    path, kind, _ = EDITABLE[name]
    if kind != "yaml":
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"Invalid YAML: {e}"]
    if name == "profile.yaml":
        from .profile import profile_from_raw, validate_profile
        try:
            return validate_profile(profile_from_raw(data or {}))
        except Exception as e:
            return [f"Profile can't be loaded: {e}"]
    if name == "slots.yaml":
        missing = [k for k in ("limits", "labels") if not isinstance((data or {}).get(k), dict)]
        return [f"Missing section: {k}" for k in missing]
    if name == "learned_facts.yaml" and data is not None and not isinstance(data, list):
        return ["learned_facts.yaml must be a list."]
    return []


def save(name: str, text: str) -> Path | None:
    """Write the file, keeping the previous content as a version. Returns the backup path."""
    path = EDITABLE[name][0]
    backup = None
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == text:
            return None
        folder = VERSIONS_DIR / name
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup, n = folder / f"{stamp}{path.suffix}", 2
        while backup.exists():  # two saves in the same second must not overwrite the first backup
            backup, n = folder / f"{stamp}_{n}{path.suffix}", n + 1
        backup.write_text(old, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    return backup


def versions(name: str) -> list[Path]:
    folder = VERSIONS_DIR / name
    return sorted(folder.glob("*"), reverse=True) if folder.exists() else []


def output_folders() -> list[Path]:
    """One folder per render, newest first (output/<application>/<timestamp>/)."""
    if not config.OUTPUT_DIR.exists():
        return []
    found = [p for app in config.OUTPUT_DIR.iterdir() if app.is_dir() for p in app.iterdir() if p.is_dir()]
    return sorted(found, key=lambda p: p.name, reverse=True)


def history() -> list[dict]:
    """Every rendered CV and cover letter, newest first, with the text that went into them."""
    from .render import HISTORY_FILE
    out = []
    for folder in output_folders():
        record = folder / HISTORY_FILE
        if not record.exists():
            continue
        try:
            meta = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta["folder"] = folder
        meta["paths"] = {k: folder / name for k, name in (meta.get("files") or {}).items() if (folder / name).exists()}
        out.append(meta)
    return sorted(out, key=lambda m: m.get("rendered_at", ""), reverse=True)


def _update_record(folder: Path, change) -> dict:
    from .render import HISTORY_FILE
    path = folder / HISTORY_FILE
    meta = json.loads(path.read_text(encoding="utf-8"))
    change(meta)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def add_flag(folder: Path, doc: str, where: str, note: str) -> dict:
    """Mark a problem you found in a rendered document (doc: "cv" or "cl"). Kept with that render in the history."""
    flag = {"doc": doc, "where": where, "note": note, "at": datetime.now().isoformat(timespec="seconds"), "resolved": False}
    return _update_record(folder, lambda m: m.setdefault("flags", []).append(flag))


def resolve_flag(folder: Path, index: int) -> dict:
    return _update_record(folder, lambda m: m["flags"][index].update(resolved=True))


def add_claim_guard(text: str) -> list[str]:
    """Append a claim guard to guardrails.yaml, keeping comments and a version of the old file. Returns problems."""
    current = read("guardrails.yaml")
    lines = current.splitlines()
    entry = f"  - {json.dumps(' '.join(text.split()), ensure_ascii=False)}"
    try:
        i = next(n for n, l in enumerate(lines) if l.startswith("claim_guards:"))
    except StopIteration:
        lines += ["", "claim_guards:", entry]
    else:
        end = i + 1
        while end < len(lines) and (lines[end].startswith(("  -", "    ")) or (not lines[end].strip() and end + 1 < len(lines)
                                                                            and lines[end + 1].startswith("  -"))):
            end += 1
        lines.insert(end, entry)
    new = "\n".join(lines) + "\n"
    problems = validate("guardrails.yaml", new)
    if not problems:
        save("guardrails.yaml", new)
    return problems
