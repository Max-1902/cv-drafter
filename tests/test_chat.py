"""The Apply chat end to end, with the AI calls replaced by fakes."""
import pytest
from streamlit.testing.v1 import AppTest

from cvdrafter import analyze, assistant, config, generate, memory
from cvdrafter.generate import post_process
from cvdrafter.profile import load_profile
from tests.test_offline import LIMITS, faithful_raw

JD = ("Software Engineer Intern\nAbout Optiver:\nWe are a market maker.\nResponsibilities\n- Build trading systems\n"
      "Requirements\n- Python\nLocation: Chicago, IL\nOptiver is an equal opportunity employer.\n" + "Details. " * 60
      + "\nhttps://jobs.lever.co/optiver/123")


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    memory.init_db()

    def fake_analyze(jd, company, role, profile, model, url=""):
        from cvdrafter import company as co
        return {"role": "Software Engineer Intern", "company": co.resolve("Optiver", company, jd, url)["company"],
                "company_check": co.resolve("Optiver", company, jd, url), "keywords": [], "eligibility": {},
                "requirements": [{"text": "Python", "importance": "must", "status": "covered", "fact_ids": ["acme.1"], "comment": ""}],
                "job": {"location": "Chicago, IL", "work_mode": "onsite", "employment_type": "internship", "salary": "",
                        "deadline": "", "job_ref": ""}}

    def fake_draft(ctx, writer, checker, progress=lambda m: None, tailor_cv=None):
        p = load_profile()
        return {"draft": post_process(faithful_raw(p), p, LIMITS)[0], "issues": [], "fix_rounds": 0, "examples_used": 0,
                "tailored_cv": True, "cv_reason": "no default CV set up"}

    monkeypatch.setattr(analyze, "analyze_job", fake_analyze)
    monkeypatch.setattr(generate, "draft_application", fake_draft)
    monkeypatch.setattr(assistant, "chat_json", lambda *a, **k: {"action": "update", "fields": {"salary": "$65/hr"},
                                                                 "reply": "Pay set to $65/hr."})
    return AppTest.from_file(str(config.ROOT / "ui/chat.py"), default_timeout=30).run()


def test_paste_logs_drafts_and_takes_commands(chat):
    at = chat
    assert not at.exception, at.exception
    at.chat_input[0].set_value(JD).run()
    assert not at.exception, at.exception
    app = memory.list_all()[0]
    assert (app["company"], app["role"], app["location"], app["url"]) == ("Optiver", "Software Engineer Intern", "Chicago, IL",
                                                                          "https://jobs.lever.co/optiver/123")
    assert not app["generated"]  # writing costs ~25-50k tokens, so it waits until asked
    at.chat_input[0].set_value("write it").run()
    assert not at.exception, at.exception
    app = memory.get(app["id"])
    assert app["generated"] and not at.exception
    doc_box = next(t for t in at.text_area if (t.key or "").startswith("doc::"))  # the whole CV as one text, on the left
    assert "# EXPERIENCE" in doc_box.value and "Built a Python news classifier" in doc_box.value
    doc_box.set_value(doc_box.value.replace("Built a Python news classifier", "Built my own classifier")).run()
    assert not at.exception, at.exception
    assert "Built my own classifier" in str(memory.get(app["id"])["final"])
    bad = next(t for t in at.text_area if (t.key or "").startswith("doc::"))
    bad.set_value(bad.value.replace("## Acme Bank | Software Engineering Intern", "## Acme Bank | Chief Executive")).run()
    assert any("isn't a job" in e.value for e in at.error)  # explained, and nothing was saved from it
    assert "Chief Executive" not in str(memory.get(app["id"])["final"])

    next(g for g in at.segmented_control if g.key == "edit_layout").set_value("Per bullet").run()
    assert len(at.text_area) > 5
    next(t for t in at.text_area if "::experience/" in (t.key or "")).set_value("My own bullet").run()
    assert "My own bullet" in str(memory.get(app["id"])["final"])

    at.chat_input[0].set_value("applied").run()
    assert memory.get(app["id"])["status"] == "sent" and memory.get(app["id"])["sent_at"]
    at.chat_input[0].set_value("status interview").run()
    assert memory.get(app["id"])["status"] == "interview"
    at.chat_input[0].set_value("the pay is 65 an hour").run()
    assert memory.get(app["id"])["salary"] == "$65/hr"
    assert not at.exception, at.exception
    page = AppTest.from_file(str(config.ROOT / "ui/applications.py"), default_timeout=30)  # details live on the Applications page
    page.query_params["app"] = str(app["id"])
    page.run()
    assert not page.exception and any(t.value == "$65/hr" for t in page.text_input)


