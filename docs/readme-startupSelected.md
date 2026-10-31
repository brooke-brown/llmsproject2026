# STRIVE: evidence-based contribution reports for team software projects
 
## The Problem Statement
 
**Instructors can't see who actually did what on a team software project until it's too late to fix.**
 
Today, contribution is judged by peer surveys (like CATME), which rely on memory and are subjective, and by Git stats like commits and lines changed, which are easy to game and do not reflect quality of work, off-repo/collaborative work, etc.
STRIVE produces a per-student contribution summary in which **every claim links to a specific commit, PR, review or issue**, and it flags imbalanced teams while there's still time to step in.
 
## Briefly describe how we know the target customers or have direct access to them.
 
- **We are the students.** As UVA CS students, we've been on team projects and know the free-rider and "who did what" problem from the inside. We can recruit student testers from our own classes and networks.
- **Instructors and TAs are a short walk away.** UVA runs team-project courses such as CS 3240 (Software Engineering), which centers on a semester-long group project. Its instructors and TAs are the buyers and daily users, and we can reach them through office hours, our department, and TAs we already know.
## How we are familiar with (or can quickly learn) most of the required infrastructure.
 
| Component | Our familiarity |
|---|---|
| Git and GitHub (commits, PRs, reviews, issues) | We use these every day in our coursework |
| GitHub REST API, authenticated via OAuth or a per-repo access token | Well-documented; quick to learn |
| Backend in Python and a relational database | Covered in our coursework and side projects; exact web framework/ORM still being decided by the group |
| Web dashboard (login routes to instructor view or student view) | Standard web development |
| LLM API with structured (JSON) output | Learnable in days; there are many examples |
| Requirements/rubric PDF parsing | New to us, but a narrow, well-documented task |
 
Nothing requires specialized hardware, model training or proprietary data. The hardest part is prompt and validation design, which is the interesting part of the project anyway.
 
## How we can reimagine the problem space to make it significantly better.
 
| Today (CATME, peer surveys, git-stats) | STRIVE |
|---|---|
| Measured once or twice a term, from memory | Continuous, from what the team actually did that week |
| Counts commits and lines | Reads each PR's diff and judges what it accomplished — core feature, bug fix, test, refactor, or just a reformatted README — against the project's actual requirements |
| A survey score with no evidence behind it | Every claim links to a commit, PR or issue a TA can click |
| Problems surface at the final peer evaluation | Flags appear weeks earlier, while the team can still recover |
| Students can't see or contest what's said about them | Students will be able to see their own summary and dispute or annotate it (planned, after the instructor view ships) |
 
Work that doesn't show up as commits is handled explicitly instead of being treated as zero:
 
- **Pair programming:** use GitHub's `Co-authored-by:` commit trailers so every author gets credit, plus "paired with…" mentions in PR descriptions.
- **Off-repo work (user interviews, design, coordination):** pull it into the repo as docs and issues where possible. Otherwise, a short weekly self-report that teammates corroborate.
- **Coverage, not just a score:** each summary shows how much of that week's work came from the repo versus self-reports, so a quiet commit history isn't mistaken for no effort.
## How we can make an LLM/Generative model is core to the solution
 
The LLM is the engine of the product, not an add-on. Without it, STRIVE is just another line counter.
 
1. **Requirements-aware classification.** The instructor uploads the project's requirements/rubric (optional) and connects each team's GitHub repo(s). For every PR, the LLM reads the diff and the project requirements together and judges: is this a core feature, a bug fix, a test, a refactor, or just a reformatted README? That tells instructors what each student actually accomplished — not just how much code they touched.
2. **Cross-artifact linking.** It connects an issue to the PR that closed it, the review that shaped that PR, and the follow-up fix. Those threads are the real story of who drove a feature.
3. **Evidence-linked summaries.** It writes a short, readable weekly summary per student, mapping their PRs to requirements and citing every claim.
4. **Interpreting off-repo signals.** It parses co-author trailers, "paired with" notes and self-reports, and matches them against artifacts.
There's no chat interface. Instructors, TAs and students only see the validated summaries, the team view and the flags.

