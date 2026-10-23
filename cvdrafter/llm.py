"""LLM calls that always return parsed JSON.

Three backends:
- "anthropic": the Anthropic API with your own key (ANTHROPIC_API_KEY in .env).
- "openrouter": the OpenRouter API with your own key (OPENROUTER_API_KEY in .env).
- "claude": the Claude Code CLI on this Mac, for people who already use it. It runs on whatever account you signed
  Claude Code in with, e.g. your Claude Pro plan.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time

import anthropic
import openai
from openai import OpenAI

from . import config, memory, pricing


class LLMError(RuntimeError):
    pass


class TruncatedError(LLMError):
    """The reply ran into the output limit. Asking again with the same limit would only cut it off again."""


# Every call this process made: shown in the sidebar so you can see where tokens go.
USAGE: list[dict] = []


def _record(task: str, model: str, inp: int, out: int, cached: int, seconds: float, cost: float | None = None) -> None:
    """billed: what you actually pay for this call. api_value: what it would cost at API prices (for comparison)."""
    paid = pricing.is_paid(model)
    via = ("OpenRouter" if pricing.is_openrouter(model) else "Anthropic API" if pricing.is_anthropic(model)
           else "Claude Code")
    row = {"task": task, "model": model, "via": via + ("" if paid else " (free)"), "input": inp, "cached": cached, "output": out, "seconds": round(seconds, 1),
           "billed": (cost or 0.0) if paid else 0.0, "api_value": cost}
    USAGE.append(row)
    try:
        memory.log_call(row)
    except Exception:  # a full disk or locked database must never break a draft
        pass


def _client() -> OpenAI:
    if not config.api_key():
        raise LLMError("OPENROUTER_API_KEY is missing. Add it to .env, or use the Claude Code backend (no key needed).")
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.api_key(),
        default_headers={"X-Title": "CV-CL Drafter"},
        timeout=180,
        max_retries=2,
    )


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start : end + 1]) if start != -1 and end > start else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        raise LLMError(f"Model did not return a JSON object:\n{text[:500]}")
    return data


LIMIT_RE = re.compile(r"usage limit|limit reached|rate limit|resets? (at|in)|out of (extra )?usage", re.I)


def _claude_once(system: str, user: str, model: str, effort: str | None) -> dict:
    binary = config.claude_bin()
    if not binary:
        raise LLMError("Claude isn't set up on this Mac yet. Open the Connect AI page and click Install Claude.")
    cmd = [binary, "-p", "--output-format", "json", "--model", model, "--system-prompt", system,
           # A plain completion: no tools, no MCP servers, no project settings, nothing saved to your session history.
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
    if effort:
        cmd += ["--effort", effort]
    # CLAUDECODE: allowed even when the app runs inside Claude Code. ANTHROPIC_API_KEY: without it Claude Code uses
    # your claude.ai login, so calls count against your subscription instead of being billed per token.
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "ANTHROPIC_API_KEY")}
    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=600, env=env,
                              cwd=tempfile.gettempdir())  # outside the project, so no CLAUDE.md is loaded
    except subprocess.TimeoutExpired as e:
        raise LLMError("Claude took longer than 10 minutes. Try again in a moment.") from e
    except OSError as e:
        raise LLMError(f"Couldn't start Claude: {e}. Open the Connect AI page and click Install Claude.") from e
    out, text = {}, proc.stdout.strip()
    for candidate in (text, text.splitlines()[-1] if text else ""):  # the reply, maybe after a warning line
        try:
            out = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    out = out if isinstance(out, dict) else {}
    if not out:
        msg = (proc.stderr or proc.stdout).strip()[:500]
        if "login" in msg.lower() or "auth" in msg.lower():
            raise LLMError("Claude isn't signed in. Open the Connect AI page and click Sign in with Claude.")
        if LIMIT_RE.search(msg):
            raise LLMError(f"You've reached your Claude plan's usage limit for now. {msg}")
        raise LLMError(f"Claude failed: {msg or 'no reply'}")
    return out


def _claude(system: str, user: str, model: str, effort: str | None, task: str) -> str:
    start = time.time()
    out = _claude_once(system, user, model, effort)
    # Not every plan includes every model (e.g. Opus): fall back to Sonnet instead of failing the draft.
    if out.get("is_error") and out.get("api_error_status") in (403, 404) and model != "sonnet":
        model, task = "sonnet", f"{task} (fell back to sonnet)"
        out = _claude_once(system, user, model, effort)
    if out.get("is_error"):
        msg = str(out.get("result"))[:500]
        if out.get("api_error_status") in (401, 403) or "/login" in msg or "authenticat" in msg.lower():
            raise LLMError("Claude isn't signed in anymore. Open the Connect AI page and click Sign in with Claude.")
        if out.get("api_error_status") == 429 or LIMIT_RE.search(msg):
            raise LLMError(f"You've reached your Claude plan's usage limit for now. {msg} Your work is saved; "
                           "try again once it resets.")
        raise LLMError(f"Claude error: {msg}")
    u = out.get("usage") or {}
    _record(task, model, u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0), u.get("output_tokens", 0),
            u.get("cache_read_input_tokens", 0), time.time() - start, out.get("total_cost_usd"))
    return out.get("result") or ""


# Models that support server-side fallback: if a safety filter wrongly declines a request, another model takes over
# inside the same call instead of failing the draft.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")


def _anthropic(system: str, user: str, model: str, effort: str | None, max_tokens: int, task: str) -> str:
    if not config.anthropic_key():
        raise LLMError("No Anthropic API key yet. Add yours on the Connect AI page.")
    client = anthropic.Anthropic(api_key=config.anthropic_key(), max_retries=2)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        # The system prompt carries the fact bank, which is the same on every call: cache it.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    if effort:
        kwargs["output_config"] = {"effort": effort}
    if model in FALLBACK_MODELS:
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    start = time.time()
    try:
        with client.beta.messages.stream(**kwargs) as stream:  # streamed: long replies would hit the HTTP timeout
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic didn't accept your API key. Check it on the Connect AI page.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Anthropic's rate limit was hit. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic request failed ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Couldn't reach the Anthropic API. Check your internet connection.") from e
    u = msg.usage
    write, read = u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0
    _record(task, model, u.input_tokens + write, u.output_tokens, read, time.time() - start,
            pricing.anthropic_cost(model, u.input_tokens, u.output_tokens, write, read))
    if msg.stop_reason == "refusal":
        raise LLMError(f"{model} declined this request.")
    if msg.stop_reason == "max_tokens":
        raise TruncatedError(f"{model} hit the {max_tokens}-token output limit, so its reply was cut off.")
    return "".join(b.text for b in msg.content if b.type == "text")


def _openrouter(system: str, user: str, model: str, temperature: float, max_tokens: int, task: str) -> str:
    client = _client()
    # The system prompt carries the fact bank, which is the same on every call: mark it cacheable (Anthropic models).
    sys_msg = ({"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
               if model.startswith("anthropic/") else {"role": "system", "content": system})
    kwargs = dict(
        model=model,
        messages=[sys_msg, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        # Only route to providers that don't store or train on prompts (your fact bank is in every prompt).
        extra_body={"provider": {"data_collection": "deny"}, "usage": {"include": True}},
    )
    start = time.time()
    try:
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except openai.BadRequestError:
            # Not every OpenRouter provider supports response_format; the prompt asks for JSON anyway.
            resp = client.chat.completions.create(**kwargs)
    except openai.APIError as e:
        hint = (" This model has no provider that guarantees not to store your data; pick another model."
                if "data policy" in str(e).lower() else "")
        raise LLMError(f"OpenRouter request failed: {e}{hint}") from e
    if not resp.choices or not resp.choices[0].message.content:
        raise LLMError("Empty response from model.")
    u = resp.usage
    if u:
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        _record(task, model, u.prompt_tokens - cached, u.completion_tokens, cached, time.time() - start, getattr(u, "cost", None))
    if resp.choices[0].finish_reason == "length":
        raise TruncatedError(f"{model} hit the {max_tokens}-token output limit, so its reply was cut off.")
    return resp.choices[0].message.content


def chat_json(system: str, user: str, model: str, temperature: float = 0.2, max_tokens: int = 32000,
              effort: str | None = None, task: str = "") -> dict:
    """model: an Anthropic id (claude-sonnet-5-5…) goes to the Anthropic API with your key; an OpenRouter id
    (vendor/model) goes to OpenRouter; a Claude Code alias (haiku, sonnet, opus…) runs through your Claude Code sign-in.
    effort (Anthropic and Claude Code): low for quick lookups; None keeps the model's default reasoning."""
    if pricing.is_paid(model) and not config.load_settings()["allow_paid"]:
        raise LLMError(f"{model} costs money and paid models are switched off. Pick a free model, or turn on "
                       "'Paid models' at the bottom left.")
    def call(prompt: str, label: str) -> str:
        if pricing.is_openrouter(model):
            return _openrouter(system, prompt, model, temperature, max_tokens, label)
        if pricing.is_anthropic(model):
            return _anthropic(system, prompt, model, effort, max_tokens, label)
        return _claude(system, prompt, model, effort, label)

    try:
        return parse_json(call(user, task))
    except TruncatedError:
        raise
    except LLMError:
        # Models occasionally break the JSON (e.g. a note to themselves inside a string). Ask once more from scratch
        # rather than repairing the broken reply, so nothing half-written slips into the draft.
        retry = user + ("\n\nYour previous reply was not valid JSON. Reply with only the JSON object: no notes, "
                        "comments or text outside the strings' intended content.")
        return parse_json(call(retry, f"{task} (retry: bad JSON)"))


def usage_summary() -> dict:
    """This session's tokens, plus what you've actually been billed (today, this month, ever)."""
    return {"calls": len(USAGE), "input": sum(u["input"] for u in USAGE), "output": sum(u["output"] for u in USAGE),
            "cached": sum(u["cached"] for u in USAGE), "billed": sum(u["billed"] for u in USAGE),
            "api_value": sum(u["api_value"] or 0 for u in USAGE if not u["billed"]), **memory.spend()}
