import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from github import Auth, Github

from core.models import Repo, Team, User
from ingest.models import Artifact

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")
MAX_DIFF_CHARS = 100_000
# GitHub's placeholder author for activity from a deleted account.
DELETED_ACCOUNT_LOGIN = "ghost"
# A review that's been started but not submitted.
PENDING_REVIEW_STATE = "PENDING"
# Matched against a whole line, so a mention inside a sentence isn't a trailer.
CO_AUTHOR_TRAILER = re.compile(
    r"co-authored-by:(?P<name>[^<>]*)<(?P<email>[^<>\s@]+@[^<>\s@]+)>\s*", re.IGNORECASE
)
# Both of GitHub's formats: 123+login@users.noreply.github.com and login@users.noreply.github.com.
NOREPLY_EMAIL = re.compile(
    r"(?:\d+\+)?(?P<login>[^@+]+)@users\.noreply\.github\.com", re.IGNORECASE
)


def ingest(repo: Repo, week_start: date, week_end: date) -> list[Artifact]:
    """Collects a repo's commits, PRs, reviews and issues for a week and returns its artifacts.

    The week runs from the start of week_start through the end of week_end, in the
    reporting time zone. Commits come from every branch, and merge commits are skipped.
    Co-authors are matched against the students on the repo's team at the time of the run.
    A PR counts when it merged, or when it was created if unmerged. An issue counts when
    it was created. A review counts when it was submitted, whatever the PR's own date, but
    not if it's pending or by the PR's author. Activity from a deleted GitHub account is
    stored with no author login.
    """
    start = datetime.combine(week_start, time.min, tzinfo=REPORTING_TIME_ZONE)
    end = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=REPORTING_TIME_ZONE)

    github_repo = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"])).get_repo(repo.full_name)
    students = list(
        User.objects.filter(team_memberships__team=repo.team).values_list("github_login", "email")
    )
    for commit in _commits_authored_between(github_repo, start, end):
        _save_commit(repo, commit, students)
    _save_pulls(repo, github_repo, start, end)
    for issue in _issues_created_between(github_repo, start, end):
        _save_issue(repo, issue)

    return list(
        Artifact.objects.filter(repo=repo, timestamp__gte=start, timestamp__lt=end).order_by(
            "timestamp", "pk"
        )
    )


def _commits_authored_between(github_repo, start: datetime, end: datetime) -> list:
    """The non-merge commits on any branch with an author date in [start, end)."""
    # GitHub filters on committer date, which a rebase moves later than the author date.
    # So `until` would lose rebased commits, and author dates are checked here instead.
    # PyGithub labels `since` as UTC without converting it, so it must already be in UTC.
    since = start.astimezone(timezone.utc)
    commits_by_sha = {
        commit.sha: commit
        for branch in github_repo.get_branches()
        for commit in github_repo.get_commits(sha=branch.name, since=since)
    }
    return [
        commit
        for commit in commits_by_sha.values()
        if len(commit.parents) <= 1 and start <= commit.commit.author.date < end
    ]


def _save_commit(repo: Repo, commit, students: list[tuple[str, str]]) -> Artifact:
    git_author = commit.commit.author
    co_author_logins, unmatched_co_authors = _match_co_authors(commit.commit.message, students)
    artifact, _ = Artifact.objects.update_or_create(
        repo=repo,
        artifact_id=f"{repo.full_name}:commit:{commit.sha}",
        defaults={
            "type": Artifact.Type.COMMIT,
            "url": commit.html_url,
            "timestamp": git_author.date,
            "title": commit.commit.message,
            "author_login": commit.author.login if commit.author else "",
            "git_author_name": git_author.name,
            "git_author_email": git_author.email,
            "co_author_logins": co_author_logins,
            "unmatched_co_authors": unmatched_co_authors,
            **_diff_fields(commit.files),
        },
    )
    return artifact


def _save_pulls(repo: Repo, github_repo, start: datetime, end: datetime) -> None:
    """Saves the PRs dated in [start, end), and updates any already stored from another week.

    Updating those moves a PR out of the week it was first stored in once it merges later.
    Every PR is listed, since one merged this week can have been opened long ago.
    """
    stored_ids = set(
        Artifact.objects.filter(repo=repo, type=Artifact.Type.PR).values_list(
            "artifact_id", flat=True
        )
    )
    for pull in github_repo.get_pulls(state="all"):
        artifact_id = f"{repo.full_name}:pr:{pull.number}"
        timestamp = pull.merged_at or pull.created_at
        if start <= timestamp < end or artifact_id in stored_ids:
            Artifact.objects.update_or_create(
                repo=repo,
                artifact_id=artifact_id,
                defaults={
                    "type": Artifact.Type.PR,
                    "timestamp": timestamp,
                    **_pull_or_issue_fields(pull),
                    **_diff_fields(pull.get_files()),
                },
            )
        _save_reviews(repo, pull, start, end)


