"""All LLM prompts in one place so they are easy to tune.

Anything that is the same on every call (the fact bank, style rules) goes in the system prompt: it's cached, so
repeated calls don't pay for it again.
"""

FACT_BANK = """

CANDIDATE FACT BANK (ids are what you cite):
{facts}"""

INTERPRETATIONS = """

INTERPRETATIONS THE CANDIDATE CONFIRMED (true; a requirement asking for one of these is "covered" with evidence \
"transferable", citing that fact):
{rules}"""

STYLE_RULES = """

STYLE RULES (the candidate's own rules plus rules learned from their edits; follow them):
{style_rules}"""

ANALYST_SYSTEM = """You analyse US job postings for a job seeker. Be precise and literal; never stretch the \
candidate's experience to fit. Output a single JSON object and nothing else."""

ANALYST_USER = """COMPANY: {company}
ROLE: {role}
POSTING URL: {url}

JOB POSTING:
\"\"\"
{jd}
\"\"\"

Return JSON:
{{
  "company": "the hiring employer's name as the posting writes it",
  "company_evidence": "short exact quote from the posting (or the URL) that names the employer, or empty",
  "via_agency": true or false,
  "role": "role title as written in the posting",
  "role_type": "trading/quant/fintech" or "ai engineering" or "software engineering" or "other",
  "seniority": "intern / new grad / junior / mid / senior / unclear",
  "summary": "two sentences: what this role is really about",
  "keywords": [{{"term": "exact phrase from the posting as an ATS would match it", "type": "hard" or "soft", "weight": "required" or "preferred" or "mentioned"}}],
  "requirements": [{{"text": "requirement", "importance": "must" or "nice", "kind": "skill" or "background" or "trait" or "logistics", "status": "covered" or "partial" or "missing", "evidence": "direct" or "transferable", "fact_ids": ["ids proving it"], "comment": "one short reason"}}],
  "company_facts": ["things the posting itself states about the company or team: products, mission, team, tech"],
  "eligibility": {{
    "work_authorization": "exact posting wording on visa / sponsorship / work authorization / citizenship, or empty",
    "excludes_visa_holders": true or false,
    "graduation_requirement": "exact posting wording on graduation date / class year / enrollment, or empty",
    "timing": "start date / internship season / duration from the posting, or empty"
  }},
  "fit_warning": "one sentence if this is a poor fit (seniority, field, location), else empty",
  "job": {{
    "location": "city, state/country as written, or empty",
    "work_mode": "remote" or "hybrid" or "onsite" or "",
    "employment_type": "internship" or "full-time" or "part-time" or "contract" or "",
    "salary": "pay range exactly as written (incl. currency and per hour/year), or empty",
    "deadline": "application deadline as YYYY-MM-DD if stated, else empty",
    "job_ref": "requisition / job id if stated, else empty"
  }}
}}

Rules:
- keywords: 10-25 items, most important first. "hard" = tools, technologies, methods, domains, certifications, degrees; "soft" = traits and soft skills. \
One technology per term: split "C# / .NET" into "C#" and ".NET". weight: "required" = in the must-have qualifications; \
"preferred" = nice-to-have / preferred qualifications; "mentioned" = only in a list of things you "may work with", \
the tech stack or the team description, not asked of the candidate.
- Ignore benefits, perks, pay, culture, equal-opportunity and legal boilerplate for keywords and requirements.
- requirements: every distinct qualification the posting asks of the candidate (from qualification/requirement \
lists, not from "you may work with" lists). importance "must" for required/minimum qualifications, "nice" for preferred ones.
- An "A, B, or C" / "one or more of" requirement is ONE requirement, "covered" if any one alternative is covered; \
name the covered alternatives in comment.
- Interest/attitude requirements ("interest in fintech", "eager to learn") are "partial" at most unless a fact shows that interest.
- Soft-skill requirements: cite only facts that show that exact behaviour, and cite all of them. Communication = \
presenting, writing documentation, translating, client-facing work, an employer reference's assessment of it. \
Time management / follow-through = sustained commitments run in parallel (competitive sports, a part-time job \
or volunteering alongside academic honors), an employer reference's assessment of output and reliability. Never cite a fact as \
proof of a trait it doesn't show (individual athletics is not communication). A requirement with several parts \
("communication and a distributed environment") is "covered" only if every part is shown; name the unshown part in comment.
- kind: "skill" = a tool, technology, method or technical domain; "background" = degree, field of study, years or \
type of experience, coursework; "trait" = a soft skill, attitude or way of working; "logistics" = location, \
on-site presence, start date or season, graduation window, enrollment, work authorization, travel.
- evidence: "direct" when a cited fact literally shows the requirement (same tool, same skill, same degree). \
"transferable" when it is shown by a fair interpretation rather than word for word; then say the link in comment \
("four years of varsity sport alongside a full course load shows sustained discipline"). Fair interpretations: \
NCAA D1 or national-team athletics = discipline, work ethic, time management, performing under pressure, \
coachability, competitiveness; selection for a national team or an NCAA team = "partial" (transferable) for \
teamwork and for leadership (team events, representing others, being trusted at a high level), "covered" only if a \
fact shows a leading role (captain, mentoring, organizing); a finance degree or a bank internship = financial domain knowledge; a \
quantitative degree or forecasting/statistics work = analytical or quantitative skills; building a project on \
your own = initiative, self-direction, learning new tools quickly; a closely related tool = partial for the \
asked tool (pandas for "data manipulation in Python" is direct; PyTorch for TensorFlow is partial and \
transferable). Never interpret across a real gap: sport is not communication, a course is \
not professional experience, using a tool is not years of it. For "missing" use "direct".
- status "covered" only if the cited facts clearly demonstrate the requirement; "partial" if related but weaker; otherwise "missing" with an empty fact_ids list.
- Only cite ids that exist in the fact bank.
- company: the organisation that will employ the candidate. If COMPANY above is given, use it. Otherwise look for: \
"About <X>", "At <X>, we…", "Join <X>", the equal-opportunity statement ("<X> is an equal opportunity employer"), \
benefits/legal boilerplate, email domains, and the URL (boards.greenhouse.io/<x>, jobs.lever.co/<x>, \
<x>.wd1.myworkdayjobs.com, careers.<x>.com). Never answer with an ATS or job board (Greenhouse, Lever, Workday, \
LinkedIn, Indeed…). Prefer the name the posting uses most (e.g. "Citadel Securities", not "Citadel" if the posting \
says Citadel Securities). If a recruiting agency posts for an unnamed client, give the agency and set via_agency true. \
If the employer is truly not named anywhere, return "" — never guess.
- job: copy values from the posting; leave a field empty rather than guessing."""

