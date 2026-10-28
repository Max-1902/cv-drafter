"""Headless smoke test of every dashboard page (no API calls)."""
import pytest
from streamlit.testing.v1 import AppTest

from cvdrafter import config, memory
from cvdrafter.generate import post_process
from cvdrafter.profile import load_profile
from tests.test_offline import LIMITS, faithful_raw

PAGES = ["ui/chat.py", "ui/home.py", "ui/new_app.py", "ui/applications.py", "ui/insights_page.py", "ui/files_page.py", "ui/connect_page.py"]


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


def test_versions_list_every_render_and_show_changes():
    from cvdrafter import render
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1)
    draft["cover_letter"][0]["text"] = "A rewritten first paragraph."
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1)
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.query_params["app"] = "1"
    at.run()
    assert not at.exception, at.exception
    assert any(e.label == "Earlier renders (1)" for e in at.expander)  # the older render sits underneath the newest
    for t in at.toggle:
        t.set_value(True)
    at.run()
    assert not at.exception, at.exception
    assert any("rewritten" in m.value for m in at.markdown)


def test_versions_keep_every_generated_draft_with_its_time(seeded_db):
    first = memory.get(seeded_db)["generated"]
    memory.update(seeded_db, generated={**first, "cover_letter": [{"text": "A second draft.", "sources": ["acme.1"]}]})
    kept = memory.drafts(seeded_db)
    assert len(kept) == 2 and kept[0]["draft"]["cover_letter"][0]["text"] == "A second draft." and kept[1]["draft"] == first
    assert [e["kind"] for e in memory.events(seeded_db)].count("generated") == 2
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.query_params["app"] = str(seeded_db)
    at.run()
    assert not at.exception, at.exception
    assert any(e.label == "Earlier drafts (1)" for e in at.expander)
    assert any(m.value.startswith("**Draft written 20") and "No Word files yet" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "Create Word files (with your edits)").click().run()
    assert not at.exception, at.exception
    override = [b for b in at.button if b.label.startswith("I checked them")]  # the sample draft may not pass every check
    if override:
        override[0].click().run()
    assert memory.get(seeded_db)["files"] and "rendered" in [e["kind"] for e in memory.events(seeded_db)]
    assert any("Word files created" in m.value for m in at.markdown)


def test_application_shows_the_posting_and_logs_a_late_application(seeded_db):
    from datetime import date, timedelta
    memory.update(seeded_db, status="ready", sent_at=None, follow_up_at=None)
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.query_params["app"] = str(seeded_db)
    at.run()
    assert not at.exception, at.exception
    assert any("We want Python and C++." in t.value for t in at.text)
    sent_on = date.today() - timedelta(days=3)
    at.date_input(key=f"applied_on_{seeded_db}").set_value(sent_on).run()
    at.button(key=f"applied_{seeded_db}").click().run()
    assert not at.exception, at.exception
    app = memory.get(seeded_db)
    assert app["status"] == "sent" and app["sent_at"] == sent_on.isoformat()
    assert app["follow_up_at"] == (sent_on + timedelta(days=memory.FOLLOW_UP_DAYS)).isoformat()
    assert any(f"Applied on {sent_on.isoformat()}" in m.value for m in at.success)


def test_render_is_blocked_until_errors_are_fixed_or_overridden(seeded_db):
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.query_params["app"] = str(seeded_db)
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


def test_versions_show_status_and_flags():
    from cvdrafter import files, render
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=1,
                            evaluation={"status": "errors", "overridden": True, "errors": 1, "warnings": 0, "ai_checked": False,
                                        "checker": "", "checked_at": "2026-10-22T20:00:00",
                                        "issues": [{"level": "error", "location": "cover_letter/1", "message": "Banned phrase"}]})
    files.add_flag(files.history()[0]["folder"], "cv", "Acme Capital", "Wrong title")
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.query_params["app"] = "1"
    at.run()
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    assert any(l.startswith("CV ·") and "AI check didn't run" in l and "1 flag" in l for l in labels)  # the flag is on the CV
    assert any(l.startswith("Cover letter ·") and "Rendered despite errors" in l and "flag" not in l for l in labels)  # the error is in the letter
    assert any("Letter: ⛔ Rendered despite errors" in m.value for m in at.markdown)  # also on the documents card


