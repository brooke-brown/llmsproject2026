"""Tests for US-2 (#18): collecting a repo's commits for a reporting week.

PyGithub's client is replaced by fakes shaped like the objects it returns, so nothing
here touches the network. `ingest` is imported per test rather than at module level, so
each criterion passes or fails on its own.

The fake filters `since` and `until` on the committer date, as GitHub does, while the
reporting week uses the author date. Those differ after a rebase.
"""

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone as django_timezone

from core.models import Repo
from ingest import services
from ingest.models import Artifact
from ingest.tests.test_student_artifacts import (
    NEW_YORK,
    ONE_MICROSECOND,
    add_repo,
    make_project,
    make_team,
)

FULL_NAME = "sample-org/app"
STUDENT = "sample-student"
WEEK_START = date(2026, 10, 19)  # A Monday, with no clock change that week.
WEEK_END = date(2026, 10, 25)  # The Sunday that ends it.
MID_WEEK = datetime(2026, 10, 21, 12, 0, tzinfo=NEW_YORK)
FIRST_MOMENT = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)  # Midnight in New York (EDT).
NEXT_WEEK = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)


def sha(character):
    return character * 40


def fake_file(filename="app/models.py", patch="@@ -0,0 +1 @@\n+class Task: pass"):
    return SimpleNamespace(filename=filename, patch=patch)


def fake_commit(
    commit_sha,
    message="Add the task model",
    authored=MID_WEEK,
    committed=None,
    login=STUDENT,
    parents=1,
    files=None,
):
    """A commit as PyGithub lists it. `commit.author` is None if no account is linked."""
    return SimpleNamespace(
        sha=commit_sha,
        html_url=f"https://github.com/{FULL_NAME}/commit/{commit_sha}",
        parents=[SimpleNamespace(sha=sha(str(number))) for number in range(parents)],
        author=SimpleNamespace(login=login) if login else None,
        files=[fake_file()] if files is None else files,
        commit=SimpleNamespace(
            message=message,
            author=SimpleNamespace(
                name="Sample Student", email="student@example.com", date=authored
            ),
            committer=SimpleNamespace(
                name="Sample Student", email="student@example.com", date=committed or authored
            ),
        ),
    )