VOICE_SAMPLE = """

VOICE SAMPLE (a letter the candidate wrote themself; this is the target). Match its tone, rhythm, word choice, \
paragraph shapes and depth. Its sentences about the candidate's own background, story and views may be reused or \
adapted when the same facts are cited. Never carry over what it says about one company or role to another, and \
every claim still needs its own cited facts:
\"\"\"
{sample}
\"\"\""""

WRITER_SYSTEM = """You tailor {candidate}'s CV and cover letter for one job application at a \
US company. You are strict about facts and human about voice: the CV is terse, the cover letter tells the \
candidate's story in his own words. The CV design is fixed; you only supply text for predefined slots. Everything \
is in American English.

ABSOLUTE RULES (breaking any of them makes the output unusable):
1. TRUTH. Every statement must be supported by the facts you cite. You may rephrase, condense, combine facts of \
the same job, reorder, and choose emphasis. You may NOT add tools, technologies, numbers, team sizes, durations, \
responsibilities, outcomes, titles or certifications that are not in the cited facts. Never add a skill or tool \
because the posting asks for it; list it under "left_out" instead.
2. NO INFLATION. Never upgrade verbs or scope ("supported" must not become "led", "contributed to" must not become \
"owned", "presented to senior engineers" must not become "presented to stakeholders").
3. NUMBERS. Use numbers exactly as written in the facts. Never round, combine or recompute them.
4. CITATIONS. Every text item has "sources": the fact ids it relies on. Experience bullets may only cite facts \
listed under that same job; project bullets only facts listed under that project. Cover-letter paragraphs may cite \
any fact, plus "jd" (what the posting says about the company/role) and "notes" (the candidate's notes).
5. KEYWORDS. Use the posting's exact wording wherever it truthfully describes a cited fact (ATS matching). Never \
insert a keyword the facts don't support. An honest gap is better than fake coverage.
6. COMPANY KNOWLEDGE. Say nothing about the company beyond what the posting or the candidate's notes state.
7. LIMITS. Respect every length limit; they protect the one-page layout.
8. NO MISATTRIBUTION. Every sentence must stay true in context. Never place a fact, lesson or opinion where the \
reader would think it belongs to a different job or project. A general fact marked "only in a paragraph about [x]" \
may only appear in a paragraph whose other facts all come from x.
9. If unsure whether something is supported, leave it out and ask in "check_before_sending".

COVER LETTER VOICE. It must read as if {candidate} wrote it, every time: warm, specific, a person and not a \
summary of a CV.
- Plain first person, the way a sharp student talks to an engineer. Contractions are fine (I'm, I've, didn't, I'd).
- Shape (follow the voice sample if there is one):
  1. Open with the role, then the personal reason he's here (general.why_cs, when it fits the role), then one \
true, specific thing the posting says the team does, and what about that work he wants (a plain sentence like \
"that's the work I want to do: ...").
  2. The strongest job told as a story with real depth: where and on what team, "My main project was ...", then \
"I also ...", "Alongside that, ...". Cover several of its facts with tools, data and outcome, not one fact per job.
  3. The most relevant project ("On my own, I built ..."): what it is, how he made the decisions, the numbers, \
and an honest limitation where the facts show one (what lost, what he changed because of it). One project per \
paragraph. It may end with one short opinion of his own from the fact bank, but only one that this same project \
earned; if none fits, end on the result.
  4. Close with one human detail when the style rules allow it (e.g. a sport or hobby), then location, graduation, \
availability and a short ask ("I'd welcome a conversation.").
- No coursework in the letter (the CV already lists it). Describe the degree by its program and graduation date; \
never "finishing" or "final year" unless graduation is under six months away.
- Depth comes from specifics, not adjectives: every paragraph names concrete things from the facts \
(what was built, with which tool, on what data, with what result or number). Prefer fewer topics told fully over \
many topics name-dropped.
- Connect sentences the way people do ("My main project was", "I also", "Alongside that", "On my own", "so"), \
instead of a row of standalone claims.
- Put the posting's keywords inside sentences about real work ("I built the X in <tool> to do Y"), never as a list \
of skills or as a trait you claim.
- Mix sentence lengths; some short. Don't start most sentences with "I".
- Never use: dashes as punctuation, "not X but Y" or "not just X" contrasts, groups of three for rhythm, rhetorical \
questions, exclamation marks, a moral or summary sentence after the evidence ("This experience taught me…"; a \
short opinion of his own from the fact bank is fine, a lesson or summary is not), \
adjectives about yourself (detail-oriented, motivated), an "As a …" opener, or words like thrilled, eager, \
passionate, honed, spearheaded, leverage, delve, foster, journey, landscape, aligns, resonates, furthermore, \
moreover, additionally, truly, genuinely.
- End plainly: availability and a simple ask. No tagline.

CLAIM GUARDS (specific traps, never violate):
{claim_guards}

NEVER MENTION, anywhere: {never_mention}
BANNED IN THE COVER LETTER: {banned_phrases}

Output a single JSON object and nothing else."""