def test_versions_let_you_rewrite_or_edit_a_blocked_letter(seeded_db, monkeypatch):
    from cvdrafter import generate
    app = memory.get(seeded_db)
    memory.update(seeded_db, final={**app["final"], "cover_letter": [{"text": "I am passionate.", "sources": ["acme.1"]}]})
    asked = []

    def fake_rewrite(ctx, draft, writer, checker, problems, instruction="", progress=None):
        asked.append((problems, instruction))
        return {"draft": {**draft, "cover_letter": [{"text": "A plain new letter.", "sources": ["acme.1"]}]}, "issues": []}
    monkeypatch.setattr(generate, "rewrite_letter", fake_rewrite)
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.query_params["app"] = str(seeded_db)
    at.run()
    next(b for b in at.button if b.label == "Create Word files (with your edits)").click().run()
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


def test_editor_chat_edits_the_boxes_and_can_undo(seeded_db, monkeypatch):
    from cvdrafter import generate
    asked = []

    def fake_revise(ctx, draft, request, history, model):
        asked.append(request)
        return {"edits": {"experience/acme/1": "A shorter classifier bullet"}, "reply": "Shortened it."}
    monkeypatch.setattr(generate, "revise_texts", fake_revise)
    at = AppTest.from_file(str(config.ROOT / "ui/new_app.py"), default_timeout=30)
    at.session_state["open_app_id"] = seeded_db
    at.session_state["llm_check"] = False
    at.run()
    box = f"ed::{seeded_db}::experience/acme/1"
    before = at.session_state[box]
    at.chat_input[0].set_value("make the first bullet shorter").run()
    assert not at.exception, at.exception
    assert asked == ["make the first bullet shorter"]
    assert at.session_state[box] == "A shorter classifier bullet"
    assert any("A shorter classifier bullet" == t.value for t in at.text_area)
    assert any("Shortened it." in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "Undo last AI change").click().run()
    assert not at.exception, at.exception
    assert at.session_state[box] == before


def test_fit_badge_and_confirming_an_interpretation(seeded_db):
    from cvdrafter.profile import load_interpretations
    at = AppTest.from_file(str(config.ROOT / "ui/new_app.py"), default_timeout=30)
    at.session_state["open_app_id"] = seeded_db
    at.run()
    assert not at.exception, at.exception
    assert any('class="fit ' in m.value for m in at.markdown)
    shows = next(t for t in at.text_input if t.label == "shows (comma-separated)")
    shows.input("leadership")
    next(b for b in at.button if b.label == "Confirm").click().run()
    assert not at.exception, at.exception
    assert load_interpretations()[0]["shows"] == ["leadership"]


def test_editor_attaches_a_posting_to_an_application_logged_without_one(monkeypatch):
    from cvdrafter import analyze
    imported = memory.create_application("Globex", "Data Science Intern", "en", "", "", {})
    memory.update(imported, status="sent", sent_at="2026-10-01")
    monkeypatch.setattr(analyze, "analyze_job", lambda jd, company, role, *a, **k: {
        "company": "Globex", "role": "Data Science Intern", "keywords": [], "eligibility": {}, "job": {},
        "requirements": [{"text": "Python", "importance": "must", "status": "covered", "fact_ids": ["acme.1"], "comment": ""}]})
    at = AppTest.from_file(str(config.ROOT / "ui/new_app.py"), default_timeout=30).run()
    n = len(memory.list_all())
    next(t for t in at.text_area if t.label == "Job posting").input("Globex wants a data science intern. " * 20)
    next(b for b in at.button if b.label == "Analyse posting").click().run()
    assert not at.exception, at.exception
    assert len(memory.list_all()) == n and memory.get(imported)["jd"].startswith("Globex wants")
    assert any("attached to it" in s.value for s in at.success)