def test_message_kinds():
    assert assistant.is_posting(JD) and not assistant.is_posting("make the letter shorter")
    assert assistant.quick_action("Applied today!")["action"] == "applied"
    assert assistant.quick_action("status: rejected")["fields"] == {"status": "rejected"}
    assert assistant.quick_action("should I apply?") is None
    assert assistant.says_applied("Applied to this yesterday:\n" + JD)
    assert not assistant.says_applied(JD)
    assert assistant.find_url(JD) == "https://jobs.lever.co/optiver/123"


def test_paid_models_blocked_when_off(tmp_path, monkeypatch):
    from cvdrafter import llm, pricing
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "claude_auth", lambda: {"authMethod": "claude.ai", "subscriptionType": "pro"})
    assert not pricing.is_paid("sonnet") and pricing.is_paid("anthropic/claude-sonnet-4.5")
    assert not pricing.is_paid("meta-llama/llama-3.3-70b-instruct:free")
    with pytest.raises(llm.LLMError, match="switched off"):
        llm.chat_json("s", "u", "anthropic/claude-sonnet-4.5")
    config.save_settings({"allow_paid": True, "models": {}})
    monkeypatch.setattr(llm, "_openrouter", lambda *a, **k: '{"ok": true}')
    assert llm.chat_json("s", "u", "anthropic/claude-sonnet-4.5") == {"ok": True}


def test_pasting_the_posting_of_an_imported_application_fills_it_in(chat, monkeypatch):
    at = chat
    imported = memory.create_application("Optiver", "Software Engineer Intern", "en", "", "", {})
    memory.update(imported, status="sent", sent_at="2026-10-01")
    at.chat_input[0].set_value(JD).run()
    assert not at.exception, at.exception
    assert len(memory.list_all()) == 1
    app = memory.get(imported)
    assert app["jd"] == JD and app["status"] == "sent" and app["analysis"]["requirements"]
    assert any("attached the posting" in m.value for m in at.markdown)


def test_same_job_with_other_text_waits_for_log_it_anyway(chat, monkeypatch):
    at = chat
    calls = []
    real = analyze.analyze_job
    monkeypatch.setattr(analyze, "analyze_job", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = memory.create_application("Optiver", "Software Engineer Intern", "en", "An older Optiver posting. " * 30, "", {})
    at.chat_input[0].set_value(JD).run()
    assert not at.exception, at.exception
    assert len(memory.list_all()) == 1 and any("log it anyway" in m.value for m in at.markdown)
    at.chat_input[0].set_value("log it anyway").run()
    assert not at.exception, at.exception
    assert len(memory.list_all()) == 2 and memory.list_all()[0]["id"] != first
    assert len(calls) == 1  # the analysis was reused, not paid for twice


def test_application_questions_are_answered_and_saved(chat, monkeypatch):
    from cvdrafter import answers
    at = chat
    at.chat_input[0].set_value(JD).run()
    app_id = memory.list_all()[0]["id"]
    seen = []

    def fake_answers(ctx, questions, writer, checker=None, progress=None):
        seen.append(questions)
        return [{"question": q, "answer": f"Answer to {q}", "sources": ["acme.1"], "issues": []} for q in questions]
    monkeypatch.setattr(answers, "answer_questions", fake_answers)
    box = next(t for t in at.text_area if t.label == "The form's questions, one per line")
    box.input("Why are you interested in this position?\nWhat AI-specific technologies are you comfortable with?")
    next(b for b in at.button if b.label == "Answer").click().run()
    assert not at.exception, at.exception
    assert seen == [["Why are you interested in this position?", "What AI-specific technologies are you comfortable with?"]]
    saved = memory.get(app_id)["answers"]
    assert [a["question"] for a in saved] == seen[0]
    assert any("Answer to Why are you interested" in c.value for c in at.code)


def test_workspace_shows_editor_and_preview_together_and_folds_the_chat(chat):
    at = chat
    at.chat_input[0].set_value(JD).run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][:2] == ["Match details", "Fit"]  # details sit in secondary tabs, not stacked panels
    at.chat_input[0].set_value("write it").run()
    assert not at.exception, at.exception
    doc = at.get("segmented_control")[0]
    assert doc.proto.options[0].content == "CV" and doc.proto.options[1].content == "Cover letter"
    assert any((t.key or "").startswith("doc::") for t in at.text_area)  # the CV editor is showing, as one text
    next(g for g in at.segmented_control if g.key == "edit_layout").set_value("Per bullet").run()
    assert any(k for k in (t.key for t in at.text_area) if "::experience/" in (k or ""))
    assert any("zoom:.66" in h.proto.body for h in at.get("html"))  # with its live preview beside it
    assert any(e.label.startswith("Chat") and not e.proto.expanded for e in at.expander)  # the chat folds away once there is a draft
    next(g for g in at.segmented_control if g.key == "doc_view").set_value("Cover letter").run()
    assert not at.exception, at.exception
    assert any("::cover_letter/" in (t.key or "") for t in at.text_area)