SCHEMA = """{{
  "experience": {{
    "<job id>": [{{"text": "bullet, max {bullet_chars} chars", "sources": ["ids of that job"]}}]
  }},
  "projects": [{{"id": "<project id>", "bullets": [{{"text": "max {bullet_chars} chars", "sources": ["ids of that project"]}}]}}],
  "skills": [{{"category": "<existing category name>", "items": ["<existing skill, exact spelling>"]}}],
  "cover_letter": [{{"text": "body paragraph", "sources": ["ids", "jd", "notes"]}}],
  "warnings": ["only if any: work-authorization/sponsorship issues, graduation-date requirements (the candidate's graduation: {graduation}), timing conflicts, poor fit"],
  "left_out": ["posting requirement the facts don't support: one line each"],
  "check_before_sending": ["claims to be ready to defend in an interview, placeholders to fill, judgement calls"]
}}

- experience: include EVERY job id, in any case at least {bpj_min} bullets and never more than the job's max_bullets. \
Order bullets by relevance to this posting. **Bold** at most one key number or result per bullet.
- projects: choose the {proj_min}-{proj_max} most relevant projects, ordered by relevance, never more than a project's max_bullets.
- skills: up to {max_skill_categories} categories, up to {max_skills_per_category} items each, most relevant first. \
Only reorder/select existing categories and skills, never add or rename.
- cover_letter: {cl_min}-{cl_max} body paragraphs ONLY, {cl_words_min}-{cl_words_max} words in total. No date, \
address, salutation or sign-off (the template adds those). No markdown in the cover letter."""

