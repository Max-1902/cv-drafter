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


def test_job_links_match_without_tracking_and_across_boards():
    k = memory.url_key
    assert k("https://harborcapital.com/careers/job/1234567/?gh_jid=1234567&gh_src=x") == k("boards.greenhouse.io/harbor/jobs/1234567")
    assert k("https://www.linkedin.com/jobs/view/4417664178/?trk=a") == k("https://www.linkedin.com/jobs/search/?currentJobId=4417664178")
    assert k("https://jobs.lever.co/optiver/123?utm_source=li") == k("jobs.lever.co/optiver/123/")
    assert k("https://x.com/careers?jobId=55&ref=mail") == "x.com/careers?jobid=55"
    assert k("https://jobs.lever.co/optiver/123") != k("https://jobs.lever.co/optiver/124")


def test_duplicates_are_found_by_containment_and_by_name_for_postings_logged_without_text():
    memory.init_db()
    jd = " ".join(f"Requirement {i}: build low-latency trading system part {i} in C++ and Python." for i in range(60))
    with_text = memory.create_application("CTC", "SWE Intern", "en", jd, "", {})
    boilerplate = "About the company. Show more. 1,234 followers. Apply now. Easy Apply. " * 5
    assert memory.find_duplicate(boilerplate + jd + boilerplate)["id"] == with_text  # LinkedIn wrapping
    assert memory.find_duplicate(jd[: len(jd) // 2])["match"] == "text"  # only part of it
    imported = memory.create_application("Summit Trading", "Software Developer Intern", "en", "", "", {})
    found = memory.find_duplicate("Summit Trading is hiring a Software Developer Intern for Summer 2027 in Chicago.")
    assert (found["id"], found["match"]) == (imported, "name")
    autumn = memory.create_application("Autumn (working-capital fintech)", "Founding Engineer", "en", "", "", {})
    assert memory.find_duplicate("Autumn 2027 data analyst internship at a bank.") is None
    assert memory.find_duplicate("Autumn is hiring a Founding Engineer for AI agents.")["id"] == autumn
    assert memory.find_duplicate("Summit Trading is hiring a Quant Researcher.") is None  # same firm, other role


def test_same_job_and_attaching_a_posting_keeps_what_you_entered():
    memory.init_db()
    app_id = memory.create_application("Northwind Trading (Northwind Group)", "Student Developer Intern, equities desk", "en", "", "", {})
    memory.update(app_id, status="sent", sent_at="2026-10-20", location="Chicago", user_notes="C++ track")
    assert memory.find_same_job("Northwind Group", "Student Developer Intern")["id"] == app_id
    assert memory.find_same_job("Northwind Group", "Quantitative Trader") is None
    assert memory.find_same_job("Harbor Capital", "Student Developer Intern") is None
    analysis = {"company": "Northwind Group", "job": {"location": "New York", "salary": "$60/hr"}}
    app = memory.attach_posting(app_id, "The posting text.", analysis, "https://dv.com/jobs/9")
    assert (app["jd"], app["status"], app["sent_at"], app["user_notes"]) == ("The posting text.", "sent", "2026-10-20", "C++ track")
    assert (app["location"], app["salary"], app["url"]) == ("Chicago", "$60/hr", "https://dv.com/jobs/9")  # only empty ones filled
