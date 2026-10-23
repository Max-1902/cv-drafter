"""Connect AI: sign in with your Claude plan (recommended), or bring your own OpenRouter or Anthropic key."""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import anthropic
import streamlit as st

from cvdrafter import config
from ui.common import ROLES, setup

INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
QUALITY_NOTE = ("**Quality note:** this app is built and tested for Claude Sonnet. Through a key it still uses Sonnet "
                "by default. If you switch to other models (GPT, Gemini, Llama, free models…), your CV and cover "
                "letter get noticeably worse and the fact-check can miss mistakes. Keep the recommended models.")

setup()
st.title("Connect AI")
st.caption("The app needs an AI to read job postings and write your documents. Everything you save stays on this Mac; "
           "your texts are only sent to the AI you connect here.")

NAMES = {"anthropic": "Anthropic API key", "openrouter": "OpenRouter key", "claude": "your Claude plan"}
current = config.backend()
if current:
    st.success(f"Connected: **{NAMES[current]}**. You're ready to go.")


def use(backend: str, env: dict[str, str] | None = None) -> None:
    """Switch backend: models go back to the recommended ones, and paid models are on for a key you pay for."""
    config.save_env({**(env or {}), "LLM_BACKEND": backend})
    config.save_settings({**config.load_settings(), "models": {}, "allow_paid": backend != "claude"})
    for role in ROLES:
        st.session_state.pop(f"{role}_model", None)
    st.session_state.pop("allow_paid", None)
    st.session_state.pop("waiting_for_login", None)
    st.rerun()


def refresh_claude() -> dict:
    config.claude_bin.cache_clear()
    config.claude_auth.cache_clear()
    return config.claude_auth()


