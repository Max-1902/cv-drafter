"""Bring-your-own-key: backend choice, saving keys, and the Anthropic API path (no real requests)."""
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from cvdrafter import config, llm, pricing

real_anthropic = llm._anthropic  # taken before conftest stubs it out


def test_backend_follows_your_key_and_never_picks_claude_code_on_its_own(monkeypatch):
    monkeypatch.setattr(config, "claude_bin", lambda: "/usr/local/bin/claude")
    assert config.backend() == ""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    assert config.backend() == "openrouter"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert config.backend() == "anthropic" and config.default_model("writer") == "claude-sonnet-5-5"
    monkeypatch.setenv("LLM_BACKEND", "claude")
    assert config.backend() == "claude" and config.default_model("checker") == "sonnet"


def test_save_env_keeps_other_lines_and_is_private():
    config.ENV_PATH.write_text("# note\nMAX_FIX_ROUNDS=2\nANTHROPIC_API_KEY=old\n")
    config.save_env({"ANTHROPIC_API_KEY": "new", "LLM_BACKEND": "anthropic"})
    assert config.ENV_PATH.read_text() == "# note\nMAX_FIX_ROUNDS=2\nANTHROPIC_API_KEY=new\nLLM_BACKEND=anthropic\n"
    assert config.ENV_PATH.stat().st_mode & 0o077 == 0
    assert config.anthropic_key() == "new" and config.backend() == "anthropic"
    config.save_env({"ANTHROPIC_API_KEY": ""})
    assert "ANTHROPIC_API_KEY" not in config.ENV_PATH.read_text() and not config.anthropic_key()


def test_a_key_turns_paid_models_on_by_default(monkeypatch):
    assert not config.load_settings()["allow_paid"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert config.load_settings()["allow_paid"]
    assert pricing.is_paid("claude-opus-5-5") and "claude-sonnet-5-5" in pricing.options(True, False)
    assert "sonnet" not in pricing.options(True, False)  # Claude Code aliases only once you pick Claude Code


def test_anthropic_call_caches_the_fact_bank_and_records_cost(monkeypatch):
    sent = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                    cache_read_input_tokens=4000)
            return SimpleNamespace(usage=usage, stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text='{"ok": true}')])

    class Client:
        def __init__(self, **kw):
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: sent.update(k) or Stream()))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(llm.anthropic, "Anthropic", Client)
    monkeypatch.setattr(llm.memory, "log_call", lambda row: None)
    assert real_anthropic("FACTS", "posting", "claude-sonnet-5-5", "low", 32000, "t") == '{"ok": true}'
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["output_config"] == {"effort": "low"} and sent["fallbacks"] == "default"
    assert "temperature" not in sent
    row = llm.USAGE[-1]
    assert row["via"] == "Anthropic API" and row["billed"] == pytest.approx((1000 * 2 + 4000 * 0.2 + 500 * 10) / 1e6)


def test_app_opens_on_connect_ai_until_something_is_connected():
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("Sign in with your Claude subscription" in h.value for h in at.subheader)
