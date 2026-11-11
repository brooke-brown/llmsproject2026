# CLAUDE.md

Shared guidance for every group member's Claude agent working in this repo. Keep it short and current: if a decision here changes, update this file in the same PR.

## What we're building

**STRIVE** is a Django web app that pulls a student team's GitHub activity, has an LLM write evidence-linked summaries, validates every claim against real GitHub data, and flags imbalanced teams for instructors (CS4501, Fall 2026). **Every claim links to a specific commit, PR, review or issue.**

**Current goal: the beta.** The alpha is built and is what's on `main`. Do not add features outside the beta scope below.

Product rationale, audiences and the instructor demo flow: `docs/readme-startupSelected.md`. Read it when scoping a new feature, not for routine work.

Reading `directions/` is blocked in `.claude/settings.json`; leave it that way. It's contents are irrelevant to this project, but must stay for documentation purposes.

## Stack

- Python 3.12, Django (server-rendered templates), SQLite locally
- PyGithub for GitHub data (token in `GITHUB_TOKEN`)
- LLM SDK with structured JSON output; claim shape defined in Pydantic (`summaries/schemas.py`)
- Pico.css from CDN for styling: no JS framework, no build step
- Config via `.env` (see `.env.example`). Never commit `.env` or secrets.
- Run: `python manage.py runserver`. Test: `python manage.py test`.

## Apps

| App | Contents |
|---|---|
| `core/` | `User` (custom, has `github_login`), `Course`, `Enrollment`, `Project`, `Sprint`, `Team`, `TeamMembership`, `Repo`, `Requirement`, requirements PDF upload + extraction (pypdf) + LLM split, admin, seed command |
| `ingest/` | `Artifact` model, `ingest(repo, week_start, week_end)`, co-author parsing, unattributed commits |
| `summaries/` | `Summary`, `Claim`, `ValidationRun`, LLM prompt, Pydantic claim schema, validator + retry |
| `dashboard/` | Views, templates, `Flag` and `FlagDispute` models + flag rules, "Generate summaries" button |
| `contributions/` | `Contribution` and `Corroboration` models: offline work students report and teammates confirm. Never evidence for a claim, passed to the LLM or counted toward flags |

## Shared contracts: do not change without team agreement

These are the interfaces between apps. Changing them silently breaks other work. If a task seems to need a contract change, stop and say so instead of making it, unless its issue already calls for that change.

The `Sprint` model, `generate_sprint_summary` (T-19, with the T-20 chunking and merge) and per-sprint flags (US-41) are built; the other sprint changes below are agreed but not built yet. Where a contract is changing, it says what `main` does today and which issue replaces it.

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
  - `Artifact.artifact_id` is unique per `Repo`, not globally: a later project reusing a GitHub repo has its own `Repo` row and copies. Always look IDs up within the team: `Artifact.objects.filter(repo__team=team, artifact_id=...)`.
  - `requirement_ids` hold `Requirement.number`, not the database ID. Once sprints land, a number only has meaning within its summary's sprint.