### Demo: here's what STRIVE looks like from the instructor's side

1. The instructor uploads the project requirements and connects a team's GitHub repo(s).
2. The dashboard shows each student's share of meaningful contributions across the team.
3. Clicking a student shows each of their PRs — summarized, labeled, and mapped to a requirement — with the LLM's reasoning.
4. Clicking any claim lands the instructor on the actual PR. Nothing is taken on faith: instead of hours of repo forensics, the instructor gets evidence they can check in seconds and grade with confidence.
 
## What Validation or Guardrail we can introduce so that no LLM-generated artifact reaches the user without passing through validation.
For each claim, code checks the following against the GitHub data we already pulled:
 
- every cited SHA, PR, issue or review ID exists in this team's repo;
- the cited artifact falls inside the reporting week;
- the student is the author, co-author or reviewer of every cited artifact;
- any numbers in the text ("added 12 tests") match the actual diff.

### Self-Correction Loop: Validation errors must automatically feed back into the LLM to retry/correct.
 
Every validator returns **specific, machine-readable errors**, not just pass/fail. The loop sends those errors back to the LLM and regenerates only the claims that failed.
 
```
generate claims (JSON) ──► validate ──► all pass? ──► publish
          ▲                    │ no
          │                    ▼
   retry with error report ◄── build feedback (failing claims + reasons)
          │
   after 3 failed attempts ──► drop claim, mark "couldn't verify", queue for TA review
```
 
1. **Generate.** The LLM produces the week's claims as JSON.
2. **Validate.** The selected validator(s) run on every claim.
3. **Build feedback.** For each failure, write a precise message, for example:
   - A: *"Claim 3 cites commit a1b2c3, which was authored by Jordan, not Maya. Cite only Maya's artifacts or remove the claim."*
   - B: *"Claim 5 says 'implemented login'; the cited diff only changes an error message. Rewrite to match the evidence."*
   - C: *"Claim 2 uses 'lazy'. Describe the observable activity instead."*
   - D: *"Claim 7 rates a 3-line README edit 'major'. Reassess significance using the diff."*
4. **Retry.** Re-prompt with the original evidence, the passing claims (kept, so they don't regress), and the error report. Only the failing claims are regenerated.
5. **Re-validate.** Repeat, up to a cap of **3 attempts**.
6. **Fail safe.** A claim that still fails is **dropped from the user-facing summary**. A note says "1 contribution couldn't be verified automatically," and the claim goes to a TA review queue. Unverified output never reaches a user.
7. **Log everything.** First-pass rate, retries per claim and final drop rate become KPIs. A rising drop rate signals a prompt or model problem.
A sketch in Python:
 
```python
def summarize_week(evidence, max_attempts=3):
    claims = llm_generate(evidence)
    for attempt in range(max_attempts):
        errors = validate(claims, evidence)          # {claim_id: [reasons]}
        if not errors:
            return publish(claims)
        failing = [c for c in claims if c.id in errors]
        fixed = llm_correct(evidence, failing, errors)  # regenerate failures only
        claims = [c for c in claims if c.id not in errors] + fixed
    errors = validate(claims, evidence)
    verified = [c for c in claims if c.id not in errors]
    queue_for_ta([c for c in claims if c.id in errors])
    return publish(verified, unverified_count=len(errors))
```
 
## Deployability: If built, are we able to fully deploy it?
 
**Yes** Every piece is standard and inexpensive:
 
- **App:** a web app, not a GitHub App. The instructor connects each team's repo(s) individually (OAuth or a per-repo access token), and STRIVE pulls commits, PRs, reviews and issues over the GitHub REST/GraphQL API.
- **LLM cost (rough estimate):** a 4-person team might produce ~40 commits a week at ~2,000 tokens of diff each, which is ~80k input tokens per team per week. For 20 teams that's ~1.6M tokens a week, a few dollars at small-model API prices.
- **Rate limits:** a weekly batch over a few dozen repos stays well within GitHub's API limits.