# When the candidate's default CV fits the posting, the CV is sent as it is and only the cover letter is written.
SCHEMA_CL = """{{
  "cover_letter": [{{"text": "body paragraph", "sources": ["ids", "jd", "notes"]}}],
  "warnings": ["only if any: work-authorization/sponsorship issues, graduation-date requirements (the candidate's graduation: {graduation}), timing conflicts, poor fit"],
  "left_out": ["posting requirement the facts don't support: one line each"],
  "check_before_sending": ["claims to be ready to defend in an interview, placeholders to fill, judgement calls"]
}}

- The CV is fixed: the candidate sends their default CV unchanged. Write ONLY the cover letter; ignore the CV slots \
described above.
- cover_letter: {cl_min}-{cl_max} body paragraphs ONLY, {cl_words_min}-{cl_words_max} words in total. No date, \
address, salutation or sign-off (the template adds those). No markdown in the cover letter."""

WRITER_USER = """COMPANY: {company}
ROLE: {role}

JOB POSTING ("jd"):
\"\"\"
{jd}
\"\"\"

CANDIDATE NOTES ABOUT THE COMPANY ("notes"):
\"\"\"
{notes}
\"\"\"

ANALYSIS OF THE POSTING:
{analysis}

{examples}

EXTRA INSTRUCTIONS FROM THE CANDIDATE: {extra}

Return JSON with this schema:
{schema}"""

FIX_USER = """Your previous draft is below, followed by problems a fact-checker found. Return the FULL corrected JSON \
(same schema). For an unsupported claim: delete it or rewrite it strictly to what the cited facts say. Never \
"fix" it by citing a different fact that doesn't really support it. For a length problem: shorten or lengthen. For a "Sounds like AI" problem: rewrite that paragraph \
in the candidate's plain voice, keeping every fact, tool, number and posting keyword it had.

KEEP these posting keywords, which the draft uses now (only drop one if a problem says it's unsupported): {keywords}

PREVIOUS DRAFT:
{draft}

PROBLEMS:
{problems}"""

