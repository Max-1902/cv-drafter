"""Point every test at the fictional profile in tests/fixtures, never at your real data/ files."""
from pathlib import Path

import pytest

from cvdrafter import config, files, llm

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def sample_data(tmp_path, monkeypatch):
    paths = {
        "PROFILE_PATH": FIXTURES / "profile.yaml",
        "GUARDRAILS_PATH": FIXTURES / "guardrails.yaml",
        "LEARNED_FACTS_PATH": tmp_path / "learned_facts.yaml",
        "STYLE_RULES_PATH": tmp_path / "style_rules.md",
        "OUTPUT_DIR": tmp_path / "output",
        "DB_PATH": tmp_path / "test.db",
        "SKILL_MAP_PATH": tmp_path / "skill_map.json",
        "DEFAULT_CV_PATH": tmp_path / "default_cv.yaml",  # none: tests tailor unless they write one
    }
    for name, path in paths.items():
        monkeypatch.setattr(config, name, path)

    def offline(*a, **k):  # no test may reach a real model; tests that need one stub it themselves
        raise llm.LLMError("offline in tests")
    monkeypatch.setattr(llm, "_claude", offline)
    monkeypatch.setattr(llm, "_openrouter", offline)
    monkeypatch.setattr(llm, "_anthropic", offline)
    monkeypatch.setattr(files, "VERSIONS_DIR", tmp_path / ".versions")
    # Tests never see your keys or backend choice, and never write your .env.
    for var in ("LLM_BACKEND", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")  # saved chats etc.
    editable = {"profile.yaml": "PROFILE_PATH", "guardrails.yaml": "GUARDRAILS_PATH",
                "learned_facts.yaml": "LEARNED_FACTS_PATH", "style_rules.md": "STYLE_RULES_PATH"}
    monkeypatch.setattr(files, "EDITABLE", {
        name: (paths[editable[name]], *rest) if name in editable else (path, *rest)
        for name, (path, *rest) in files.EDITABLE.items()
    })