def test_applications_list_opens_an_application_with_its_documents_first(seeded_db):
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.session_state["llm_check"] = False
    at.run()
    assert not at.exception, at.exception
    assert any("Acme Capital" in m.value for m in at.markdown)
    at.button(key=f"open_{seeded_db}").click().run()
    assert not at.exception, at.exception
    assert at.query_params["app"] == [str(seeded_db)] or at.query_params["app"] == str(seeded_db)
    assert any("Acme Capital" in s.value for s in at.subheader)
    labels = [t.label for t in at.tabs]  # the Versions tab holds tabs of its own
    assert [l for l in labels if l in ("Tracking", "Versions", "Fit", "Interview prep", "Learn from edits", "Posting")] == [
        "Tracking", "Versions", "Fit", "Interview prep", "Learn from edits", "Posting"]
    assert any(b.label == "Create Word files (.docx)" for b in at.button)
    next(b for b in at.button if b.label == "← All applications").click().run()
    assert not at.exception, at.exception
    assert not any(s.value.startswith("Acme") for s in at.subheader)


def test_applications_list_shows_document_state_and_filters_by_it(seeded_db):
    from cvdrafter import render
    p = load_profile()
    draft, _ = post_process(faithful_raw(p), p, LIMITS)
    other = memory.create_application("Globex", "Data Science Intern", "en", "", "", {})  # nothing generated for this one
    at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    row = lambda: [m.value for m in at.markdown]  # noqa: E731
    assert "📝 Draft" in row() and "–" in row()  # Acme has text only, Globex has nothing
    render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=seeded_db,
                            evaluation={"status": "verified", "overridden": False, "errors": 0, "warnings": 0, "ai_checked": True,
                                        "checker": "x", "checked_at": "2026-10-22T20:00:00", "issues": []})
    at.run()
    assert row().count("✅ Verified") == 2  # CV and letter
    docs = next(m for m in at.multiselect if m.label == "Documents")
    docs.set_value(["Nothing yet"]).run()
    assert any("Globex" in m for m in row()) and not any("Acme Capital" in m for m in row())
    docs.set_value(["Verified"]).run()
    assert any("Acme Capital" in m for m in row()) and not any("Globex" in m for m in row())
    assert other


def test_document_state_is_judged_per_document():
    from cvdrafter import docstate
    ev = {"status": "errors", "overridden": True, "errors": 1, "warnings": 1, "ai_checked": True,
          "issues": [{"level": "error", "location": "cover_letter/1", "message": "x"},
                     {"level": "warning", "location": "experience/acme/1", "message": "y"}]}
    render = {"evaluation": ev, "flags": [{"doc": "cv", "resolved": False}, {"doc": "cv", "resolved": True}]}
    assert docstate.check(render, "cl") == "overridden" and docstate.check(render, "cv") == "warnings"
    assert docstate.check({**render, "evaluation": {**ev, "overridden": False}}, "cl") == "errors"
    company = {"evaluation": {**ev, "issues": [{"level": "error", "location": "company", "message": "guess"}]}}
    assert docstate.check(company, "cv") == docstate.check(company, "cl") == "overridden"  # concerns both documents
    assert docstate.check({"evaluation": {**ev, "issues": [], "overridden": False}}, "cv") == "verified"
    assert docstate.check({"evaluation": {**ev, "issues": [], "ai_checked": False}}, "cv") == "unverified"
    assert docstate.check({}, "cv") == "unchecked"
    assert docstate.label(render, "cv").endswith("🚩 1 flag(s)")
    app = {"final": None, "generated": {"x": 1}}
    assert docstate.summary(app, []) == "Draft only" and docstate.summary({"final": None, "generated": None}, []) == "Nothing yet"
    good = {"evaluation": {**ev, "issues": [], "overridden": False}, "flags": []}
    assert docstate.summary(app, [good]) == "Verified"
    assert docstate.summary(app, [{**good, "flags": [{"doc": "cl", "resolved": False}]}]) == "Needs a look"
    assert docstate.summary(app, [render]) == "Needs a look"
    from datetime import date
    assert docstate.when("2026-10-26T20:51:00", date(2026, 11, 1)) == "Oct 26"
    assert docstate.when("2025-10-26", date(2026, 11, 1)) == "Oct 26 2025" and docstate.when("") == "–"


def test_applications_board_and_table_views_render(seeded_db):
    for view in ("Board", "Table"):
        at = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)
        at.session_state["apps_view"] = view
        at.run()
        assert not at.exception, at.exception


