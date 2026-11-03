# STRIVE

Evidence-based contribution reports for team software projects. Built for **CS 4501** (Fall 2026).

STRIVE pulls a student team's GitHub activity, has an LLM write a weekly summary for each student, checks every claim against the real GitHub data, and flags imbalanced teams for instructors. **Every claim links to a specific commit, PR, review or issue**, so staff can check the evidence themselves.

**Status:** alpha, for instructor and TA validation on November 3, 2026. The [alpha scope](CLAUDE.md#alpha-scope) sets what's in and out. The reasoning behind the product is in the [STRIVE pitch](docs/readme-startupSelected.md).

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Using STRIVE](#using-strive)
- [Validation](#validation)
- [Flags](#flags)
- [Data model](#data-model)
- [Project layout](#project-layout)
- [Testing](#testing)
- [Contributing](#contributing)
- [Known limits of the alpha](#known-limits-of-the-alpha)
- [Student data](#student-data)
- [Team](#team)

## How it works

Clicking **Generate summary** for a student and week runs this pipeline:

```
Generate summary
  │
  ▼
ingest               pull commits, PRs, reviews and issues from each of the team's repos
  │
  ▼
generate claims      the LLM reads the student's artifacts and the project's requirements
  │
  ▼
validate             plain Python checks each claim against the stored GitHub data
  │
  ├─ all pass ───────────────────────────────┐
  │                                          │
  └─ some fail ─► regenerate only those      │
                  (up to 2 retries)          │
                     │                       │
                     ├─ now pass ────────────┤
                     │                       ▼
                     └─ still fail ─► drop   save the summary and a ValidationRun
                                             │
                                             ▼
                                     recompute the team's flags
```

Staff then browse **course overview → team → summary**. The summary page shows verified claims with their GitHub links, plus an "N couldn't be verified" note if any were dropped.

### What's collected

| Artifact | Date that places it in a week | Credited to |
|---|---|---|
| Commit (every branch, no merge commits) | Author date | The author and any `Co-authored-by:` students |
| Pull request | Merged date, or created date if unmerged | The author |
| Review (not pending, and not by the PR's author) | Submitted date | The reviewer |
| Issue | Created date | The author |

A reporting week runs Monday to Sunday inclusive, in `America/New_York`.

Ingesting a week again refreshes it. Stored artifacts pick up changes from GitHub and stay one row each, so existing claims keep their links. A failed run leaves the stored data as it was.

## Quick start

You need Python 3.12 (`.python-version` pins it for pyenv). Other versions aren't tested, so check `python --version` first.

```bash
git clone https://github.com/brooke-brown/llmsproject2026.git
cd llmsproject2026

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # then replace the placeholder values
```

**Django doesn't load `.env` by itself.** Export it into each new shell before running any command that needs those values:

```bash
set -a; source .env; set +a
```

Then create the database, the demo data and an admin account, and start the server:

```bash
python manage.py migrate
python manage.py seed             # needs SEED_PASSWORD
python manage.py createsuperuser  # also asks for a GitHub login
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and log in as `sample-instructor`, using your `SEED_PASSWORD`.

### Demo data

`python manage.py seed` is safe to run again. It updates rows in place and keeps anything already ingested or edited, but it does reset the staff passwords to `SEED_PASSWORD`.

| Created | Details |
|---|---|
| Course and project | `CS 4501, Fall 2026` with `Project 1` and two sample requirements |
| Staff | `sample-instructor` and `sample-ta`, who log in with `SEED_PASSWORD` |
| `Team Live` | This repo (`brooke-brown/llmsproject2026`) with the group's three GitHub logins. Summaries work here if `GITHUB_TOKEN` can read the repo. |
| `Team Sample` | An invented repo and students. **Its repo doesn't exist on GitHub, so Generate summary fails here** with a "couldn't find" message. |

Students get no password, because the alpha has no student pages.

### Which account to use

| Account | Dashboard | `/admin/` |
|---|---|---|
| `sample-instructor`, `sample-ta` | Yes | No |
| A superuser with no enrollment | No: 403 on a course page, and the home page lists no courses | Yes |

The dashboard goes by `Enrollment`, not by Django's staff status. To use one account for both, enroll your superuser in the course as an instructor or TA.

## Environment variables

Documented in [`.env.example`](.env.example). Keep real values in `.env`, which is gitignored.

| Variable | Needed for | Notes |
|---|---|---|
| `GITHUB_TOKEN` | Generate summary | Must be able to read the team's repos, including private ones |
| `LLM_API_KEY` | Generate summary, requirements import | Anthropic API key |
| `LLM_MODEL` | Optional | Defaults to `claude-opus-5-5` |
| `SEED_PASSWORD` | `manage.py seed` | The command refuses to run if it's empty or still `replace-me` |
| `DJANGO_SECRET_KEY` | Deployments | Falls back to an insecure development key when unset |

## Using STRIVE

### Setting up a course

Setup happens in the admin at `/admin/`, in this order:

1. **Users:** add everyone with their `github_login`, which is how GitHub activity is matched to people. Also set a student's email to the address they use in `Co-authored-by:` trailers (see below).
2. **Course**, then an **Enrollment** per person as instructor, TA or student.
3. **Project:** add it to the course.
4. **Requirements (optional):** open the project and click **Import requirements**, then upload a PDF or paste text. The LLM splits the document, and you can edit the results in admin.
5. **Team:** add it to the project, with its students and repos. Enter repos as `https://github.com/owner/repo`.

Good to know:

- **Import works once per project.** Claims refer to requirements by number, so a project that already has requirements is refused. That includes the seeded `Project 1`.
- **The import rejects** scanned PDFs with no text, password-protected PDFs, and any split containing a requirement that isn't word for word in the source.
- **Each student can be on one team per project,** and must be enrolled in the course as a student.
- **Co-author credit matches on email, never on name.** A trailer counts if it uses the student's GitHub noreply address or the email on their `User`. Seeded students have no email, so only noreply trailers match until you add one.
- **Commits that match no one on the team** are listed in admin: under **Artifacts**, use the **Unattributed commits** filter.

### Generating summaries

On a team page, pick a week and click **Generate summary** next to a student.

- The date must be a **Monday** that starts a week that has already ended. It defaults to the most recent completed week.
- Each click runs inside the web request and ingests all of the team's repos, so it can take a while. Each LLM call can take up to 2 minutes, and a summary can make up to 3 of them.
- Generating the same week again replaces that summary's claims.
- A student with no activity gets a "No activity that week" summary, with no LLM call.
- If ingest or the LLM fails, the page says which step failed and any earlier summary stays as it was.

### Pages

| URL | Page |
|---|---|
| `/accounts/login/`, `/accounts/logout/` | Log in and out |
| `/` | Your courses. Goes straight to the course if you only have one. |
| `/courses/<id>/` | Each project's requirements and teams, with flagged teams first, then warned teams |
| `/teams/<id>/` | Repos, flags, students, their summaries, and the Generate summary button |
| `/summaries/<id>/` | Verified claims with GitHub links, and the "couldn't be verified" note |
| `/admin/` | Setup, plus read-only views of artifacts, summaries, claims, validation runs and flags |

Only instructors and TAs of a course can see its pages.

## Validation

**No LLM-generated text reaches a dashboard page without passing the validator.** The validator is plain Python with no LLM calls (`summaries/validator.py`). It uses only the stored GitHub data, and reports every problem with a claim, in a machine-readable form:

| Error | The claim fails when |
|---|---|
| `malformed_id` | A cited ID isn't in the format `owner/repo:type:id` |
| `artifact_not_found` | A cited artifact isn't in one of this team's repos |
| `outside_week` | A cited artifact is dated outside the reporting week |
| `not_contributor` | The student isn't the author, co-author or reviewer of a cited artifact |
| `unknown_requirement` | A requirement number doesn't exist in the project |

Failing claims go back to the LLM with their errors, for up to **2 retries (3 attempts in all)**. Claims that passed are never changed. A claim that still fails is saved as "couldn't verify" with its last errors. The dashboard doesn't show it and only counts it in the note. Admin users can read it for debugging.

Each generation also saves a `ValidationRun` with counts only: total claims, first-pass rate, retries and drop rate. See them in admin.

### Facts and judgments

The artifact checks are **verified facts**. Requirement mapping and significance are **LLM judgments**, and the summary page labels them that way. The validator confirms that a cited requirement exists, not that the work really advances it.

### Claim schema

Defined in [`summaries/schemas.py`](summaries/schemas.py). This is a [shared contract](CLAUDE.md#shared-contracts-do-not-change-without-team-agreement), so changing it needs team agreement.

```json
{
  "text": "Added login form validation and tests.",
  "category": "feature",
  "artifact_ids": ["acme/app:pr:12"],
  "requirement_ids": [1],
  "significance": "moderate"
}
```

- `category` is one of `feature`, `bug fix`, `tests`, `refactor` or `review`.
- `significance` is one of `minor`, `moderate` or `major`, based on what the work accomplishes rather than lines changed.
- `artifact_ids` needs at least one ID. `type` in `owner/repo:type:id` is one of `commit`, `pr`, `review` or `issue`.
- `requirement_ids` holds `Requirement.number`, not the database ID. Empty means the claim isn't tied to a requirement.

## Flags

Flags are recomputed after each summary is saved, and replace the team's earlier flags. There's one rule so far, `low-requirement-share` in [`dashboard/flags.py`](dashboard/flags.py).

Each student's score is the sum of their verified, requirement-tied claims, weighted **minor 1, moderate 2, major 3**. Their share of the team's total is then compared with an equal share:

| Share, compared with an equal share | Result | On a team of 3 |
|---|---|---|
| Below 50% | **Flag** | Below about 17% of the work |
| 50% to below 75% | **Warning** | About 17% to below 25% |
| 75% or more | Nothing | 25% or more |

The rule only looks at the latest week in which **every student on the team has a summary**, so a missing summary never raises a flag. **To see flags, generate the same week for every student on the team.** No flag is raised if a team has fewer than two students or no requirement-tied work that week.

Flags describe observable activity, not character.

## Data model

```
Course                     e.g. "CS 4501, Fall 2026"
 ├── Enrollment            User ↔ Course, with role: instructor | ta | student
 └── Project               has Requirement rows (project, number, text)
      └── Team             exists only inside this project
           ├── TeamMembership   User ↔ Team (students)
           └── Repo             the team's GitHub repo(s)
                └── Artifact    a commit, PR, review or issue

Summary          → Team + User + reporting week
  └── Claim      → the claim schema + status (verified / couldn't verify)
ValidationRun    → Summary: counts for one generation
Flag             → Team, and optionally one student
```

- **Teams belong to a project, not the course,** so a class that reshuffles teams gets new teams per project.
- **Roles live on `Enrollment`, not `User`,** so someone can be a TA in one course and a student in another.
- **`Artifact.artifact_id` is unique per `Repo`, not globally,** so look IDs up within the team: `Artifact.objects.filter(repo__team=team, artifact_id=...)`.

## Project layout

Each app has one owner, and changes outside your own app go through its owner. See [`CLAUDE.md`](CLAUDE.md#apps-and-owners).

| Path | Owner | Contents |
|---|---|---|
| [`core/`](core/) | Todd | Users, courses, enrollments, projects, teams, repos, requirements, PDF import, setup admin, `seed` command |
| [`ingest/`](ingest/) | Todd | `Artifact` model, GitHub ingest, co-author parsing, unattributed commits, sample fixture |
| [`summaries/`](summaries/) | Akshita | `Summary`, `Claim`, `ValidationRun`, LLM client, prompts, claim schema, validator, retry loop |
| [`dashboard/`](dashboard/) | Brooke | Views, templates, `Flag` model and rules, Generate summary button |
| [`strive/`](strive/) | | Django settings and root URLs |
| [`docs/`](docs/) | | The [STRIVE pitch](docs/readme-startupSelected.md) |
| [`directions/`](directions/) | | Archive of the startup ideas we considered and didn't pick. Kept for documentation only. |

Key entry points, which are also shared contracts:

```python
ingest.services.ingest(repo: Repo, week_start: date, week_end: date) -> list[Artifact]
summaries.services.generate_summary(user: User, team: Team, week_start: date) -> Summary
dashboard.flags.compute_flags(team: Team) -> list[Flag]
```

**Stack:** Python 3.12, Django 6.0, SQLite, PyGithub, the Anthropic SDK with Pydantic structured output, pypdf, and server-rendered templates with Pico.css from a CDN. There's no JavaScript framework and no build step.

## Testing

```bash
python manage.py test                 # everything
python manage.py test summaries       # one app
```

The tests don't need API keys or a `.env` file.

To measure coverage:

```bash
coverage run manage.py test
coverage json -q -o coverage.json
```

Read `totals` in `coverage.json`. [`.coveragerc`](.coveragerc) sets what's measured, so don't override it.

`ingest/fixtures/sample_artifacts.json` holds invented data for working in parallel, including a course, teams, students and artifacts. Load it with `python manage.py loaddata sample_artifacts`, into an empty database: it has fixed primary keys, so it can overwrite existing rows.

## Contributing

[`CLAUDE.md`](CLAUDE.md) has the full conventions. The essentials:

- `main` always works. Never commit to it directly. Changes land through small PRs with one teammate's approval.
- Use one short-lived branch per task, named for its owner, such as `todd/ingest-commits`.
- Write tests from the issue's acceptance criteria first. Every validator check and flag rule needs a test.
- Keep views thin and put logic in each app's `services.py`.
- Only make migrations for your own app, and merge them one person at a time.
- Don't change a [shared contract](CLAUDE.md#shared-contracts-do-not-change-without-team-agreement) without team agreement.
- Use `Co-authored-by:` trailers when pairing. Our own repo is test data.
- Add `Closes #N` to a PR that completes an issue, and end every PR description with this section, measured fresh on the branch:

  ```
  ## Test Metrics
  - Tests: <total> total, <passing> passing (<new> new)
  - Line coverage: <percent>% (<covered>/<total> lines)
  - Branch coverage: <percent>% (<covered>/<total>)
  ```

## Known limits of the alpha

- **Local development settings only:** `strive/settings.py` has `DEBUG = True` and an empty `ALLOWED_HOSTS`, so it needs changes before it can be deployed.
- **One shared GitHub token:** every repo is read with `GITHUB_TOKEN`, and there's no GitHub OAuth login yet.
- **Summaries run inside the web request,** because there are no background jobs.
- **Long diffs are cut:** up to 100,000 characters of a diff are stored and at most 4,000 go into the prompt, or fewer in a busy week. The LLM may judge large changes from only part of the diff.
- **Dropped claims aren't queued for review.** The pitch describes a TA review queue, but for now they can only be read in admin.
- **Planned for beta:** a polished per-requirement progress view, checks on numbers in claims against the diff, a student view, disputes, self-reports, consent, CSV import, and "paired with" detection.

There's no chat interface, in the alpha or later. Users only see validated summaries, team views and flags.

## Student data

Contribution summaries are student education records under FERPA.

- Never commit `.env`, `.venv/`, `.idea/`, `__pycache__/` or local database files.
- Add new variables to `.env.example` with placeholder values only.
- Don't log student code or summaries to third-party services, and don't use real classmates' data in tests or fixtures without their permission.
- Commit messages, PR and issue text, and diffs are sent to the LLM API to write summaries, so only connect repos whose owners have agreed to that.

## Team

- Todd Burged
- Akshita Tiwari
- Brooke Brown
