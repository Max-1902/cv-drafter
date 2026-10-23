"""Profile chat: AI changes are applied in code, junk is skipped, and every change can be undone."""
import shutil

import pytest
import yaml

from cvdrafter import config, files, importer, profile_chat
from cvdrafter.profile import index_facts, load_profile


@pytest.fixture
def own_profile(tmp_path, monkeypatch):
    copy = tmp_path / "profile.yaml"
    shutil.copy(config.PROFILE_PATH, copy)
    monkeypatch.setattr(config, "PROFILE_PATH", copy)
    monkeypatch.setitem(files.EDITABLE, "profile.yaml", (copy, *files.EDITABLE["profile.yaml"][1:]))
    return copy


def raw():
    return yaml.safe_load(config.PROFILE_PATH.read_text())


def test_changes_land_where_they_belong():
    new, done = profile_chat.apply_changes(raw(), [
        {"op": "add_fact", "entry_id": "acme", "text": "Mentored two new interns", "skills": ["Mentoring"]},
        {"op": "add_entry", "section": "experience", "fields": {"company": "Cafe Luna", "title": "Barista",
                                                                 "dates": "2022"}, "facts": ["Trained 3 new staff"]},
        {"op": "add_item", "list": "languages", "text": "Spanish (B2)"},
        {"op": "add_skills", "category": "Tools", "items": ["Excel", "Excel"]},
        {"op": "set_personal", "field": "linkedin", "value": "linkedin.com/in/janedoe"},
    ])
    assert len(done) == 5
    from cvdrafter.profile import profile_from_raw
    prof = profile_from_raw(new)
    texts = {f["text"].split(" (skills:")[0] for f in index_facts(prof).values()}
    assert "Mentored two new interns" in texts and "Trained 3 new staff" in texts
    assert "Spanish (B2)" in prof["languages"] and prof["skills"]["Tools"].count("Excel") == 1
    assert prof["personal"]["linkedin"] == "linkedin.com/in/janedoe"


def test_junk_and_duplicates_are_skipped():
    before = raw()
    first = before["experience"][0]["facts"][0]["text"]
    new, done = profile_chat.apply_changes(before, [
        "not a dict", {"op": "explode"}, {"op": "add_entry", "section": "experience", "fields": {}},
        {"op": "add_fact", "entry_id": "acme", "text": first},  # already there
        {"op": "set_personal", "field": "email", "value": "evil@example.com"},  # contact details are never touched
        {"op": "remove_fact", "fact_id": "does.not.exist"},
    ])
    assert done == [] and new == before


def test_unknown_entry_becomes_a_general_fact_instead_of_being_lost():
    new, done = profile_chat.apply_changes(raw(), [{"op": "add_fact", "entry_id": "made_up", "text": "Ran a marathon"}])
    assert done and "Ran a marathon" in [f if isinstance(f, str) else f["text"] for f in new["general_facts"]]


def test_removing_a_fact_keeps_the_other_ids():
    from cvdrafter.profile import profile_from_raw
    before = {f["id"]: f["text"] for f in index_facts(profile_from_raw(raw())).values()}
    victim = next(i for i in before if i.startswith("acme."))
    new, done = profile_chat.apply_changes(raw(), [{"op": "remove_fact", "fact_id": victim}])
    after = {f["id"]: f["text"] for f in index_facts(profile_from_raw(new)).values()}
    assert done and victim not in after
    assert all(after[i] == t for i, t in before.items() if i != victim)


def test_chat_turn_saves_and_undo_restores(own_profile, monkeypatch):
    sent = {}

    def fake(system, user, model, **kw):
        sent["user"] = user
        return {"reply": "Added it!", "changes": [{"op": "add_general_fact", "text": "Volunteer at the food bank"}]}
    monkeypatch.setattr(profile_chat, "chat_json", fake)
    before = own_profile.read_text()
    out = profile_chat.handle("I volunteer at the food bank, call me on +1 555 010 0199", [], [], "sonnet")
    assert out["done"] and "Volunteer at the food bank" in own_profile.read_text()
    assert "555 010 0199" not in sent["user"]  # contact details never leave the Mac
    profile_chat.undo(out["undo"])
    assert own_profile.read_text() == before


def test_first_cv_builds_the_fact_bank(tmp_path, monkeypatch):
    path = tmp_path / "new" / "profile.yaml"
    path.parent.mkdir()
    monkeypatch.setattr(config, "PROFILE_PATH", path)
    monkeypatch.setitem(files.EDITABLE, "profile.yaml", (path, *files.EDITABLE["profile.yaml"][1:]))
    monkeypatch.setattr(importer, "chat_json", lambda *a, **k: {
        "personal": {"name": "Sam Lee"}, "experience": [{"company": "Shop", "title": "Clerk", "facts": ["Sold things"]}],
        "skills": {"Tools": ["Excel"]}})
    out = profile_chat.handle("", [("Sam_Lee_CV.pdf", "Sam Lee, Clerk at Shop. Sold things. Excel.")], [], "sonnet")
    assert path.exists() and load_profile(path)["personal"]["name"] == "Sam Lee"
    assert "Sam\\_Lee\\_CV.pdf" in out["reply"] or "Sam_Lee_CV.pdf" in out["reply"]
    profile_chat.undo(out["undo"])
    assert not path.exists()


def test_unreadable_files_get_a_plain_message():
    with pytest.raises(profile_chat.ChatError, match="can't read"):
        profile_chat.read_file("photo.heic", b"x")
    with pytest.raises(profile_chat.ChatError, match="couldn't open"):
        profile_chat.read_file("broken.pdf", b"not a pdf")
    with pytest.raises(profile_chat.ChatError, match="no readable text"):
        profile_chat.read_file("empty.txt", b"   ")


def test_chats_survive_closing_and_reopening_the_app():
    from ui.common import load_chat, save_chat
    turns = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Added.", "done": ["x"], "undo": "old"}]
    save_chat("profile", turns)
    back = load_chat("profile", [])
    assert [t["content"] for t in back] == ["hi", "Added."] and "undo" not in back[1]  # undo is session-only
    (config.DATA_DIR / "chats" / "apply.json").write_text("{broken")
    assert load_chat("apply", [{"role": "assistant", "content": "welcome"}])[0]["content"] == "welcome"