def as_utc(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


class FakeGitHubRepo:
    """Takes {branch name: commits, newest first}. The first branch is the default.

    As on GitHub, listing PRs or issues returns only open ones unless asked otherwise,
    `since` filters issues on their updated date, and the issues list includes every PR.
    """

    def __init__(self, branches, pulls=(), issues=()):
        self.branches = branches
        self.pulls = pulls
        self.issues = issues

    def get_branches(self):
        return iter(
            SimpleNamespace(
                name=name, commit=SimpleNamespace(sha=commits[0].sha if commits else None)
            )
            for name, commits in self.branches.items()
        )

    def get_commits(self, sha=None, since=None, until=None):
        return iter(
            commit
            for commit in self._commits_on(sha)
            if (since is None or commit.commit.committer.date >= as_utc(since))
            and (until is None or commit.commit.committer.date <= as_utc(until))
        )

    def _commits_on(self, ref):
        if ref is None:
            return next(iter(self.branches.values()), [])
        for name, commits in self.branches.items():
            if ref == name or (commits and ref == commits[0].sha):
                return commits
        raise AssertionError(f"Asked for commits on an unknown ref: {ref}")

    def get_pulls(self, state="open", sort="created", direction="desc"):
        return iter(self._listed(self.pulls, state, sort, direction))

    def get_issues(self, state="open", sort="created", direction="desc", since=None):
        issues_and_pulls = [*self.issues, *(pull.as_issue for pull in self.pulls)]
        return iter(
            issue
            for issue in self._listed(issues_and_pulls, state, sort, direction)
            if since is None or issue.updated_at >= as_utc(since)
        )

    @staticmethod
    def _listed(items, state, sort, direction):
        return sorted(
            (item for item in items if state in ("all", item.state)),
            key=lambda item: getattr(item, f"{sort}_at"),
            reverse=direction == "desc",
        )


class IngestTestCase(TestCase):
    def setUp(self):
        self.project = make_project()
        self.team = make_team(self.project)
        self.repo = add_repo(self.team, FULL_NAME)

    def ingest(
        self, branches, repo=None, week_start=WEEK_START, week_end=WEEK_END, pulls=(), issues=()
    ):
        from ingest.services import ingest

        repo = repo or self.repo
        # A lookup by any name other than the Repo's full_name raises a KeyError.
        repos_on_github = {repo.full_name: FakeGitHubRepo(branches, pulls, issues)}
        with (
            mock.patch("ingest.services.Github") as github,
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
        ):
            github.return_value.get_repo.side_effect = (
                lambda full_name, **kwargs: repos_on_github[full_name]
            )
            return ingest(repo, week_start, week_end)

    def ingest_one(self, commit):
        """Ingests a single commit and returns its artifact as saved in the database."""
        artifacts = self.ingest({"main": [commit]})
        self.assertEqual(len(artifacts), 1)
        return Artifact.objects.get(pk=artifacts[0].pk)

    def stored_artifact(self, number, repo=None, timestamp=MID_WEEK, type_="pr"):
        repo = repo or self.repo
        return Artifact.objects.create(
            repo=repo,
            artifact_id=f"{repo.full_name}:{type_}:{number}",
            type=type_,
            url=f"https://github.com/{repo.full_name}/pull/{number}",
            timestamp=timestamp,
        )

    def assert_collected(self, artifacts, *expected_shas):
        # assertCountEqual also catches a commit that's returned twice.
        self.assertCountEqual(
            [artifact.artifact_id for artifact in artifacts],
            [f"{FULL_NAME}:commit:{commit_sha}" for commit_sha in expected_shas],
        )


class EveryBranchTests(IngestTestCase):
    def test_collects_commits_from_every_branch(self):
        artifacts = self.ingest(
            {
                "main": [fake_commit(sha("a"))],
                "feature/tasks": [fake_commit(sha("b"))],
                "fix/login": [fake_commit(sha("c"))],
            }
        )
        self.assert_collected(artifacts, sha("a"), sha("b"), sha("c"))

    def test_collects_unmerged_work_when_the_default_branch_had_none_that_week(self):
        artifacts = self.ingest({"main": [], "feature/tasks": [fake_commit(sha("b"))]})
        self.assert_collected(artifacts, sha("b"))

    def test_saves_the_collected_commits(self):
        self.ingest({"main": [fake_commit(sha("a"))], "feature/tasks": [fake_commit(sha("b"))]})
        self.assert_collected(Artifact.objects.filter(repo=self.repo), sha("a"), sha("b"))

    def test_returns_a_list_of_artifacts(self):
        artifacts = self.ingest({"main": [fake_commit(sha("a"))]})
        self.assertIsInstance(artifacts, list)
        self.assertIsInstance(artifacts[0], Artifact)

    def test_a_repo_with_no_branches_returns_nothing(self):
        self.assertEqual(self.ingest({}), [])

    def test_reads_the_repo_named_by_full_name_not_its_url(self):
        # Only repo setup reads Repo.url. ingest's fake GitHub only knows the full_name.
        repo = add_repo(make_team(self.project, "Team B"), "sample-org/renamed")
        Repo.objects.filter(pk=repo.pk).update(url="https://github.com/sample-org/old-name")
        repo.refresh_from_db()
        self.assertEqual(self.ingest({"main": []}, repo=repo), [])


class CommitOnTwoBranchesTests(IngestTestCase):
    def test_a_commit_on_two_branches_counts_once(self):
        shared = fake_commit(sha("a"))
        artifacts = self.ingest({"main": [shared], "feature/tasks": [shared]})
        self.assert_collected(artifacts, sha("a"))
        self.assertEqual(Artifact.objects.filter(repo=self.repo).count(), 1)

    def test_shared_history_counts_once_while_each_branchs_own_commit_is_kept(self):
        shared = fake_commit(sha("a"))
        artifacts = self.ingest(
            {
                "main": [fake_commit(sha("b")), shared],
                "feature/tasks": [fake_commit(sha("c")), shared],
            }
        )
        self.assert_collected(artifacts, sha("a"), sha("b"), sha("c"))


class MergeCommitTests(IngestTestCase):
    def test_a_merge_commit_is_not_collected_or_saved(self):
        merge = fake_commit(sha("m"), message="Merge pull request #1", parents=2)
        artifacts = self.ingest({"main": [merge, fake_commit(sha("a"))]})
        self.assert_collected(artifacts, sha("a"))
        self.assert_collected(Artifact.objects.filter(repo=self.repo), sha("a"))

    def test_a_merge_with_more_than_two_parents_is_not_collected(self):
        merge = fake_commit(sha("m"), parents=3)
        artifacts = self.ingest({"main": [merge, fake_commit(sha("a"))]})
        self.assert_collected(artifacts, sha("a"))

    def test_merging_a_branch_keeps_its_commit_once_and_drops_the_merge(self):
        work = fake_commit(sha("a"))
        merge = fake_commit(sha("m"), parents=2)
        artifacts = self.ingest({"main": [merge, work], "feature/tasks": [work]})
        self.assert_collected(artifacts, sha("a"))

    def test_merge_commits_are_found_by_parents_not_by_message(self):
        # A squash merge has one parent and holds real work, whatever its message says.
        squash = fake_commit(sha("a"), message="Merge branch 'feature/tasks'")
        self.assert_collected(self.ingest({"main": [squash]}), sha("a"))

    def test_a_first_commit_with_no_parents_is_collected(self):
        first = fake_commit(sha("a"), message="Initial commit", parents=0)
        self.assert_collected(self.ingest({"main": [first]}), sha("a"))


class GitHubLinkTests(IngestTestCase):
    def test_each_commit_links_to_github(self):
        artifact = self.ingest_one(fake_commit(sha("a")))
        self.assertEqual(artifact.url, f"https://github.com/{FULL_NAME}/commit/{sha('a')}")

    def test_each_commit_has_a_valid_artifact_id_built_from_full_name(self):
        artifact = self.ingest_one(fake_commit(sha("a")))
        self.assertEqual(artifact.type, Artifact.Type.COMMIT)
        self.assertEqual(artifact.artifact_id, f"{FULL_NAME}:commit:{sha('a')}")
        artifact.full_clean()


class StoredCommitTests(IngestTestCase):
    def test_stores_the_whole_commit_message(self):
        message = "Add the task model\n\nIt holds a title for now."
        artifact = self.ingest_one(fake_commit(sha("a"), message=message))
        self.assertEqual(artifact.title, message)

    def test_stores_the_author_login_and_git_identity(self):
        artifact = self.ingest_one(fake_commit(sha("a")))
        self.assertEqual(artifact.author_login, STUDENT)
        self.assertEqual(artifact.git_author_name, "Sample Student")
        self.assertEqual(artifact.git_author_email, "student@example.com")

    def test_collects_a_commit_with_no_linked_github_account(self):
        artifact = self.ingest_one(fake_commit(sha("a"), login=None))
        self.assertEqual(artifact.author_login, "")
        self.assertEqual(artifact.git_author_email, "student@example.com")

    def test_timestamp_is_the_author_date_not_the_committer_date(self):
        committed = datetime(2026, 10, 22, 12, 0, tzinfo=NEW_YORK)
        artifact = self.ingest_one(fake_commit(sha("a"), committed=committed))
        self.assertEqual(artifact.timestamp, MID_WEEK)


class DiffTests(IngestTestCase):
    def test_stores_each_changed_files_name_and_changes(self):
        files = [
            fake_file("app/models.py", "@@ -0,0 +1 @@\n+class Task: pass"),
            fake_file("app/views.py", "@@ -1 +1 @@\n-old_view\n+new_view"),
        ]
        artifact = self.ingest_one(fake_commit(sha("a"), files=files))
        for file in files:
            self.assertIn(file.filename, artifact.diff)
            self.assertIn(file.patch, artifact.diff)
        self.assertFalse(artifact.diff_truncated)

    def test_a_binary_file_with_no_patch_is_named_without_failing(self):
        # GitHub sends no patch for binary files.
        files = [fake_file("app/logo.png", None), fake_file("app/views.py", "+new_view")]
        artifact = self.ingest_one(fake_commit(sha("a"), files=files))
        self.assertIn("app/logo.png", artifact.diff)
        self.assertNotIn("None", artifact.diff)
        self.assertIn("+new_view", artifact.diff)

    def test_a_very_large_diff_is_cut_to_the_limit_and_marked_as_truncated(self):
        patch = "+" * (services.MAX_DIFF_CHARS + 1)
        artifact = self.ingest_one(fake_commit(sha("a"), files=[fake_file(patch=patch)]))
        self.assertEqual(len(artifact.diff), services.MAX_DIFF_CHARS)
        self.assertTrue(artifact.diff_truncated)

    def test_truncation_keeps_the_start_of_the_diff(self):
        commit = fake_commit(sha("a"), files=[fake_file(patch="+" + "x" * 500)])
        whole = self.ingest_one(commit).diff
        Artifact.objects.all().delete()
        with mock.patch("ingest.services.MAX_DIFF_CHARS", 100):
            artifact = self.ingest_one(commit)
        self.assertEqual(artifact.diff, whole[:100])

    def test_a_diff_exactly_at_the_limit_is_not_marked_as_truncated(self):
        commit = fake_commit(sha("a"))
        whole = self.ingest_one(commit).diff
        Artifact.objects.all().delete()
        with mock.patch("ingest.services.MAX_DIFF_CHARS", len(whole)):
            artifact = self.ingest_one(commit)
        self.assertEqual(artifact.diff, whole)
        self.assertFalse(artifact.diff_truncated)

    def test_a_diff_one_character_over_the_limit_is_marked_as_truncated(self):
        commit = fake_commit(sha("a"))
        whole = self.ingest_one(commit).diff
        Artifact.objects.all().delete()
        with mock.patch("ingest.services.MAX_DIFF_CHARS", len(whole) - 1):
            artifact = self.ingest_one(commit)
        self.assertEqual(artifact.diff, whole[:-1])
        self.assertTrue(artifact.diff_truncated)


class ReportingWeekTests(IngestTestCase):
    def assert_week_boundaries(self, week_start, week_end, first_moment, next_week):
        artifacts = self.ingest(
            {
                "main": [
                    fake_commit(sha("a"), authored=next_week),
                    fake_commit(sha("b"), authored=next_week - ONE_MICROSECOND),
                    fake_commit(sha("c"), authored=first_moment),
                    fake_commit(sha("d"), authored=first_moment - ONE_MICROSECOND),
                ]
            },
            week_start=week_start,
            week_end=week_end,
        )
        self.assert_collected(artifacts, sha("b"), sha("c"))

    def test_includes_the_first_and_last_moments_of_the_week(self):
        self.assert_week_boundaries(WEEK_START, WEEK_END, FIRST_MOMENT, NEXT_WEEK)

    def test_boundaries_hold_when_the_clocks_go_back_during_the_week(self):
        # Clocks go back on Sunday, Nov 1, 2026, so this week is 169 hours long.
        self.assert_week_boundaries(
            date(2026, 10, 26),
            date(2026, 11, 1),
            datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc),  # Midnight in EDT.
            datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc),  # Midnight in EST.
        )

    def test_boundaries_hold_when_the_clocks_go_forward_during_the_week(self):
        # Clocks go forward on Sunday, Mar 14, 2027, so this week is 167 hours long.
        self.assert_week_boundaries(
            date(2027, 3, 8),
            date(2027, 3, 14),
            datetime(2027, 3, 8, 5, 0, tzinfo=timezone.utc),  # Midnight in EST.
            datetime(2027, 3, 15, 4, 0, tzinfo=timezone.utc),  # Midnight in EDT.
        )

    def test_week_stays_in_new_york_time_whatever_time_zone_is_active(self):
        with django_timezone.override(ZoneInfo("Asia/Tokyo")):
            self.assert_week_boundaries(WEEK_START, WEEK_END, FIRST_MOMENT, NEXT_WEEK)

    def test_collects_a_commit_authored_this_week_but_rebased_after_it(self):
        # GitHub filters on committer date, so asking it to stop at the week's end misses this.
        rebased = fake_commit(sha("a"), committed=NEXT_WEEK + timedelta(days=1))
        self.assert_collected(self.ingest({"main": [rebased]}), sha("a"))

    def test_skips_a_commit_authored_last_week_but_rebased_into_this_one(self):
        rebased = fake_commit(sha("a"), authored=FIRST_MOMENT - ONE_MICROSECOND, committed=MID_WEEK)
        self.assertEqual(self.ingest({"main": [rebased]}), [])
        self.assertFalse(Artifact.objects.filter(repo=self.repo).exists())