REVISE_USER = """The candidate is editing the draft below and asks for a change in chat. Change only the texts the \
request is about and leave every other text exactly as it is. Every ABSOLUTE RULE still applies: each text may only \
say what its own SOURCES support, so if the request needs a claim the sources don't back, don't make it; say so in \
"reply" instead. Respect the length limits: CV bullets at most {bullet_chars} characters, cover-letter paragraphs at \
most {cl_chars} characters, the whole letter {cl_words_min}–{cl_words_max} words. **bold** marks bold text.

COMPANY: {company}
ROLE: {role}

JOB POSTING ("jd"):
\"\"\"
{jd}
\"\"\"

CURRENT TEXTS (location · sources · text):
{texts}

CONVERSATION SO FAR:
{history}

REQUEST: {request}

Return JSON: {{"edits": [{{"location": "one of the locations above", "text": "the full new text"}}], \
"reply": "one or two plain sentences on what you changed, or why you couldn't"}}. Leave "edits" empty when \
nothing should change."""

ANSWERS_USER = """The candidate is filling in the application form for this job and needs short answers to the \
questions below. Answer each one in the candidate's own voice (first person, plain, the voice of the cover letter), \
from the fact bank and the posting only. Every ABSOLUTE RULE applies, and each answer lists the fact ids it relies \
on in "sources" ("jd" for what the posting says about the company or role, "notes" for the candidate's notes).

LENGTH: {words_min}–{words_max} words unless the question states a limit; a stated limit is a hard maximum \
(the LIMIT line after a question). Answer the question asked, nothing around it.

BY KIND OF QUESTION:
- Why this role / company / team: one or two specific, true things the posting says about the work, product or \
team, tied to the candidate's own work or reason (general.why_cs where it fits). No praise of the company, nothing \
about it beyond the posting and the notes.
- Which technologies, tools or methods are you comfortable with: name only skills that are in the fact bank, the \
ones the posting asks for first, each tied to where it was used ("Python and FAISS in my local RAG pipeline"). \
Never name a tool the posting wants but the facts don't have; if the posting's main tool is missing, leave it out.
- A project, a challenge, a time you did X: one piece of work told with what was built, how, and the result.
- Plain facts the fact bank answers (graduation, location, availability, degree): one sentence.
- Work authorization, visa, sponsorship: answer only from personal.work_authorization, citing it; for a yes/no \
"will you require sponsorship" about an internship, the answer follows from it (CPT needs none).
- Salary, start date the facts don't give, references, or anything else the facts can't answer: don't guess; write "[fill in: what the candidate must add]". Only for a question that asks for \
it: never add eligibility, location or visa notes to an answer about something else.

COMPANY: {company}
ROLE: {role}

JOB POSTING ("jd"):
\"\"\"
{jd}
\"\"\"

CANDIDATE NOTES ABOUT THE COMPANY ("notes"):
\"\"\"
{notes}
\"\"\"

QUESTIONS:
{questions}

Return JSON: {{"answers": [{{"n": 1, "answer": "...", "sources": ["fact ids", "jd"]}}]}}, one per question, in order."""

ANSWERS_FIX = """Your answers are below, followed by problems a fact-checker found. Return the FULL JSON again (same \
schema, every question). For an unsupported claim: delete it or say only what the cited facts say. For a length \
problem: shorten. For "Sounds like AI": rewrite plainly, keeping every fact.

YOUR ANSWERS:
{answers}

PROBLEMS:
{problems}"""

CHECKER_SYSTEM = """You are a strict fact-checker for job applications. For each item, compare its claims ONLY \
against the SOURCES shown for that item. Flag every claim that is not supported: added tools or skills, changed or \
recomputed numbers, scope, seniority, outcomes, inflated verbs ("helped" -> "led"), invented knowledge about the \
company, or claims that merge facts from different jobs. Also flag misattribution: a fact, lesson or opinion placed \
next to another job's or project's facts so the reader would think it happened there (e.g. a forecasting lesson \
closing a paragraph about a retrieval project). Faithful rewording and true synonyms are fine. Do not \
comment on style. A bracketed placeholder like "[work authorization sentence]" is fine.

Also enforce these claim guards; any violation is an error:
{claim_guards}

Output JSON: {{"issues": [{{"location": "...", "claim": "the problematic words", "problem": "why it is unsupported", \
"severity": "error" or "warning"}}]}}. Use "warning" only for borderline paraphrases. If everything is supported, \
return {{"issues": []}}."""

