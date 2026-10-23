"""Which models cost money, and how much. OpenRouter's public model list has live prices (no key needed)."""
from __future__ import annotations

import json
import time
import urllib.request

from . import config

CACHE = config.DATA_DIR / ".openrouter_models.json"
PROVIDERS = ("anthropic/", "openai/", "google/", "meta-llama/", "mistralai/", "deepseek/", "x-ai/", "qwen/")
# Claude Code aliases: run on your Claude subscription, never billed per token.
CLAUDE = {"haiku": "Claude Haiku (fast)", "sonnet": "Claude Sonnet (balanced)", "opus": "Claude Opus (strongest)",
          "fable": "Claude Fable"}
# Anthropic API ids, billed to your own key: name, $ per 1M input tokens, $ per 1M output tokens.
ANTHROPIC = {"claude-haiku-5-5": ("Claude Haiku 5.5 (fast)", 0.10, 0.50),
             "claude-sonnet-5-5": ("Claude Sonnet 5.5 (balanced)", 2.00, 10.00),
             "claude-opus-5-5": ("Claude Opus 5.5 (strongest)", 4.00, 20.00)}


def _cached() -> dict[str, dict]:
    """The last saved price list; {} if there is none or it's unreadable (a half-written file must not break the app)."""
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def openrouter_models() -> dict[str, dict]:
    """id -> {name, input, output} with prices in $ per 1M tokens. Cached for a day; {} when offline."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < 86400:
        return _cached()
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=8) as r:
            data = json.load(r)["data"]
    except Exception:
        return _cached()  # offline: an old price list beats none
    out = {}
    for m in data:
        if m["id"].endswith(":batch") or not (m["id"].startswith(PROVIDERS) or m["id"].endswith(":free")):
            continue
        try:
            price = {k: float(m["pricing"][k]) * 1e6 for k in ("prompt", "completion")}
        except (KeyError, TypeError, ValueError):
            continue
        out[m["id"]] = {"name": m.get("name", m["id"]), "input": price["prompt"], "output": price["completion"]}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(out))
    return out


def is_openrouter(model: str) -> bool:
    return "/" in (model or "")


def is_anthropic(model: str) -> bool:
    """An Anthropic API id (claude-sonnet-5-5…), as opposed to a Claude Code alias (sonnet…)."""
    return (model or "").startswith("claude-")


def anthropic_cost(model: str, inp: int, out: int, cache_write: int, cache_read: int) -> float | None:
    """$ for one call. Cache writes cost 1.25x input, cache reads 0.1x. None for a model without a known price."""
    if model not in ANTHROPIC:
        return None
    _, i, o = ANTHROPIC[model]
    return (inp * i + cache_write * i * 1.25 + cache_read * i * 0.1 + out * o) / 1e6


def is_paid(model: str) -> bool:
    """True if a call to this model costs money on top of what you already pay."""
    if is_openrouter(model):
        return not model.endswith(":free")
    if is_anthropic(model):
        return True
    return config.claude_auth().get("authMethod", "claude.ai") != "claude.ai"  # Claude Code on an API key bills per token


def label(model: str) -> str:
    if is_anthropic(model):
        name, i, o = ANTHROPIC.get(model, (model, None, None))
        return f"{name} · your Anthropic key" + (f", ${i:.2f} in / ${o:.2f} out per 1M tokens" if i is not None else "")
    if not is_openrouter(model):
        plan = config.claude_auth().get("subscriptionType") or "Claude"
        how = f"included in your {plan.title()} plan" if not is_paid(model) else "billed to your Anthropic API key"
        return f"{CLAUDE.get(model, model)} · {how}"
    m = openrouter_models().get(model)
    if model.endswith(":free"):
        return f"{m['name'] if m else model} · free on OpenRouter"
    if not m:
        return f"{model} · OpenRouter, price unknown"
    return f"{m['name']} · OpenRouter ${m['input']:.2f} in / ${m['output']:.2f} out per 1M tokens"


def options(allow_paid: bool, have_key: bool) -> list[str]:
    """What the model pickers offer: free first. Claude Code only once you've picked it on the Connect AI page."""
    opts = list(CLAUDE) if config.backend() == "claude" and config.claude_bin() else []
    if allow_paid and config.anthropic_key():
        opts += list(ANTHROPIC)
    if have_key:
        models = openrouter_models()
        opts += sorted(m for m in models if m.endswith(":free"))
        if allow_paid:
            opts += sorted((m for m in models if not m.endswith(":free")), key=lambda m: (not m.startswith("anthropic/"), m))
    return opts
