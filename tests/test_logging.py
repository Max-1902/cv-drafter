"""Company detection and application logging (no API calls)."""
from datetime import date

import pytest

from cvdrafter import company, config, memory


@pytest.mark.parametrize("url, expected", [
    ("https://boards.greenhouse.io/janestreet/jobs/123", "Janestreet"),
    ("https://job-boards.greenhouse.io/examplecorp/jobs/5", "Examplecorp"),
    ("https://jobs.lever.co/palantir/abc-def", "Palantir"),
    ("https://jobs.ashbyhq.com/openai/123", "Openai"),
    ("https://citadel.wd5.myworkdayjobs.com/en-US/External/job/x", "Citadel"),
    ("https://careers.twosigma.com/careers/JobDetail/1", "Twosigma"),
    ("https://www.drw.com/work-at-drw/listings/x", "Drw"),
    ("https://www.linkedin.com/jobs/view/123", ""),
    ("", ""),
])
def test_company_from_url(url, expected):
    assert company.from_url(url) == expected


def test_company_from_text_prefers_eeo_and_about():
    jd = ("About Optiver:\nWe are a market maker. Our partners include Nasdaq.\n"
          "Optiver is an equal opportunity employer.")
    assert company.from_text(jd)[0] == "Optiver"


def test_resolve_rejects_hallucinated_or_ats_names():
    jd = "Join Acme Robotics as an intern. Acme Robotics is an equal opportunity employer."
    url = "https://boards.greenhouse.io/acmerobotics/jobs/1"
    assert company.resolve("Greenhouse", "", jd, url)["company"] == "Acme Robotics"
    r = company.resolve("Globex", "", jd, url)  # not in the posting: code overrides the AI
    assert r["company"] == "Acme Robotics" and r["confidence"] == "high"
    assert company.resolve("Acme Robotics Inc.", "", jd, url)["confidence"] == "high"
    assert company.resolve("anything", "Typed Co", jd, url)["company"] == "Typed Co"


def test_resolve_unknown_is_low_confidence():
    assert company.resolve("", "", "We build software. Apply now.", "")["confidence"] == "low"


def test_mark_applied_logs_dates_and_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    memory.init_db()
    analysis = {"job": {"location": "Chicago, IL", "salary": "$60/hr"}, "company_check": {"confidence": "high"}}
    app_id = memory.create_application("Optiver", "SWE Intern", "en", "jd", "", analysis, url="https://x.com")
    memory.mark_applied(app_id, date(2026, 10, 1))
    app = memory.get(app_id)
    assert (app["status"], app["sent_at"], app["follow_up_at"]) == ("sent", "2026-10-01", "2026-10-08")
    assert (app["location"], app["salary"], app["company_confidence"], app["url"]) == ("Chicago, IL", "$60/hr", "high", "https://x.com")
    assert [e["kind"] for e in memory.events(app_id)] == ["created", "status"]
    memory.update(app_id, status="interview")
    assert memory.events(app_id)[-1]["detail"] == "sent → interview"
    memory.delete(app_id)
    assert memory.events(app_id) == []


def test_same_posting_pasted_again_is_found_before_any_ai_call():
    memory.init_db()
    jd = " ".join(f"Requirement {i}: build low-latency trading system part {i} in C++ and Python." for i in range(60))
    app_id = memory.create_application("CTC", "SWE Intern", "en", jd, "", {}, url="https://ctc.com/jobs/1")
    assert memory.find_duplicate("Applied? Here it is again https://x.com/job\n" + jd.upper())["id"] == app_id
    assert memory.find_duplicate("short note", "https://ctc.com/jobs/1/")["id"] == app_id
    assert memory.find_duplicate("Quant Researcher. Design statistical models for options pricing in R and kdb. " * 3) is None


def test_deleting_the_newest_application_leaves_no_gap_in_numbering():
    memory.init_db()
    first = memory.create_application("A", "r", "en", "jd a", "", {})
    memory.delete(memory.create_application("B", "r", "en", "jd b", "", {}))
    assert memory.create_application("C", "r", "en", "jd c", "", {}) == first + 1