def test_word_files_can_be_opened_in_word_and_the_pdf_made_again(seeded_db, tmp_path, monkeypatch):
    import os
    import subprocess

    from cvdrafter import pdf
    folder = tmp_path / "render"
    folder.mkdir()
    files = {}
    for key in ("cv", "cl"):
        (folder / f"{key}.docx").write_bytes(b"word")
        (folder / f"{key}.pdf").write_bytes(b"pdf")
        files |= {f"{key}_docx": str(folder / f"{key}.docx"), f"{key}_pdf": str(folder / f"{key}.pdf")}
    files["cv_pages"] = 2
    memory.update(seeded_db, files=files)
    opened, made = [], []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: opened.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(pdf, "to_pdf", lambda docx: made.append(docx) or docx.with_suffix(".pdf"))
    monkeypatch.setattr(pdf, "page_count", lambda path: 1)

    def page():
        import streamlit as st

        from cvdrafter import memory
        from ui.common import download_buttons
        app = memory.get(1)
        download_buttons(app["files"], "t", app["id"])
        st.caption("done")

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception, at.exception
    assert [b.label for b in at.button] == ["Edit in Word", "Edit in Word"]  # a Word button for the CV and one for the letter
    assert not at.caption or all("edited after" not in c.value for c in at.caption)  # nothing edited yet
    at.button[0].click().run()
    assert opened and opened[0][-1].endswith("cv.docx")

    os.utime(folder / "cv.docx", (os.stat(folder / "cv.pdf").st_mtime + 60,) * 2)  # saved in Word since the PDF was made
    at = AppTest.from_function(page, default_timeout=30).run()
    assert [b.label for b in at.button] == ["Edit in Word", "Edit in Word", "Update PDF from Word edits"]
    assert any("Word file was edited after" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "Update PDF from Word edits").click().run()
    assert not at.exception, at.exception
    assert [str(m) for m in made] == [str(folder / "cv.docx")]
    assert memory.get(seeded_db)["files"]["cv_pages"] == 1 and "edited" in [e["kind"] for e in memory.events(seeded_db)]


def test_edited_since_pdf_and_open_in_word(tmp_path, monkeypatch):
    import os
    import subprocess

    from cvdrafter import pdf
    d, p = tmp_path / "a.docx", tmp_path / "a.pdf"
    assert not pdf.edited_since_pdf(d, p)  # nothing there
    d.write_bytes(b"x"), p.write_bytes(b"x")
    os.utime(d, (1000, 1000)), os.utime(p, (2000, 2000))
    assert not pdf.edited_since_pdf(d, p)
    os.utime(d, (3000, 3000))
    assert pdf.edited_since_pdf(d, p)
    monkeypatch.setattr(pdf, "WORD_APP", tmp_path / "NoWord.app")
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert pdf.open_in_word(d) and seen == [["open", str(d)]]  # without Word installed, the system's own app for .docx
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1))
    assert not pdf.open_in_word(d)


def test_word_edits_are_pulled_into_the_app_once(seeded_db, tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from cvdrafter import render
    from docx import Document
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    p = load_profile()
    draft = memory.get(seeded_db)["final"]
    files = render.render_documents(draft, p, "Acme Capital", "Quant Developer Intern", app_id=seeded_db)
    memory.update(seeded_db, files=files)
    doc = Document(files["cl_docx"])
    para = next(x for x in doc.paragraphs if x.text.strip() == "Changed paragraph.")
    para.runs[0].text = "Typed in Word."
    doc.save(files["cl_docx"])
    stamp = os.stat(Path(files["cl_docx"]).parent / "render.json").st_mtime + 60
    os.utime(files["cl_docx"], (stamp, stamp))  # saved in Word after the app made it

    def page():
        from cvdrafter import memory
        from ui.common import download_buttons
        app = memory.get(1)
        download_buttons(app["files"], "t", app["id"], can_pull=True)

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("Word file was edited after" in c.value for c in at.caption)
    assert [b.label for b in at.button].count("Use my Word edits in the app") == 1  # only the letter was edited
    next(b for b in at.button if b.label == "Use my Word edits in the app").click().run()
    assert not at.exception, at.exception
    saved = memory.get(seeded_db)
    assert saved["final"]["cover_letter"][0]["text"] == "Typed in Word."
    assert saved["final"]["cover_letter"][0]["sources"] == ["acme.1"]
    assert "Use my Word edits in the app" not in [b.label for b in at.button]  # read once, not offered again
    assert not any("Word file was edited after" in c.value for c in at.caption)