def _save_reviews(repo: Repo, pull, start: datetime, end: datetime) -> None:
    """Saves a PR's reviews submitted in [start, end), leaving out any by the PR's author."""
    # Deleted accounts all share one login, so two of them aren't known to be one person.
    pull_author = _credited_login(pull.user).lower()
    for review in pull.get_reviews():
        submitted = review.submitted_at
        if review.state == PENDING_REVIEW_STATE or submitted is None:
            continue
        reviewer = _credited_login(review.user)
        if start <= submitted < end and (not reviewer or reviewer.lower() != pull_author):
            Artifact.objects.update_or_create(
                repo=repo,
                artifact_id=f"{repo.full_name}:review:{review.id}",
                defaults={
                    "type": Artifact.Type.REVIEW,
                    "url": review.html_url,
                    "timestamp": submitted,
                    # GitHub sends a null body when the reviewer wrote nothing.
                    "body": review.body or "",
                    "author_login": reviewer,
                    "review_state": review.state,
                },
            )


def _issues_created_between(github_repo, start: datetime, end: datetime) -> list:
    """The issues created in [start, end), leaving out the PRs GitHub also lists as issues."""
    # GitHub filters `since` on the updated date, which is never before the created date.
    # PyGithub labels `since` as UTC without converting it, so it must already be in UTC.
    since = start.astimezone(timezone.utc)
    return [
        issue
        for issue in github_repo.get_issues(state="all", since=since)
        if issue.pull_request is None and start <= issue.created_at < end
    ]


def _save_issue(repo: Repo, issue) -> Artifact:
    artifact, _ = Artifact.objects.update_or_create(
        repo=repo,
        artifact_id=f"{repo.full_name}:issue:{issue.number}",
        defaults={
            "type": Artifact.Type.ISSUE,
            "timestamp": issue.created_at,
            **_pull_or_issue_fields(issue),
        },
    )
    return artifact


def _pull_or_issue_fields(item) -> dict[str, str]:
    return {
        "url": item.html_url,
        "title": item.title,
        # GitHub sends a null body when there's no description.
        "body": item.body or "",
        "author_login": _credited_login(item.user),
    }


def _credited_login(github_user) -> str:
    """The user's login, or an empty string for a deleted account, which earns no credit."""
    if github_user is None or github_user.login == DELETED_ACCOUNT_LOGIN:
        return ""
    return github_user.login


def _match_co_authors(
    message: str, students: list[tuple[str, str]]
) -> tuple[list[str], list[dict[str, str]]]:
    """Splits a commit message's co-authors into student logins and records of everyone else.

    `students` holds (github_login, email) pairs. Only emails are matched, never names.
    A line that isn't a well-formed trailer is skipped, since it names no one to match.
    """
    logins = []
    unmatched = []
    seen_emails = set()
    for line in message.splitlines():
        trailer = CO_AUTHOR_TRAILER.fullmatch(line)
        if trailer is None or trailer["email"].lower() in seen_emails:
            continue
        seen_emails.add(trailer["email"].lower())
        login = _student_login(trailer["email"], students)
        if login is None:
            unmatched.append({"name": trailer["name"].strip(), "email": trailer["email"]})
        elif login not in logins:
            logins.append(login)
    return logins, unmatched


def _student_login(email: str, students: list[tuple[str, str]]) -> str | None:
    """The login of the one student this email identifies, or None."""
    noreply = NOREPLY_EMAIL.fullmatch(email)
    if noreply:
        matches = [login for login, _ in students if login.lower() == noreply["login"].lower()]
    else:
        # User.email isn't unique, so an address two teammates share identifies neither.
        matches = [login for login, address in students if address.lower() == email.lower()]
    return matches[0] if len(matches) == 1 else None


def _diff_fields(files) -> dict:
    # GitHub sends no patch for binary files, so those are listed by name only.
    diff = "".join(
        f"--- a/{file.filename}\n+++ b/{file.filename}\n{file.patch or ''}\n" for file in files
    )
    return {"diff": diff[:MAX_DIFF_CHARS], "diff_truncated": len(diff) > MAX_DIFF_CHARS}


def student_week_artifacts(user: User, team: Team, week_start: date) -> list[Artifact]:
    """The artifacts in a team's repos that credit a student during a reporting week.

    A student is credited as an artifact's author, co-author or reviewer (a review's
    author_login is its reviewer). Logins are compared ignoring case. The week runs for
    seven days from week_start, in the reporting time zone.
    """
    # Built from dates, because a week with a clock change isn't exactly 168 hours long.
    start = datetime.combine(week_start, time.min, tzinfo=REPORTING_TIME_ZONE)
    end = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=REPORTING_TIME_ZONE)
    artifacts = Artifact.objects.filter(
        repo__team=team, timestamp__gte=start, timestamp__lt=end
    ).order_by("timestamp", "pk")

    # Compared in Python, because a database search of the co-author JSON matches
    # substrings: sample-student would also match sample-student-2.
    login = user.github_login.lower()
    return [
        artifact
        for artifact in artifacts
        if artifact.author_login.lower() == login
        or login in (co_author.lower() for co_author in artifact.co_author_logins)
    ]
