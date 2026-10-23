"""Paths, settings and layout limits."""
from __future__ import annotations

import functools
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)  # a fresh install has no data folder yet
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"

PROFILE_PATH = DATA_DIR / "profile.yaml"
SETTINGS_PATH = DATA_DIR / "settings.json"  # model choices and the paid-models switch, kept between sessions
LEARNED_FACTS_PATH = DATA_DIR / "learned_facts.yaml"
STYLE_RULES_PATH = DATA_DIR / "style_rules.md"
GUARDRAILS_PATH = DATA_DIR / "guardrails.yaml"
INTERPRETATIONS_PATH = DATA_DIR / "interpretations.yaml"  # facts you confirmed show a trait, for the fit verdict
DEFAULT_CV_PATH = DATA_DIR / "default_cv.yaml"  # reused when a posting doesn't need a tailored CV
VOICE_SAMPLE_PATH = DATA_DIR / "voice_sample.md"
SKILL_MAP_PATH = DATA_DIR / "skill_map.json"  # posting keyword -> canonical skill, for Insights
DB_PATH = Path(os.getenv("CVD_DB") or DATA_DIR / "applications.db")

SLOTS_PATH = TEMPLATES_DIR / "slots.yaml"
CV_TEMPLATE = TEMPLATES_DIR / "cv_template.docx"
CL_TEMPLATE = TEMPLATES_DIR / "cl_template.docx"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_FIX_ROUNDS = int(os.getenv("MAX_FIX_ROUNDS", "2"))

# One model per job. Sonnet is the standard: it writes and fact-checks (the app is tuned for it).
#   writer  - analysis and the CV/letter: quality matters most
#   checker - fact-checks the writer against your facts
#   fast    - turns short chat messages into actions: a small model is plenty
MODELS = {
    "anthropic": {"writer": "claude-sonnet-5-5", "checker": "claude-sonnet-5-5", "fast": "claude-haiku-5-5"},
    "claude": {"writer": "sonnet", "checker": "sonnet", "fast": "haiku"},
    # The same models as the Anthropic API, so OpenRouter gives the same quality.
    "openrouter": {"writer": "anthropic/claude-sonnet-5.5", "checker": "anthropic/claude-sonnet-5.5",
                   "fast": "anthropic/claude-haiku-5.5"},
}


def api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def save_env(updates: dict[str, str]) -> None:
    """Write keys into .env (only you can read it) and apply them right away. An empty value removes the key."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, done = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            done.add(key)
            if updates[key]:
                out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if v and k not in done]
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)
    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


@functools.lru_cache(maxsize=1)  # it globs the editor folders, and every model label asks for it
def claude_bin() -> str:
    """The Claude Code CLI: CLAUDE_BIN, then PATH, then the copy bundled with the VS Code / Cursor extension."""
    if os.getenv("CLAUDE_BIN"):
        return os.getenv("CLAUDE_BIN")
    # An app opened from Finder has a bare PATH, so look where the installers put it too.
    for found in (shutil.which("claude"), Path.home() / ".local/bin/claude", Path.home() / ".claude/local/claude",
                  "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if found and Path(found).exists():
            return str(found)
    bundled = []
    for editor in (".vscode", ".cursor", ".windsurf"):
        bundled += glob.glob(str(Path.home() / editor / "extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    version = lambda p: [int(x) for x in re.findall(r"\d+", p.split("claude-code-")[1].split("-darwin")[0])]  # noqa: E731
    return max(bundled, key=version) if bundled else ""


BACKENDS = ("anthropic", "openrouter", "claude")


def backend() -> str:
    """LLM_BACKEND=anthropic|openrouter|claude. Default: whichever key you added, Anthropic first. Claude Code is never
    picked on its own: choose it on the Connect AI page if you already use it. "" means nothing is connected yet."""
    choice = os.getenv("LLM_BACKEND", "auto").strip().lower()
    if choice in BACKENDS:
        return choice
    if anthropic_key():
        return "anthropic"
    return "openrouter" if api_key() else ""


@functools.lru_cache(maxsize=1)
def claude_auth() -> dict:
    """How Claude Code is signed in: authMethod 'claude.ai' means your subscription (not billed per call)."""
    if not claude_bin():
        return {}
    try:
        out = subprocess.run([claude_bin(), "auth", "status"], capture_output=True, text=True, timeout=20)
        return json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    # A key you added bills per call, so paid models start on there; with Claude Code they start off.
    return {"allow_paid": bool(data.get("allow_paid", backend() in ("anthropic", "openrouter"))), "models": dict(data.get("models") or {}),
            # Off: a pasted posting is only logged and analysed. A full write + fact-check costs ~25-50k tokens.
            "auto_write": bool(data.get("auto_write", False))}


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def model_for(role: str) -> str:
    """The model you picked for this job in the app, else the default."""
    return load_settings()["models"].get(role) or default_model(role)


def default_model(role: str) -> str:
    """WRITER_MODEL etc. in .env are OpenRouter ids; ANTHROPIC_WRITER_MODEL etc. are Anthropic API ids
    (claude-sonnet-5-5…); CLAUDE_WRITER_MODEL etc. are Claude Code aliases (sonnet, opus…)."""
    b = backend() or "anthropic"
    env = role.upper() + "_MODEL"
    if b != "openrouter":
        env = ("CLAUDE_" if b == "claude" else "ANTHROPIC_") + env
    return os.getenv(env) or MODELS[b][role]


def voice_sample() -> str:
    """A letter or text you wrote yourself, without the <!-- --> instructions. Empty if you haven't added one."""
    if not VOICE_SAMPLE_PATH.exists():
        return ""
    return re.sub(r"<!--.*?-->", "", VOICE_SAMPLE_PATH.read_text(encoding="utf-8"), flags=re.S).strip()


def load_slots() -> dict:
    with open(SLOTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def labels() -> dict:
    return load_slots()["labels"]
