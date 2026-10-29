# CLAUDE.md

Shared guidance for every group member's Claude agent working in this repo. Keep it short and current: if a decision here changes, update this file in the same PR.

## What we're building

**STRIVE** is a website that gives instructors evidence-based, per-student contribution reports for team software projects (CS4501, Fall 2026). Each week it produces a summary for every student where **every claim links to a specific commit, PR, review or issue**, and it flags imbalanced teams early.

Full product pitch: `directions/readme-startupSelected.md`. Read it before designing any feature.

## How the website works

- **Login page** → the user is routed to the **instructor view** or the **student view** based on their course enrollments.
- **Instructor view (current focus):**
  1. Starts as an empty dashboard with one visually highlighted **Upload project** button.
  2. Uploading a project creates it under a course (the first time, the instructor names the course; afterwards they pick an existing one). Attaching a requirements/rubric PDF is **optional**.
  3. The instructor adds teams under the project and connects each team's GitHub repo(s). This does **not** require the PDF.
  4. Weekly validated summaries and flags appear per team and student.
- **Student view (later):** a student sees their own summary and can dispute or annotate it.
- There is **no chat interface**. Users only see validated summaries, team views and flags.

## Data model 

```
Course                     e.g. "CS 3240, Fall 2026"
 ├── Enrollment            User ↔ Course, with role: instructor | ta | student
 └── Project               optional requirements PDF → Requirement rows
      └── Team             exists only inside this project
           ├── TeamMembership   User ↔ Team (students)
           └── Repo             the team's GitHub repo(s)

WeeklySummary    → Team + User + reporting week
  └── Claim      → text, status (verified / couldn't verify), links to evidence artifacts
```

- **Teams belong to a project, not the course.** Classes that reshuffle teams per project get new teams per project; a semester-long class has one project with one set of teams. Past projects' teams and reports are never edited.
- Reusing teams in a later project is a UI feature ("Copy teams from a previous project"), not a schema change.
- **Roles live on `Enrollment`, not `User`.** One person can be a TA in one course and a student in another.
- **Every user has `User.github_login`.** It's how commits, PRs and reviews are matched to students.
- **Repo is its own table** so a team can have more than one repo.
- Use the word **"Team"** everywhere (code, UI, issues), never "group."
- Later, optional: `joined_at`/`left_at` on `TeamMembership` for students who switch or drop mid-project.

## Build order

Build in this order. Don't start a later step's features in an earlier step's PR.

1. Basic login, with roles from `Enrollment`: GitHub issue #4
2. Empty instructor dashboard + highlighted "Upload project" button (creates course/project; PDF optional)
3. Add teams and connect their repos, ingest GitHub data: #5
4. Weekly evidence-linked summaries (#1) + the validator with its self-correction loop (#2)
5. Requirements PDF parsing + judging contributions against requirements: #7
6. Then: early imbalance flags (#3), student see/dispute (#6), pair-programming credit (#8), off-repo work (#9), student view

## Stack (not yet decided)

The whole app is written in **Python**. The web framework, templating vs. frontend framework, database/ORM, and libraries are **still being decided by the group**. Until this section lists them, don't scaffold the app or add dependencies; ask instead. Once decided, record here: framework, database, ORM, Python version, project layout, and the run/test commands.

## Non-negotiable rules for LLM output

- **No LLM-generated text reaches a user without passing the validator.**
- The validator returns specific, machine-readable errors per claim, not just pass/fail. Those errors feed back to the LLM, which regenerates **only the failing claims**, up to **3 attempts**.
- A claim that still fails is dropped from the user-facing summary, counted as "couldn't verify," and queued for TA review.
- Validator checks, using stored GitHub data: every cited SHA/PR/issue/review ID exists in one of this team's repos; it falls inside the reporting week; the student is its author, co-author or reviewer; any numbers in the text match the actual diff.
- The validator is plain Python with no LLM calls, and must have unit tests.

## Git workflow (GitHub Flow)

- `main` always works. **Never commit directly to `main`**; changes land only through PRs with one teammate's approval.
- One short-lived branch per issue, made from the latest `main`, named with the GitHub issue number: `feature/4-login`, `feature/5-connect-repos`, `fix/12-dashboard-button`.
- PR descriptions include `Closes #N`.
- Keep PRs small and focused on one issue.
- Use `Co-authored-by:` trailers when pairing. STRIVE credits pair programming from them, and our own repo is test data.

## Secrets and student data

- API keys and GitHub tokens live in `.env` (gitignored). Document new variables in `.env.example` with placeholder values only.
- Never commit `.env`, `.venv/`, `.idea/`, `__pycache__/` or local database files.
- Contribution summaries are student education records (FERPA). Don't log student code or summaries to third-party services, and don't use real classmates' data in tests or fixtures without permission.