class ReturnedArtifactsTests(IngestTestCase):
    def test_a_second_run_returns_the_commits_the_first_run_saved(self):
        branches = {"main": [fake_commit(sha("a"))]}
        first = self.ingest(branches)
        second = self.ingest(branches)
        self.assertEqual([artifact.pk for artifact in second], [first[0].pk])
        self.assertEqual(Artifact.objects.filter(repo=self.repo).count(), 1)

    def test_returns_old_commits_alongside_new_ones(self):
        self.ingest({"main": [fake_commit(sha("a"))]})
        artifacts = self.ingest({"main": [fake_commit(sha("b")), fake_commit(sha("a"))]})
        self.assert_collected(artifacts, sha("a"), sha("b"))

    def test_returns_every_type_of_artifact_already_stored_for_the_week(self):
        stored = [
            self.stored_artifact(1, timestamp=FIRST_MOMENT),
            self.stored_artifact(2, timestamp=NEXT_WEEK - ONE_MICROSECOND, type_="issue"),
            self.stored_artifact(3, type_="review"),
        ]
        artifacts = self.ingest({"main": [fake_commit(sha("a"))]})
        commit = Artifact.objects.get(artifact_id=f"{FULL_NAME}:commit:{sha('a')}")
        self.assertCountEqual(artifacts, [*stored, commit])

    def test_leaves_out_stored_artifacts_from_other_weeks(self):
        self.stored_artifact(1, timestamp=FIRST_MOMENT - ONE_MICROSECOND)
        self.stored_artifact(2, timestamp=NEXT_WEEK)
        self.assert_collected(self.ingest({"main": [fake_commit(sha("a"))]}), sha("a"))
        self.assertEqual(Artifact.objects.filter(repo=self.repo).count(), 3)

    def test_leaves_out_artifacts_from_the_teams_other_repos(self):
        self.stored_artifact(1, repo=add_repo(self.team, "sample-org/docs"))
        self.assert_collected(self.ingest({"main": [fake_commit(sha("a"))]}), sha("a"))

    def test_a_later_project_reusing_the_repo_gets_its_own_copy(self):
        # artifact_id is unique per Repo, so the first project's row must not be reused.
        later_repo = add_repo(make_team(make_project(self.project.course, "Project 2")), FULL_NAME)
        branches = {"main": [fake_commit(sha("a"))]}
        original = self.ingest(branches)[0]
        copy = self.ingest(branches, repo=later_repo)[0]
        self.assertNotEqual(copy.pk, original.pk)
        self.assertEqual(copy.artifact_id, original.artifact_id)
        self.assertEqual(copy.repo, later_repo)
        self.assertEqual(Artifact.objects.get(pk=original.pk).repo, self.repo)
