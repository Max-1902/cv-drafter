# Architecture

I built this app so I can paste a job posting and get a CV and cover letter tailored to it, written only from my
**fact bank** (`data/profile.yaml`). It checks every claim before anything gets rendered into my Word templates, and
it tracks every application in SQLite.

I designed everything around one rule: **nothing goes into a document unless it traces back to a fact in my
profile.**

## The full pipeline

```
 1. I paste a posting            ui/chat.py or ui/new_app.py
        │
        ▼
 2. Clean the posting            posting.py      strip job-board text and boilerplate
        │
        ▼
 3. Analyse                      analyze.py      keywords, requirements, job details
        │                        company.py      confirm the company name
        ▼
 4. Log the application          memory.py       saved in SQLite, status: draft
        │
        ▼
 5. Write CV + cover letter      generate.py     fact bank + style rules + my best past applications
        │
        ▼
 6. Enforce the template         generate.py     post_process(): no new jobs, skills or facts
        │
        ▼
 7. Check                        verify.py       rule checks, then AI fact-check
        │                                        errors? ──► back to step 5 (max MAX_FIX_ROUNDS)
        ▼
 8. I review and edit            ui/             live preview + diff against my facts
        │
        ▼
 9. Render gate                  verify.py       re-check the exact edited text; errors block the render
        │
        ▼
10. Render                       render.py       DOCX from my templates, PDF via Word (pdf.py)
        │                                        status: ready
        ▼
11. I say "applied"              memory.py       status: sent, follow-up reminder in 7 days
        │
        ▼
12. Learn                        learn.py        my edits become style rules
                                 memory.py       what I sent becomes an example for step 5
```

Every model call in steps 3, 5 and 7 goes through `llm.chat_json()`, which sends it to the Anthropic API, OpenRouter
or the Claude Code CLI.

- I put all the logic in `cvdrafter/` and kept Streamlit out of it. `ui/` is just a thin layer on top, which is
  why I can test the core offline.
- I route every model call through `llm.chat_json()`, so there's one place that handles backends, JSON parsing and
  cost logging.
- Anything I can check in code (numbers, ids, skills, lengths, banned words), I check in code. I don't trust a
  prompt to do it.

## How I keep each step honest

| Step | File | What I implemented |
|---|---|---|
| 1. Analyse | `analyze.py` | The model extracts keywords, requirements and job details. Then my code decides which keywords are really in my profile and confirms the company name (`company.py`). |
| 2. Write | `generate.py` | I make the model cite fact ids for every bullet and paragraph. `post_process()` forces its output into the template: it can't add jobs or skills, and I take names, dates and titles straight from my profile. |
| 3. Check | `verify.py` | Rule checks run first, then a second, stronger model fact-checks each item against the facts it cites. Errors go back to the writer, up to `MAX_FIX_ROUNDS` rounds. |
| 4. Render | `render.py` | I re-check the exact text I edited before rendering. If errors remain, nothing gets rendered unless I override it, and I record the override. Every render goes into its own timestamped folder under `output/`, so nothing gets overwritten. |

## What gets checked

I wrote these as rule checks in `verify.check_deterministic()`. Each one is an error:

- a citation is missing, points to an unknown id, or uses a fact from a different job
- a number doesn't appear in the cited facts
- a skill or posting keyword isn't in my profile
- a term from my `guardrails.yaml` shows up
- the text is too long for my one-page layout
- the cover letter sounds like AI (`voice.py`)

## Storage

| What | Where |
|---|---|
| My facts, guardrails, style rules | `data/*.yaml`, `data/*.md`: plain files I can edit by hand. Every in-app save backs up the old version to `data/.versions/`. |
| Applications, status timeline, LLM call log | `data/applications.db` (`memory.py`). New columns get added automatically on startup. |
| Every generated CV + cover letter | `drafts` table in `data/applications.db`, one timestamped row per generation, so regenerating never loses the earlier draft. The Versions tab of an application lists them. |
| Rendered documents | `output/<company>_<role>_<id>/<timestamp>/`, plus a `render.json` the Versions tab reads |

## Models

| Role | Default | What I use it for |
|---|---|---|
| writer | Sonnet | analysis, writing, fixes |
| checker | Sonnet | fact-checking every statement against the fact bank. Sonnet is the standard model the app is tuned for. |
| fast | Haiku | chat commands |

A model id like `claude-sonnet-5-5` goes to the Anthropic API with your own key. A name like `vendor/model` goes to
OpenRouter. A name like `sonnet` runs through Claude Code on whatever account it's signed in with; the app only offers
that once you pick Claude Code on the Connect AI page. Paid models start on when you connect a key and off with Claude
Code. When they're off, any call that would cost money is blocked.

## Run and test

```bash
./run.sh                          # http://localhost:8501
.venv/bin/python -m pytest -q     # offline, uses tests/fixtures/, never touches my data/
```

## Where to change things

| To… | Edit |
|---|---|
| change prompts | `cvdrafter/prompts.py` |
| add a check | `verify.check_deterministic()` + a test in `tests/test_offline.py` |
| change length limits | `templates/slots.yaml` |
| change the CV design | `templates/cv_template.docx` (keep the `{{ }}` tags) |
| add a database column | `memory._COLUMNS` and `memory._MIGRATIONS` |
| add a chat command | `assistant.quick_action()` |
| add a page | a new file in `ui/`, registered in `app.py` |

## Limitations

- I built PDF export around Microsoft Word on macOS. LibreOffice is only a fallback.
- I built it for myself, running locally. It has no auth and no protection against concurrent writes.
