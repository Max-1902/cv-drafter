"""Headless smoke test of every dashboard page (no API calls)."""
import pytest
from streamlit.testing.v1 import AppTest

from cvdrafter import config, memory
from cvdrafter.generate import post_process
from cvdrafter.profile import load_profile
from tests.test_offline import LIMITS, faithful_raw

PAGES = ["ui/chat.py", "ui/home.py", "ui/new_app.py", "ui/pipeline.py", "ui/log_page.py", "ui/insights_page.py", "ui/files_page.py", "ui/history_page.py", "ui/connect_page.py"]


@pytest.fixture(autouse=True)
def seeded_db(sample_data):
    memory.init_db()
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    analysis = {
        "role_type": "trading/quant/fintech", "seniority": "intern", "summary": "Quant dev intern.",
        "keywords": [{"term": "Python", "type": "hard", "in_profile": True},
                     {"term": "C++", "type": "hard", "in_profile": False}],
        "requirements": [{"text": "Python", "importance": "must", "status": "covered", "fact_ids": ["acme.1"], "comment": ""},
                         {"text": "C++", "importance": "must", "status": "missing", "fact_ids": [], "comment": ""}],
        "eligibility": {"work_authorization": "Must be authorized to work in the US", "excludes_visa_holders": False},
    }
    app_id = memory.create_application("Acme Capital", "Quant Developer Intern", "en", "We want Python and C++.", "", analysis)
    edited = {**draft, "cover_letter": [{"text": "Changed paragraph.", "sources": ["acme.1"]}]}
    # A hostile posting: the app must show this as text, never as HTML or an image.
    analysis["keywords"].append({"term": "![x](https://evil.example/?d=1)", "type": "hard", "in_profile": False})
    memory.update(app_id, generated=draft, final=edited, status="interview")
    return app_id


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(str(config.ROOT / page), default_timeout=30).run()
    assert not at.exception, at.exception


def test_editor_loads_saved_application(seeded_db):
    at = AppTest.from_file(str(config.ROOT / "ui/new_app.py"), default_timeout=30)
    at.session_state["open_app_id"] = seeded_db
    at.run()
    assert not at.exception, at.exception
    assert any("Acme Capital" in s.value for s in at.subheader)
    assert len(at.text_area) > 5  # bullet + cover-letter editors


def test_fact_bank_save_without_edits_keeps_every_fact(tmp_path, monkeypatch):
    import shutil

    from cvdrafter import files
    from cvdrafter.profile import index_facts
    copy = tmp_path / "profile.yaml"
    shutil.copy(config.PROFILE_PATH, copy)
    monkeypatch.setattr(config, "PROFILE_PATH", copy)
    monkeypatch.setitem(files.EDITABLE, "profile.yaml", (copy, *files.EDITABLE["profile.yaml"][1:]))
    before = load_profile()
    at = AppTest.from_file(str(config.ROOT / "ui/files_page.py"), default_timeout=30).run()
    next(b for b in at.button if b.label == "Save fact bank").click().run()
    assert not at.exception, at.exception
    after = load_profile()
    assert {k: v["text"] for k, v in index_facts(after).items()} == {k: v["text"] for k, v in index_facts(before).items()}
    assert [e["dates"] for e in after["experience"]] == [e["dates"] for e in before["experience"]]


