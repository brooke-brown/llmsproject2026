# CLAUDE.md

Shared guidance for every group member's Claude agent working in this repo. Keep it short and current: if a decision here changes, update this file in the same PR.

## What we're building

**STRIVE** is a Django web app that pulls a student team's GitHub activity, has an LLM write evidence-linked weekly summaries, validates every claim against real GitHub data, and flags imbalanced teams for instructors (CS4501, Fall 2026). **Every claim links to a specific commit, PR, review or issue.**

**Current goal: alpha for instructor/TA validation on Tuesday Nov 3, 2026.** Build the thin end-to-end slice only. Do not add features outside the alpha scope below.

Product rationale, audiences and the instructor demo flow: `docs/readme-startupSelected.md`. Read it when scoping a new feature, not for routine work.

Reading `directions/` is blocked in `.claude/settings.json`; leave it that way. It's contents are irrelevant to this project, but must stay for documentation purposes.

## Stack

- Python 3.12, Django (server-rendered templates), SQLite locally
- PyGithub for GitHub data (token in `GITHUB_TOKEN`)
- LLM SDK with structured JSON output; claim shape defined in Pydantic (`summaries/schemas.py`)
- Pico.css from CDN for styling: no JS framework, no build step
- Config via `.env` (see `.env.example`). Never commit `.env` or secrets.
- Run: `python manage.py runserver`. Test: `python manage.py test`.

## Apps and owners

Each person owns one app. **Only edit files in your own app** unless the task says otherwise. If you need a change in another app, describe it and stop; the owner makes it.

| App | Owner | Contents |
|---|---|---|
| `core/` | Todd | `User` (custom, has `github_login`), `Course`, `Enrollment`, `Project`, `Team`, `TeamMembership`, `Repo`, `Requirement`, requirements PDF upload + extraction (pypdf) + LLM split, admin, seed command |
| `ingest/` | Todd | `Artifact` model, `ingest(repo, week_start, week_end)`, co-author parsing, unattributed commits |
| `summaries/` | Akshita | `Summary`, `Claim`, `ValidationRun`, LLM prompt, Pydantic claim schema, validator + retry |
| `dashboard/` | Brooke | Views, templates, `Flag` model + rules, "Generate summary" button. Brooke also owns the deploy and Tuesday's demo script and interview questions. |

## Shared contracts: do not change without team agreement

These are the interfaces between people. Changing them silently breaks someone else's work. If a task seems to need a contract change, stop and say so instead of making it.

- **Models in `core/` and `ingest/`** (field names and types)
- **Claim schema** in `summaries/schemas.py`. Requirement mapping and significance are based on what the work accomplishes, not lines changed; empty `requirement_ids` means not tied to a requirement.
  ```
  {"text": str,
   "category": "feature" | "bug fix" | "tests" | "refactor" | "review",
   "artifact_ids": [str],
   "requirement_ids": [int],
   "significance": "minor" | "moderate" | "major"}
  ```
  - `artifact_ids` use the format `owner/repo:type:id`, such as `acme/app:pr:12`, so they stay unique across a team's repos.
  - `requirement_ids` hold `Requirement.number`, not the database ID.
- **Reporting week:** Monday-Sunday inclusive, in `America/New_York`. Ingest and the validator must use the same date for each artifact:
  - Commit: author date
  - PR: merged date, or created date if unmerged
  - Review: submitted date
  - Issue: created date
- **`Requirement` model** in `core/`: `project`, `number`, `text`
- **Function signatures:**
  - `ingest.services.ingest(repo: Repo, week_start: date, week_end: date) -> list[Artifact]`
  - `summaries.services.generate_summary(user: User, team: Team, week_start: date) -> Summary`
  - `dashboard.flags.compute_flags(team: Team) -> list[Flag]`

## Data model