def install_claude() -> str:
    """'' when Claude Code is installed. Uses Anthropic's official installer (no admin password needed)."""
    env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:/usr/bin:/bin:/usr/sbin:/sbin:{os.environ.get('PATH', '')}"}
    try:
        r = subprocess.run(["/bin/bash", "-c", INSTALL_CMD], capture_output=True, text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return "The download took too long. Check your internet connection and try again."
    refresh_claude()
    if config.claude_bin():
        return ""
    return (r.stderr or r.stdout or "unknown error").strip()[-400:]


def open_login() -> None:
    """Claude's own sign-in runs in a Terminal window; the browser opens claude.ai, where you pick your account."""
    script = Path(tempfile.gettempdir()) / "cv-drafter-claude-login.command"
    script.write_text(
        "#!/bin/bash\nclear\n"
        "echo 'Signing in to Claude for CV Drafter.'\n"
        "echo 'Your browser opens next: log in to claude.ai and click Authorize.'\necho\n"
        f"{shlex.quote(config.claude_bin())} auth login --claudeai\n"
        "echo\necho 'Done. You can close this window and go back to CV Drafter in your browser.'\n",
        encoding="utf-8")
    script.chmod(0o700)
    subprocess.run(["open", "-a", "Terminal", str(script)], check=False)
    st.session_state["waiting_for_login"] = True


def check_anthropic(key: str) -> str:
    """'' if the key works. Listing models is free, so checking costs nothing."""
    try:
        anthropic.Anthropic(api_key=key, max_retries=0, timeout=15).models.list(limit=1)
        return ""
    except anthropic.AuthenticationError:
        return "Anthropic didn't accept this key. Copy it again from console.anthropic.com."
    except anthropic.APIError as e:
        return f"Couldn't check the key: {e}"


def check_openrouter(key: str) -> str:
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return ""
    except urllib.error.HTTPError as e:
        return ("OpenRouter didn't accept this key. Copy it again from openrouter.ai/keys." if e.code in (401, 403)
                else f"Couldn't check the key: {e}")
    except OSError as e:
        return f"Couldn't reach OpenRouter: {e}"


def key_form(backend: str, env_key: str, saved: str, check, where: str) -> None:
    with st.form(f"form_{backend}"):
        key = st.text_input("API key", type="password", placeholder="•••• saved" if saved else "paste your key",
                            help=where)
        if st.form_submit_button("Check and use this key", type="primary"):
            key = key.strip() or saved
            if not key:
                st.error("Paste a key first.")
            else:
                with st.spinner("Checking the key…"):
                    err = check(key)
                if err:
                    st.error(err)
                else:
                    use(backend, {env_key: key})


# ---------------------------------------------------------------- 1. Claude plan (recommended)

with st.container(border=True):
    st.subheader("Sign in with your Claude subscription (recommended)")
    st.markdown("Uses your **Claude Pro or Max** plan. No API key, no extra bill: the app runs on your plan's usage. "
                "The app never sees your Claude password.")

    if not config.claude_bin():
        st.markdown("**Step 1 of 2:** install Claude on this Mac. It's free, takes about a minute, and needs no "
                    "admin password.")
        if st.button("Install Claude", type="primary"):
            with st.spinner("Installing Claude… (about a minute)"):
                err = install_claude()
            if err:
                st.error(f"The install didn't finish: {err}")
                st.caption(f"You can also install it yourself: open Terminal and paste `{INSTALL_CMD}`, then click "
                           "Check again.")
            else:
                st.rerun()
        if st.button("Check again", key="check_install"):
            refresh_claude()
            st.rerun()
    else:
        auth = config.claude_auth()
        plan = (auth.get("subscriptionType") or "").lower()
        on_plan = auth.get("loggedIn") and auth.get("authMethod") == "claude.ai"

        if on_plan and plan not in ("", "pro", "max", "team", "enterprise"):
            st.warning(f"You're signed in, but your account is on the **{plan.title()}** plan. The app needs Claude "
                       "**Pro or Max** (claude.ai → Settings → Billing), or use a key below.")
        elif on_plan and current == "claude":
            st.success(f"Signed in with your Claude {plan.title()} plan.")
        elif on_plan:
            st.info(f"Claude is signed in with your {plan.title() or 'Claude'} plan.")
            if st.button("Use my Claude plan", type="primary"):
                use("claude")
        else:
            if auth.get("loggedIn"):
                st.warning("Claude is signed in with an API key, not a subscription. Sign in again and pick your "
                           "Claude account.")
            st.markdown("**Step 2 of 2:** sign in. A small Terminal window opens and then your browser: log in to "
                        "claude.ai and click **Authorize**. This page notices by itself when you're done.")
            if st.button("Sign in with Claude", type="primary"):
                open_login()

            if st.session_state.get("waiting_for_login"):
                @st.fragment(run_every="3s")
                def wait_for_login():
                    a = refresh_claude()
                    if a.get("loggedIn") and a.get("authMethod") == "claude.ai":
                        use("claude")
                    st.caption("⏳ Waiting for you to finish signing in in the browser…")
                wait_for_login()

        with st.expander("Trouble signing in?"):
            st.markdown("- No browser opened? Look at the Terminal window: it shows a link to open by hand.\n"
                        "- It asks for a code? Copy the code from the browser into the Terminal window and press "
                        "Return.\n- Free Claude accounts can't be used; you need Pro or Max.")
            c1, c2 = st.columns(2)
            if c1.button("Check again"):
                refresh_claude()
                st.rerun()
            if c2.button("Sign in again"):
                open_login()


# ---------------------------------------------------------------- 2. keys

with st.expander("No Claude subscription? Use an API key instead", expanded=current in ("openrouter", "anthropic")):
    st.warning(QUALITY_NOTE)
    t_or, t_api = st.tabs(["OpenRouter key", "Anthropic API key"])
    with t_or:
        st.markdown("Pay per use (usually a few cents per application). Requests only go to providers that don't "
                    "store or train on your data.\n\n1. Create a key at [openrouter.ai/keys](https://openrouter.ai/keys) "
                    "and add a few dollars of credit.\n2. Paste it here.")
        key_form("openrouter", "OPENROUTER_API_KEY", config.api_key(), check_openrouter,
                 "Starts with sk-or-. Saved only on this Mac.")
    with t_api:
        st.markdown("Pay Anthropic per use (usually a few cents per application).\n\n"
                    "1. Create a key at [console.anthropic.com](https://console.anthropic.com/settings/keys) and add "
                    "a few dollars of credit under Billing.\n2. Paste it here.")
        key_form("anthropic", "ANTHROPIC_API_KEY", config.anthropic_key(), check_anthropic,
                 "Starts with sk-ant-. Saved only on this Mac.")

if current:
    st.divider()
    if not config.PROFILE_PATH.exists():
        st.page_link("ui/files_page.py", label="Next: add your CV (Your profile → drop in your CV)", icon="📄")
    else:
        st.page_link("ui/chat.py", label="Go to Apply", icon="➡️")