- **Reporting dates:** the weekly report covers a Monday-Sunday week, inclusive, in `America/New_York`, until T-21 (#125) removes it. A sprint report (T-19) covers one sprint, from its first day through the earlier of today and its last day, inclusive, in `America/New_York`. `student_week_artifacts` and `unattributed_commits` take an optional `last_day`, and the validator's `validate_sprint_claims` takes the covered range. Ingest stores this date for each artifact in `Artifact.timestamp`, and the validator must use that field:
  - Commit: author date
  - PR: merged date, or created date if unmerged
  - Review: submitted date
  - Issue: created date
- **`Requirement` model** in `core/`: `project`, `sprint`, `number`, `text`, with numbers unique per sprint. `sprint` is optional until T-21 (#125) removes `project`, and requirements with no sprint stay unique per project. New code must use `sprint`.
- **`Sprint` model** in `core/`: `project` (`related_name="sprints"`), `name`, `start_date`, `end_date`, both dates inclusive. `Sprint.covered_through(today)` returns the earlier of `today` and `end_date`.
- **`Repo.full_name`** in `core/` is `owner/repo`, required and unique per team. An artifact's ID must start with its repo's `full_name`. Only repo setup (US-1) reads `Repo.url` to fill this in; everything else uses `full_name`.
- **Reviewers:** a review artifact's `author_login` is the reviewer. There is no separate reviewer field. `Artifact.review_state` holds GitHub's state for a review, such as `APPROVED`, and is empty for other types.
- **Function signatures:**
  - `ingest.services.ingest(repo: Repo, week_start: date, week_end: date) -> list[Artifact]`
  - `summaries.services.generate_summary(user: User, team: Team, week_start: date) -> Summary`, and `summaries.services.generate_sprint_summary(user: User, team: Team, sprint: Sprint) -> Summary` (T-19), which replaces it when T-21 (#125) removes it. A sprint summary has `Summary.sprint` set and `week_start` empty
  - `dashboard.flags.compute_flags(team: Team) -> list[Flag]`

## Data model

What's on `main` today. The planned sprint model follows it.

```
Course                     e.g. "CS 3240, Fall 2026"
 ├── Enrollment            User ↔ Course, with role: instructor | ta | student
 └── Project               optional requirements PDF or pasted text → Requirement rows
      ├── Sprint           name, start and end date; a project has zero or more
      │    └── Requirement numbered per sprint; also keeps `project` until T-21
      └── Team             exists only inside this project
           ├── TeamMembership   User ↔ Team (students)
           ├── Repo             the team's GitHub repo
           │    └── Artifact    a commit, PR, review or issue pulled from the repo
           └── Contribution     offline work a student reports; in any sprint whose dates include its date
                └── Corroboration   a teammate's confirmation, one per teammate

Summary          → Team + User + reporting week
  └── Claim      → claim schema above + status (verified / couldn't verify)
ValidationRun    → per-run log: first-pass rate, retries, drop rate
Flag             → Team + Sprint: each finished sprint keeps its own flags
FlagDispute      → Team + student + rule + Sprint: finds its flag by these, so it survives flags being recomputed
GenerationRun    → Team: exists only while its summaries are generating
```

Planned changes for sprints (#119–#125):

```
Requirement      → belongs to a sprint only, once T-21 removes `project`
Summary          → Team + User + Sprint, replacing the reporting week
```

- **Teams belong to a project, not the course.** Classes that reshuffle teams per project get new teams per project; a semester-long class has one project with one set of teams. Past projects' teams and reports are never edited.
- Reusing teams in a later project is a UI feature ("Copy teams from a previous project"), not a schema change.
- **Roles live on `Enrollment`, never on `User`.** One person can be a TA in one course and a student in another.
- **Every user has `User.github_login`.** It's how commits, PRs and reviews are matched to students.
- **`User.must_change_password`** is set by a temporary password or a password set in the admin, and cleared when the user changes it. Until then, middleware keeps them on the password change page.
- **A team has one repo**, enforced on the dashboard. `Repo` stays its own table and the admin isn't restricted, so code that reads a team's repos must still handle several.
- Use the word **"Team"** everywhere (code, UI, issues), never "group."
- Later, optional: `joined_at`/`left_at` on `TeamMembership` for students who switch or drop mid-project.

## Alpha scope

Built, and what `main` does today:

- Setup: instructors create, rename and delete projects, manage requirements, and manage teams, students and each team's repo from the dashboard; deleting warns what goes with it in its confirmation, including summaries, and is only refused where a claim would lose its evidence or requirement (a repo or requirement that claims cite). Courses and instructor and TA enrollments stay in admin. Repo ingest by URL + token, including co-author trailers and PR review credit
- Requirements PDF (or pasted text) → `Requirement` rows an instructor can edit on the project's requirements page or in admin; requirement tags + significance on claims; simple per-project requirement table
- "Generate summaries" button, one per team on the course overview, which calls `ingest` once per repo, then `generate_summary` once per student; flags are recomputed once every student has succeeded. A team can only have one run at a time (`GenerationRun`). The current week can be generated, but a summary written before its week ended (`Summary.is_partial`) is labelled, counts toward the team page's shares, and never counts toward flags until the week is generated again
- Validator, retry, drop + "N couldn't be verified" note, `ValidationRun` logging
- 1–2 flag rules, based on share of requirement-tied claims weighted by significance
- Pages: course overview (teams, flagged ones first, labels only) → team (flag reasons, students and their summaries) → summary (claims with GitHub links and the unverified note)

## Beta scope

Each item is a GitHub issue. Read the issue before starting; it has the acceptance criteria.

**Sprints (#118–#125).** Agreed, not built yet. New code lands beside the weekly code so `main` keeps working, and T-21 (#125) then removes the weekly code and deletes the alpha summaries.
- A project has zero or more sprints, each with its own dates and requirements. Requirements never belong to a project as a whole. Built (US-39, #121): the project's edit page has a Sprints panel, where instructors create, rename and delete sprints and manage each sprint's requirements, including copying them from an earlier sprint. Dates are locked once a sprint has summaries. The overview's Generate summaries control picks a started sprint (US-40, #122); a sprint run recomputes flags once every student has succeeded, as a week does.
- A new sprint's dates are editable and default to 7 days: from today for the first sprint, or from the latest sprint's last day otherwise.
- Two sprints can overlap only by one sprint's last day being the next one's first day, and can never start on the same day.
- Each student gets one summary per sprint. Before the sprint ends, it covers the sprint's first day through today and is labelled in progress.
- A covered range is split into 7-day chunks, with a leftover of 3 days or fewer joining the previous chunk, so up to 10 days is one chunk. Every student on a team is chunked the same way. Several chunks go through an LLM merge pass and the validator again, and users only ever see the one report.
- Flags are computed per sprint, only once it has ended and every student has a complete summary. A running sprint shows shares but raises no flags or warnings. Built (US-41, #123): `compute_flags` recomputes every such sprint; the course overview is labelled from the team's latest one. Weekly summaries no longer raise flags.
- Built (US-42, #124): the team page has a sprint menu (`?sprint=<id>`), defaulting to the latest sprint the team has a summary for. Its tag, flags, disputes, shares, pie and summary links are all for that sprint. Shares appear once every student has a summary for it, partial or not; a partial one is labelled "in progress, through <date>", and a missing one "Not generated".

**Setup at scale:** CSV import of teams and students (#126, built: instructors upload on the project's edit page, preview, then confirm), and generating temporary passwords in bulk (#127).

**Student side:** student logins (#102, #106: built, with instructors setting temporary passwords on the team page), the student home (#107), flag disputes (#108, #109, with the `FlagDispute` model already built), offline contributions (#104, #110–#112) and peer evaluations (#105, #113–#115). Built so far:
- Disputes (US-32): instructors see disputes on the team page and keep or withdraw the flag. A withdrawn flag no longer counts toward a team being flagged; teams with an open dispute sort right after flagged ones.
- Disputing a flag (US-31): students dispute a flag about them from the student home, and edit the dispute until it's decided. The page is at `teams/<id>/flags/<rule>/<sprint_id>/dispute/`, never `Flag.pk`, because `compute_flags` replaces flags on every run. A decided dispute stays readable after its flag is no longer raised; an open one is hidden.

**Later, with no issues yet:** polished per-requirement progress view, numeric diff checks, GitHub OAuth login, consent, background jobs, "paired with" detection.

There is **no chat interface**, in the alpha or later. Users only see validated summaries, team views and flags.

## Rules for LLM output and the validator

- **No LLM-generated text reaches a user without passing the validator.**
- Summaries describe observable activity only, with no judgments of character.
- Every claim cites at least one `Artifact`, rendered as a GitHub link.
- Validator checks, using stored GitHub data: each cited artifact exists in one of this team's repos; it falls inside the reporting dates; the student is its author, co-author or reviewer; every `requirement_id` exists for the project (for the summary's sprint, once T-19 lands).
- Artifact checks are verified facts. Requirement mapping and significance are LLM judgments; label them as such in the UI.
- The validator returns specific, machine-readable errors per claim, not just pass/fail. Those errors feed back to the LLM, which regenerates **only the failing claims**, up to **2 retries (3 attempts total)**.
- A claim that still fails is dropped from the user-facing summary and counted as "couldn't verify."
- The validator is plain Python with no LLM calls, and must have unit tests.

## Code conventions

- Keep views thin; put logic in `services.py` in each app.
- Write tests from the issue's acceptance criteria before implementing. Every validator check and flag rule needs a test.
- Make a new migration in the app whose models change; never edit a migration that's already merged.

## Git workflow

- `main` always works. **Never commit directly to `main`**; changes land only through small PRs with one teammate's approval. Pull `main` often.
- One short-lived branch per task, starting with your name: `yourname/ingest-commits`.
- If a PR completes a GitHub issue, include `Closes #N` in its description.
- Keep PR descriptions concise, but include crucial information and end every one with a `## Test Metrics` section. We parse these for the end-of-alpha presentation, so keep these exact labels and measure fresh on the PR's branch:
  ```
  ## Test Metrics
  - Tests: <total> total, <passing> passing (<new> new)
  - Line coverage: <percent>% (<covered>/<total> lines)
  - Branch coverage: <percent>% (<covered>/<total>)
  ```
  Get the numbers with `coverage run manage.py test`, then `coverage json -q -o coverage.json`, and read `totals` in `coverage.json` (`covered_lines`/`num_statements`, `covered_branches`/`num_branches`). `.coveragerc` sets what's measured: don't override it.
- One person merges migrations at a time.
- Use `Co-authored-by:` trailers when pairing. STRIVE credits pair programming from them, and our own repo is test data.
- Fixture for working in parallel: `ingest/fixtures/sample_artifacts.json`.

## Secrets and student data

- API keys and GitHub tokens live in `.env` (gitignored). Document new variables in `.env.example` with placeholder values only.
- Never commit `.env`, `.venv/`, `.idea/`, `__pycache__/` or local database files.
- Contribution summaries are student education records (FERPA). Don't log student code or summaries to third-party services, and don't use real classmates' data in tests or fixtures without permission.