def test_history_lists_every_render_and_shows_changes():
    from cvdrafter import render
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1)
    draft["cover_letter"][0]["text"] = "A rewritten first paragraph."
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1)
    at = AppTest.from_file(str(config.ROOT / "ui/history_page.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    rendered = [e for e in at.expander if "Word files" not in e.label and not e.label.startswith("Timeline")]
    assert len(rendered) == 4  # two CVs, two cover letters
    for t in at.toggle:
        t.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert any("rewritten" in m.value for m in at.markdown)


def test_history_keeps_every_generated_draft_with_its_time(seeded_db):
    first = memory.get(seeded_db)["generated"]
    memory.update(seeded_db, generated={**first, "cover_letter": [{"text": "A second draft.", "sources": ["acme.1"]}]})
    kept = memory.drafts(seeded_db)
    assert len(kept) == 2 and kept[0]["draft"]["cover_letter"][0]["text"] == "A second draft." and kept[1]["draft"] == first
    assert [e["kind"] for e in memory.events(seeded_db)].count("generated") == 2
    at = AppTest.from_file(str(config.ROOT / "ui/history_page.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander if "Word files" in e.label]
    assert len(labels) == 2 and all(l[:4].isdigit() and "No Word files yet" in l for l in labels)  # starts with date + time
    next(b for b in at.button if b.label.startswith("Create Word files")).click().run()
    assert not at.exception, at.exception
    override = [b for b in at.button if b.label.startswith("I checked them")]  # the sample draft may not pass every check
    if override:
        override[0].click().run()
    assert memory.get(seeded_db)["files"] and "rendered" in [e["kind"] for e in memory.events(seeded_db)]
    assert any("Word files created" in e.label for e in at.expander)


def test_render_is_blocked_until_errors_are_fixed_or_overridden(seeded_db):
    at = AppTest.from_file(str(config.ROOT / "ui/pipeline.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.run()
    memory.update(seeded_db, final={**memory.get(seeded_db)["final"],
                                    "cover_letter": [{"text": "I am passionate.", "sources": ["acme.1"]}]}, files=None)
    at.run()
    docs = next(b for b in at.button if b.label == "Create Word files (.docx)")
    docs.click().run()
    assert not at.exception, at.exception
    assert not memory.get(seeded_db)["files"] and any("Not rendered" in e.value for e in at.error)
    next(b for b in at.button if b.label.startswith("I checked them")).click().run()
    assert not at.exception, at.exception
    from cvdrafter import files
    rendered = files.history()[0]["evaluation"]
    assert memory.get(seeded_db)["files"] and rendered["overridden"] and rendered["status"] == "errors"


def test_history_shows_status_and_flags():
    from cvdrafter import files, render
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1,
                            evaluation={"status": "errors", "overridden": True, "errors": 1, "warnings": 0, "ai_checked": False,
                                        "checker": "", "checked_at": "2026-10-22T20:00:00",
                                        "issues": [{"level": "error", "location": "cover_letter/1", "message": "Banned phrase"}]})
    files.add_flag(files.history()[0]["folder"], "cv", "Acme Capital", "Wrong title")
    at = AppTest.from_file(str(config.ROOT / "ui/history_page.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander]
    assert any("Rendered despite errors" in l and "1 flag" in l for l in labels)  # the CV entry
    assert any("Rendered despite errors" in l and "flag" not in l for l in labels)  # the letter: the flag was on the CV
    assert at.metric[2].value == "1"


def test_history_lets_you_rewrite_or_edit_a_blocked_letter(seeded_db, monkeypatch):
    from cvdrafter import generate
    app = memory.get(seeded_db)
    memory.update(seeded_db, final={**app["final"], "cover_letter": [{"text": "I am passionate.", "sources": ["acme.1"]}]})
    asked = []

    def fake_rewrite(ctx, draft, writer, checker, problems, instruction="", progress=None):
        asked.append((problems, instruction))
        return {"draft": {**draft, "cover_letter": [{"text": "A plain new letter.", "sources": ["acme.1"]}]}, "issues": []}
    monkeypatch.setattr(generate, "rewrite_letter", fake_rewrite)
    at = AppTest.from_file(str(config.ROOT / "ui/history_page.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.run()
    next(b for b in at.button if b.label.startswith("Create Word files")).click().run()
    assert not at.exception, at.exception
    assert any("Not rendered" in e.value for e in at.error) and not memory.get(seeded_db)["files"]
    rewrite = next(b for b in at.button if b.label.startswith("Rewrite to fix"))
    next(t for t in at.text_area if t.label == "Instructions (optional)").input("shorter")
    rewrite.click().run()
    assert not at.exception, at.exception
    assert asked and asked[0][1] == "shorter" and all(i["location"].startswith("cover_letter") for i in asked[0][0])
    app = memory.get(seeded_db)
    assert app["final"] is None and app["generated"]["cover_letter"][0]["text"] == "A plain new letter."
    assert len(memory.drafts(seeded_db)) == 2  # the old draft is kept

    paragraph = next(t for t in at.text_area if t.value == "A plain new letter.")
    paragraph.input("My own wording.")
    at.run()
    next(b for b in at.button if b.label == "Save my edits").click().run()
    assert not at.exception, at.exception
    assert memory.get(seeded_db)["final"]["cover_letter"][0]["text"] == "My own wording."
    assert any(b.label == "Create Word files (with your edits)" for b in at.button)
