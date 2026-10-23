"""Turn your manual edits into reusable style rules."""
from __future__ import annotations

from datetime import date

from . import config, prompts
from .llm import chat_json
from .verify import iter_items


def edit_pairs(generated: dict, final: dict) -> list[tuple[str, str, str]]:
    before = {loc: item["text"] for loc, _, item, _ in iter_items(generated)}
    after = {loc: item["text"] for loc, _, item, _ in iter_items(final)}
    return [(loc, before[loc], after[loc]) for loc in before if loc in after and before[loc].strip() != after[loc].strip()]


def read_rules() -> str:
    return config.STYLE_RULES_PATH.read_text(encoding="utf-8") if config.STYLE_RULES_PATH.exists() else ""


def propose_rules(generated: dict, final: dict, model: str) -> list[str]:
    pairs = edit_pairs(generated, final)
    if not pairs:
        return []
    text = "\n\n".join(f"[{loc}]\nBEFORE: {b}\nAFTER:  {a}" for loc, b, a in pairs)
    data = chat_json(prompts.LEARN_SYSTEM, prompts.LEARN_USER.format(existing=read_rules() or "(none)", pairs=text), model, temperature=0.2,
                     task="learn from edits")
    return [str(r).strip() for r in data.get("rules") or [] if str(r).strip()]


def append_rules(rules: list[str], source: str) -> None:
    if not rules:
        return
    block = f"\n\n<!-- learned {date.today().isoformat()} from {source} -->\n" + "\n".join(f"- {r}" for r in rules)
    config.STYLE_RULES_PATH.write_text(read_rules().rstrip() + block + "\n", encoding="utf-8")