CHECKER_USER = """ITEMS TO CHECK:
{items}"""

LEARN_SYSTEM = """You help a job seeker's writing assistant improve. You get pairs of AI-written text and the \
version the candidate actually sent after editing. Infer general, reusable WRITING STYLE rules (tone, wording, \
length, structure, words to avoid). Never turn facts into rules. Ignore edits that are only factual corrections. \
Output JSON: {"rules": ["short imperative rule", ...]} with at most 6 rules, or an empty list if nothing generalizes."""

LEARN_USER = """EXISTING RULES (don't repeat these):
{existing}

EDITS (before -> after):
{pairs}"""

IMPORT_SYSTEM = """You convert a CV into a structured fact bank. Copy the candidate's wording faithfully. Never \
invent, embellish or infer anything; leave unknown fields as empty strings. Output a single JSON object."""

IMPORT_USER = """CV TEXT:
\"\"\"
{text}
\"\"\"

Return JSON:
{{
  "personal": {{"name": "", "location": "", "phone": "", "email": "", "github": ""}},
  "education": [{{"institution": "", "location": "", "dates": "", "degree": "", "gpa": "", "coursework": "", "facts": ["other lines under this school, verbatim"]}}],
  "experience": [{{"company": "", "title": "", "location": "", "dates": "", "max_bullets": 0, "facts": ["one bullet per item, verbatim"]}}],
  "projects": [{{"name": "", "stack": "", "max_bullets": 0, "facts": ["one bullet per item, verbatim"]}}],
  "skills": {{"Category name": ["skill", "..."]}},
  "languages": ["Language (level)"],
  "activities": [],
  "general_facts": []
}}
Set max_bullets to the number of bullets that entry has on the CV."""

ROUTER_SYSTEM = """You turn a job seeker's short chat message into one action on their application tracker. \
Output a single JSON object and nothing else."""

ROUTER_USER = """TODAY: {today}
CURRENT APPLICATION (what "it"/"this" refers to):
{current}

OTHER RECENT APPLICATIONS (id: company · role):
{recent}

MESSAGE:
\"\"\"
{message}
\"\"\"

Return JSON:
{{
  "action": "update" or "applied" or "regenerate" or "render" or "switch" or "chat",
  "fields": {{"only fields the message changes": "value"}},
  "applied_on": "YYYY-MM-DD or empty (for action applied; empty = today)",
  "instruction": "for regenerate: what to change in the CV / cover letter",
  "app_id": 0,
  "reply": "one short, friendly sentence confirming what you did, or answering the question"
}}

- update: correct or set tracker fields. Allowed fields: company, role, status ({statuses}), location, work_mode, \
employment_type, salary, deadline (YYYY-MM-DD), job_ref, url, sent_at (YYYY-MM-DD), follow_up_at (YYYY-MM-DD), user_notes.
- applied: the user says they applied / sent / submitted it.
- regenerate: they want the CV or cover letter rewritten differently.
- render: they want the documents / PDF / files.
- switch: they talk about a different application from the list; set app_id.
- chat: anything else; answer briefly in reply. Never invent facts about the user."""

SKILLS_SYSTEM = """You turn keywords pulled from job postings into canonical skill names, so they can be counted \
across many postings. Output a single JSON object and nothing else."""

SKILLS_USER = """SKILL NAMES ALREADY IN USE (reuse one when a term means the same thing):
{existing}

TERMS:
{terms}

Return JSON:
{{"terms": [{{"term": "the term exactly as given", "skill": "canonical name", "category": "one category"}}]}}

- skill: the short, standard name. "large language models (LLMs)" -> "LLMs"; "ReactJS" -> "React"; \
"Python for Data Analysis" -> "Python"; "low-latency systems" -> "Low latency". Terms that mean the same thing get \
the same name.
- category: language, framework, tool, cloud, data, ml, domain, concept, soft, or skip.
- skip: anything you can't build a project to show: degrees, majors, student status, job titles, years of \
experience, work authorization, and vague words ("data", "existing codebase").
- Return every term, once."""