```
Course                     e.g. "CS 3240, Fall 2026"
 ├── Enrollment            User ↔ Course, with role: instructor | ta | student
 └── Project               optional requirements PDF or pasted text → Requirement rows
      └── Team             exists only inside this project
           ├── TeamMembership   User ↔ Team (students)
           └── Repo             the team's GitHub repo(s)
                └── Artifact    a commit, PR, review or issue pulled from the repo

Summary          → Team + User + reporting week
  └── Claim      → claim schema above + status (verified / couldn't verify)
ValidationRun    → per-run log: first-pass rate, retries, drop rate
Flag             → Team
```

- **Teams belong to a project, not the course.** Classes that reshuffle teams per project get new teams per project; a semester-long class has one project with one set of teams. Past projects' teams and reports are never edited.
- Reusing teams in a later project is a UI feature ("Copy teams from a previous project"), not a schema change.
- **Roles live on `Enrollment`, never on `User`.** One person can be a TA in one course and a student in another.
- **Every user has `User.github_login`.** It's how commits, PRs and reviews are matched to students.
- **Repo is its own table** so a team can have more than one repo.
- Use the word **"Team"** everywhere (code, UI, issues), never "group."
- Later, optional: `joined_at`/`left_at` on `TeamMembership` for students who switch or drop mid-project.

## Alpha scope

**In:**
- Admin-based setup; repo ingest by URL + token, including co-author trailers and PR review credit
- Requirements PDF (or pasted text) → `Requirement` rows editable in admin; requirement tags + significance on claims; simple per-project requirement table
- "Generate summary" button, which calls `ingest`, then `generate_summary`
- Validator, retry, drop + "N couldn't be verified" note, `ValidationRun` logging
- 1–2 flag rules, based on share of requirement-tied claims weighted by significance
- Pages: course overview (teams, flagged ones first) → team (students and their summaries) → summary (claims with GitHub links and the unverified note)

**Fallback:** if the core pipeline isn't working end to end by Monday midday, cut requirements.

**Out (beta):** polished per-requirement progress view, numeric diff checks, student view, disputes, self-reports, GitHub OAuth login, consent, CSV import, background jobs, "paired with" detection.

There is **no chat interface**, in the alpha or later. Users only see validated summaries, team views and flags.

## Rules for LLM output and the validator

- **No LLM-generated text reaches a user without passing the validator.**
- Summaries describe observable activity only, with no judgments of character.
- Every claim cites at least one `Artifact`, rendered as a GitHub link.
- Validator checks, using stored GitHub data: each cited artifact exists in one of this team's repos; it falls inside the reporting week; the student is its author, co-author or reviewer; every `requirement_id` exists for the project.
- Artifact checks are verified facts. Requirement mapping and significance are LLM judgments; label them as such in the UI.
- The validator returns specific, machine-readable errors per claim, not just pass/fail. Those errors feed back to the LLM, which regenerates **only the failing claims**, up to **2 retries (3 attempts total)**.
- A claim that still fails is dropped from the user-facing summary and counted as "couldn't verify."
- The validator is plain Python with no LLM calls, and must have unit tests.

## Code conventions

- Keep views thin; put logic in `services.py` in each app.
- Write tests from the issue's acceptance criteria before implementing. Every validator check and flag rule needs a test.
- Make a migration for model changes in your own app; never edit another app's migrations.

## Git workflow

- `main` always works. **Never commit directly to `main`**; changes land only through small PRs with one teammate's approval. Pull `main` often.
- One short-lived branch per task, named for its owner: `todd/ingest-commits`, `akshita/validator`, `brooke/course-overview`.
- If a PR completes a GitHub issue, include `Closes #N` in its description.
- One person merges migrations at a time.
- Use `Co-authored-by:` trailers when pairing. STRIVE credits pair programming from them, and our own repo is test data.
- Fixture for working in parallel: `ingest/fixtures/sample_artifacts.json`.

## Secrets and student data

- API keys and GitHub tokens live in `.env` (gitignored). Document new variables in `.env.example` with placeholder values only.
- Never commit `.env`, `.venv/`, `.idea/`, `__pycache__/` or local database files.
- Contribution summaries are student education records (FERPA). Don't log student code or summaries to third-party services, and don't use real classmates' data in tests or fixtures without permission.
