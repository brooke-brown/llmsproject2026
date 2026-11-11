# STRIVE

Evidence-based contribution reports for team software projects. Built for **CS 4501** (Fall 2026).

STRIVE pulls a student team's GitHub activity, has an LLM write a weekly summary for each student, checks every claim against the real GitHub data, and flags imbalanced teams for instructors. **Every claim links to a specific commit, PR, review or issue**, so staff can check the evidence themselves.

**Status:** the alpha is built, and the beta is in progress. This README describes what's on `main`, which still reports by week. [Planned for the beta](#planned-for-the-beta) covers what's changing, and [`CLAUDE.md`](CLAUDE.md#beta-scope) has the scope. The reasoning behind the product is in the [STRIVE pitch](docs/readme-startupSelected.md).

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
- [Planned for the beta](#planned-for-the-beta)
- [Known limits of the alpha](#known-limits-of-the-alpha)
- [Student data](#student-data)
- [Team](#team)

## How it works

Clicking **Generate summaries** for a team and week ingests the team's repos once, then writes a summary for each student. For each student, it runs this pipeline:

```
Generate summaries
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
                                     recompute the team's flags, once every student has succeeded
```

Staff then browse **course overview → team → summary**. The overview shows which teams are flagged, and the team page shows which student a flag is about. The summary page shows verified claims with their GitHub links, plus an "N couldn't be verified" note if any were dropped.

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
| `Team Sample` | An invented repo and students. **Its repo doesn't exist on GitHub, so Generate summaries fails here** with a "couldn't find" message. |

Students get no password. To log in as one, set a temporary password on the team page.

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
| `GITHUB_TOKEN` | Generate summaries | Must be able to read the team's repos, including private ones |
| `LLM_API_KEY` | Generate summaries, requirements import | Anthropic API key |
| `LLM_MODEL` | Optional | Defaults to `claude-opus-5-5` |
| `SEED_PASSWORD` | `manage.py seed` | The command refuses to run if it's empty or still `replace-me` |
| `DJANGO_SECRET_KEY` | Deployments | Falls back to an insecure development key when unset |

## Using STRIVE

### Setting up a course

Do these in this order. Steps 2 to 6 are on the dashboard for any instructor enrolled in the course, with no need for `/admin/`. Step 1 happens in the admin.

1. **Course, instructors and TAs (admin):** add the course, then a user with a `github_login` and an Enrollment for each instructor and TA.
2. **Project (dashboard):** on the course overview, click **New project** and enter a name, which opens the project's edit page for its requirements and teams. Click **Edit project** on the overview to return to that page, where **edit** beside the name changes it. Names must be unique within a course, ignoring case.
3. **Sprints and their requirements (dashboard):** on the project's edit page, click **New sprint** and enter a name, a start date and an end date. The dates default to seven days from today, or from the latest sprint's last day, and can be any range starting on any day. A sprint can start on the day the previous one ends, but two sprints can't start on the same day. Open a sprint to give it requirements: upload a PDF or paste text and the LLM splits it, or, while the sprint has none, **Copy requirements from an earlier sprint** to keep their numbers. You can then edit, add and delete requirements on the sprint's page. **edit** beside a sprint's name renames it, and changes its dates until it has summaries. Deleting a sprint deletes its requirements, summaries and flags. (The project-level requirements page stays until T-21 removes it.)
4. **Team (dashboard):** on the project's edit page or the course overview, enter a name and click **Add team**. Open the team to rename it, with **edit** beside its name, or delete it. Names must be unique within a project, ignoring case.
5. **Students (dashboard):** on the team page, enter each student's full name and GitHub username, plus the email they use in `Co-authored-by:` trailers if you have it (see below). This creates their account and course enrollment if they don't have them. New accounts can't log in until you give them a temporary password (see [Student logins](#student-logins)).
6. **Repo (dashboard):** on the team page, enter the team's repo URL, such as `https://github.com/owner/repo`. A team has one repo: to use another, click **change**, which asks you to confirm. Once activity has been pulled from a repo, it can't be changed or removed. STRIVE checks that it can read the repo, using `GITHUB_TOKEN` rather than your own GitHub access, so the account behind that token needs access to private repos.

Good to know:

- **Import only works while a project has no requirements.** Claims refer to requirements by number, so a project that already has requirements is refused. That includes the seeded `Project 1`.
- **The import rejects** scanned PDFs with no text, password-protected PDFs, and any split containing a requirement that isn't word for word in the source.
- **Requirement numbers don't change.** You can edit a requirement's text but not its number, and deleting one never renumbers the others. A new requirement takes the next number after the project's highest, so a gap stays a gap. If you delete the highest-numbered requirement, the next one you add reuses its number.
- **A requirement a claim cites can't be deleted** on the dashboard, including one cited only by a claim that couldn't be verified. The admin doesn't enforce this, so don't delete requirements there.
- **Each student can be on one team per project,** and must be enrolled in the course as a student. A course's instructors and TAs can't be added to a team.
- **An existing account is reused as it is.** A GitHub username that already has an account, ignoring case, keeps its name and email.
- **On the team page, only a student's name and temporary password can be edited.** Their GitHub username shows below their name, and it and their email change in the admin. An account that's an instructor or TA in another course, or a staff account, can only be edited in the admin.
- **Deleting is permanent, and each confirmation says what goes with it.** Deleting a project or team also deletes its summaries, and removing a student deletes their summaries on that team, and the team's flags are recomputed. Accounts and enrollments stay.
- **Two things can't be deleted while claims depend on them.** A repo whose activity a claim cites, because the claim would lose its GitHub link, and a requirement a claim cites, because its number could be reused.
- **Co-author credit matches on email, never on name.** A trailer counts if it uses the student's GitHub noreply address or the email on their `User`. Seeded students have no email, so only noreply trailers match until you add one.
- **Commits that match no one on the team** are listed in admin: under **Artifacts**, use the **Unattributed commits** filter.

### Student logins

An instructor gives each student a temporary password, and shares it with them directly, such as in class. STRIVE sends no email.

1. **Set it (dashboard):** on the team page, fill in **Temporary password** when adding a student, or click **edit** beside a student. Leaving the field blank keeps their current password. Only the course's instructors can do this.
2. **Log in:** the student logs in with their GitHub username and the temporary password. An older account can have a different username. If so, the message after saving and the edit form name the one to share.
3. **Choose a new one:** until they do, every page except logout sends them back to the password change page. They then land on the student home.

Good to know:

- **A password that breaks a password rule is refused,** with a message naming the rule, and nothing else on the form is saved.
- **Spaces and line breaks around a password are dropped,** so a pasted one still works. A password of only spaces is refused.
- **The password is never shown again.** The edit form says only whether the student has logged in and set their own password.
- **Setting a new one replaces the student's own password,** and they must change it again at their next login. This is how to help a student who is locked out, since there's no password reset by email.
- **Staff accounts and anyone who's an instructor or TA in any course** can only be given a password in the admin. Typing a password for one on the add form refuses the whole add.
- **A student also enrolled in a course you don't teach** can only be given a password in the admin, so one course's instructor can't take over an account another course relies on. This applies on both forms, and adding them with the field blank still works.
- **An existing account with a password and no enrollments** can't be added from the team page, with or without a temporary password, since it could be anyone's. Enroll it in the course in the admin first.
- **A password set in the admin** must be changed at the next login too, because whoever set it knows it.

### Generating summaries

On the course overview, pick a sprint for a team and click **Generate summaries**.

- **The menu lists the project's started sprints,** such as "Sprint 2 (Oct 30 – Nov 5)". Sprints that haven't started aren't offered, and a project with none shows a message instead. It defaults to the current sprint, or on a day shared by two sprints the one that just started, or else the most recently ended. Generating ingests each repo from the sprint's first day through today (or its last day once it has ended), then writes one summary per student. A sprint still running is labelled **in progress**, with the date it covers through, and generating it again after it ends covers all of it. Flags per sprint are not raised yet: that is the next beta issue (#123).
- **Retrying works by sprint.** A failed student is retried for the same sprint, without another ingest.

The notes below describe the weekly flow, which still works through the same button's endpoint until T-21 (#125) removes it, so they apply to sprints except where they mention weeks and Mondays.


- The date must be a **Monday**, for this week or an earlier one. Other days can't be picked in browsers that honour the date field's `step`, and are refused either way. It defaults to the most recent completed week.
- **You can generate the current week to check on a team mid-week.** Its summaries are labelled **in progress**. The team page's contribution shares include them and say so, but flags don't count them. After the week ends they're labelled **partial** and flags still skip them until you generate that week again, so a Wednesday snapshot never affects a flag.
- **Only one run per team at a time.** While a team is generating, its button shows a spinner on every fresh load of the overview, a second run is refused, and the page reloads itself when the run finishes. While a run is going, the course overview and that team's page show **Starting** until the first student is done, then **N of M students done** with a progress bar, and name any student whose summary has failed so far with the reason. The overview updates the progress in place every 5 seconds, so text typed into a form on it is kept until the run ends and the page reloads. The team page reloads itself every 5 seconds until the run ends. You can leave and come back: the run keeps going, and its result message appears the next time you open the course overview.
- **A stuck run clears by itself.** If the server dies mid-run, the team is blocked until a time limit of 10 minutes plus 6 per student, which the refusal message shows. To clear it sooner, delete it under **Generation runs** in the admin.
- Each click runs inside the web request, ingesting all of the team's repos once and then writing each student's summary in turn, so it can take a while. Each LLM call can take up to 2 minutes, and a summary can make up to 3 of them.
- Generating the same week again regenerates the whole team's summaries, using fresh GitHub data.
- A student with no activity gets a "No activity that week" summary, with no LLM call.
- If ingest fails, the message names the repo, nothing changes and no LLM calls are made.
- If a student's summary fails, the other students are still processed, and the failed student keeps any earlier summary for the week. The message names who failed, and the team gets a **Retry failed summaries** button that writes only those summaries again, without another ingest. This is separate from the automatic retries of claims that fail the validator.

### Pages

| URL | Page |
|---|---|
| `/accounts/login/`, `/accounts/logout/` | Log in and out |
| `/` | Your courses. Goes straight to the course if you only have one. |
| `/courses/<id>/` | Each project's requirements and teams, with flagged teams first, then warned teams, then teams with an open dispute, a Flag, Warning or Dispute open label, and the Generate summaries button. Instructors also get New project, and Edit project and requirements links for each project. |
| `/teams/<id>/` | Repos, flags with their reasons and any disputes, and students, whose names link to their newest summary, with all their summaries. Instructors can keep or withdraw a disputed flag. |
| `/summaries/<id>/` | Verified claims with GitHub links, and the "couldn't be verified" note |
| `/projects/<id>/requirements/` | Import, edit, add and delete a project's requirements. Instructors only. |
| `/admin/` | Users, courses, enrollments, teams and repos, plus read-only views of artifacts, summaries, claims, validation runs and flags |

Only instructors and TAs of a course can see its pages. Only instructors can create or rename projects and change requirements: TAs don't see these actions and get a 403.

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

Flags are recomputed once every student's summary has succeeded, and replace the team's earlier flags. After a partial failure they stay as they were until a retry or a full run succeeds. There's one rule so far, `low-requirement-share` in [`dashboard/flags.py`](dashboard/flags.py).

Each student's score is the sum of their verified, requirement-tied claims, weighted **minor 1, moderate 2, major 3**. Their share of the team's total is then compared with an equal share:

| Share, compared with an equal share | Result | On a team of 3 |
|---|---|---|
| Below 50% | **Flag** | Below about 17% of the work |
| 50% to below 75% | **Warning** | About 17% to below 25% |
| 75% or more | Nothing | 25% or more |

The rule only looks at the latest week in which **every student on the team has a summary**, so a missing summary never raises a flag. No flag is raised if a team has fewer than two students or no requirement-tied work that week.

Flags describe observable activity, not character.

### Disputes

A student can dispute a flag or warning about them with a written explanation. A dispute is matched to its flag by team, student, rule and week, so it carries over when flags are recomputed, even if the flag's wording or severity changes.

- The course overview labels a team with an open dispute **Dispute open**, and lists those teams right after flagged and warned teams.
- The team page shows each dispute under its flag, with the student's explanation. If a later run no longer raises the flag, the dispute stays, marked **Flag no longer raised**.
- An instructor can **Keep flag** or **Withdraw flag**, with an optional note to the student, and can change the decision later. TAs can read disputes but not decide them.
- A withdrawn flag shows as **Withdrawn after dispute** without its reason. It no longer makes the team flagged or colours the pie chart, and stays withdrawn after flags are recomputed. Contribution shares don't change.

## Data model

```
Course                     e.g. "CS 4501, Fall 2026"
 ├── Enrollment            User ↔ Course, with role: instructor | ta | student
 └── Project               has Requirement rows (project, number, text)
      └── Team             exists only inside this project
           ├── TeamMembership   User ↔ Team (students)
           └── Repo             the team's GitHub repo
                └── Artifact    a commit, PR, review or issue

Summary          → Team + User + reporting week
  └── Claim      → the claim schema + status (verified / couldn't verify)
ValidationRun    → Summary: counts for one generation
Flag             → Team, and optionally one student
FlagDispute      → Team + student: finds its flag by rule and week, so it survives flags being recomputed
GenerationRun    → Team: exists only while its summaries are generating
```

- **Teams belong to a project, not the course,** so a class that reshuffles teams gets new teams per project.
- **Roles live on `Enrollment`, not `User`,** so someone can be a TA in one course and a student in another.
- **`Artifact.artifact_id` is unique per `Repo`, not globally,** so look IDs up within the team: `Artifact.objects.filter(repo__team=team, artifact_id=...)`.

## Project layout

| Path | Contents |
|---|---|
| [`core/`](core/) | Users, courses, enrollments, projects, teams, repos, requirements, PDF import, project, requirement, team, student and repo setup logic, setup admin, `seed` command |
| [`ingest/`](ingest/) | `Artifact` model, GitHub ingest, co-author parsing, unattributed commits, sample fixture |
| [`summaries/`](summaries/) | `Summary`, `Claim`, `ValidationRun`, LLM client, prompts, claim schema, validator, retry loop |
| [`dashboard/`](dashboard/) | Views, templates, `Flag` and `FlagDispute` models, flag rules, Generate summaries button, instructor setup pages |
| [`strive/`](strive/) | Django settings and root URLs |
| [`docs/`](docs/) | The [STRIVE pitch](docs/readme-startupSelected.md) |
| [`directions/`](directions/) | Archive of the startup ideas we considered and didn't pick. Kept for documentation only. |

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
- Use one short-lived branch per task, starting with your name, such as `yourname/ingest-commits`.
- Write tests from the issue's acceptance criteria first. Every validator check and flag rule needs a test.
- Keep views thin and put logic in each app's `services.py`.
- Never edit a migration that's already merged, and merge new ones one person at a time.
- Don't change a [shared contract](CLAUDE.md#shared-contracts-do-not-change-without-team-agreement) without team agreement.
- Use `Co-authored-by:` trailers when pairing. Our own repo is test data.
- Add `Closes #N` to a PR that completes an issue, and end every PR description with this section, measured fresh on the branch:

  ```
  ## Test Metrics
  - Tests: <total> total, <passing> passing (<new> new)
  - Line coverage: <percent>% (<covered>/<total> lines)
  - Branch coverage: <percent>% (<covered>/<total>)
  ```

## Planned for the beta

None of this is built yet, except the `FlagDispute` model and [student logins](#student-logins). Each item is tracked as a GitHub issue.

- **Sprints (#118–#125):** a project has zero or more sprints, each with its own dates and requirements, and sprints can start on any day and run any length.
- **One report per sprint:** each student gets one summary per sprint. Before the sprint ends, it covers the sprint's first day through today. Ranges over 10 days are processed in weekly chunks and merged into that one report, which goes through the validator again.
- **Flags per sprint:** flags are based on the whole sprint and only raised once it has ended. A running sprint shows shares but raises no flags.
- **Setup at scale:** CSV import of teams and students (#126), and generating temporary passwords in bulk (#127).
- **Student side:** a student home, flag disputes, offline contributions and peer evaluations (#104, #105 and #107–#115).

## Known limits of the alpha

- **Local development settings only:** `strive/settings.py` has `DEBUG = True` and an empty `ALLOWED_HOSTS`, so it needs changes before it can be deployed.
- **One shared GitHub token:** every repo is read with `GITHUB_TOKEN`, and there's no GitHub OAuth login yet.
- **Summaries run inside the web request,** because there are no background jobs.
- **Long diffs are cut:** up to 100,000 characters of a diff are stored and at most 4,000 go into the prompt, or fewer in a busy week. The LLM may judge large changes from only part of the diff.
- **Dropped claims aren't queued for review.** The pitch describes a TA review queue, but for now they can only be read in admin.
- **Later, with no issues yet:** a polished per-requirement progress view, checks on numbers in claims against the diff, GitHub OAuth login, consent, background jobs, and "paired with" detection.

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