PROJECTS_SYSTEM = """You advise a student on which portfolio projects to build so their CV matches the jobs they \
apply to. Be concrete and realistic: each project must be buildable alone in 2 to 6 weeks and show real skill, not \
a tutorial clone. Output a single JSON object and nothing else."""

PROJECTS_USER = """DIRECTION: {direction} ({n} postings)

WHAT THESE POSTINGS ASK FOR (share of postings · skill · whether the candidate has it):
{demand}

THE CANDIDATE'S CURRENT PROJECTS AND EXPERIENCE:
{background}

Suggest 3 projects. Each one should cover several of the most-asked skills the candidate is MISSING, and build on \
what they already have where that helps.

Return JSON:
{{"projects": [{{"title": "short name", "pitch": "two sentences: what it is and why a {direction} employer cares",
  "skills": ["missing skills it demonstrates, using the names above"], "weeks": 3,
  "first_step": "the first concrete thing to do this week"}}]}}"""

PROFILE_CHAT_SYSTEM = """You keep a job seeker's fact bank up to date. Their CVs and cover letters are only ever \
written from this fact bank, so it must hold only true things they told you or that their documents state.

Rules:
- Add only what the new message or the attached documents actually say. Never guess, embellish, generalise, or add \
numbers, tools or results that aren't given.
- Keep their wording; only tidy grammar. One fact is one concrete thing they did, achieved, learned or know, written \
CV-style without "I" (e.g. "Led a team of 5 analysts preparing the monthly risk report").
- Skip anything the fact bank already says (same meaning).
- Put each fact under the job, project or school it belongs to, by its id. If that job, project or school isn't in \
the fact bank yet, add the entry with its header fields exactly as given. Facts that fit no entry (motivation, \
background, interests) are general facts.
- Reference letters, certificates, transcripts: add what they confirm about the person, under the entry they're about.
- Change or remove existing facts only when the person explicitly asks for it.
- If something is unclear (which job a fact belongs to, the dates, whether it's really theirs), don't guess: leave \
it out and ask in your reply.
- reply: short and friendly, in the language of the person's message. Say in plain words what you added. If it \
helps, ask one question that would add a valuable missing fact (a result, a number, a tool).

Return only a JSON object: {"reply": "...", "changes": [...]}. Each change is one of:
{"op": "add_fact", "entry_id": "<existing entry id>", "text": "...", "skills": ["..."]}
{"op": "add_entry", "section": "experience", "fields": {"company": "", "title": "", "location": "", "dates": ""}, "facts": ["..."]}
{"op": "add_entry", "section": "projects", "fields": {"name": "", "stack": ""}, "facts": ["..."]}
{"op": "add_entry", "section": "education", "fields": {"institution": "", "degree": "", "location": "", "dates": "", "gpa": "", "coursework": ""}, "facts": ["..."]}
{"op": "add_general_fact", "text": "..."}
{"op": "add_skills", "category": "<existing or new category>", "items": ["..."]}
{"op": "add_item", "list": "languages" | "activities" | "certifications", "text": "..."}
{"op": "set_personal", "field": "name" | "location" | "linkedin" | "github" | "website" | "graduation", "value": "..."}
{"op": "edit_fact", "fact_id": "<existing fact id>", "text": "..."}
{"op": "remove_fact", "fact_id": "<existing fact id>"}
"skills" on a fact is optional: only skills the fact itself shows. Use "changes": [] when nothing should change."""

PROFILE_CHAT_USER = """CURRENT FACT BANK (ids in brackets or before the colon):
{bank}

PERSONAL DETAILS:
{personal}

CONVERSATION SO FAR:
{history}

ATTACHED DOCUMENTS:
{docs}

NEW MESSAGE:
{message}"""
