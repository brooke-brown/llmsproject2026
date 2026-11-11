"""Tests for US-4 (#20): collecting a repo's PRs and issues for a reporting week.

PyGithub's client is replaced by fakes shaped like the objects it returns, so nothing
here touches the network. The fake repo lists every PR as an issue too, as GitHub does,
and lists only open items unless asked for all of them.

Each exclusion test also ingests an item that must be returned, so that it can't pass
just because nothing was collected.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from ingest import services
from ingest.models import Artifact
from ingest.tests.test_ingest_commits import (
    FIRST_MOMENT,
    FULL_NAME,
    MID_WEEK,
    NEXT_WEEK,
    STUDENT,
    WEEK_END,
    WEEK_START,
    IngestTestCase,
    fake_commit,
    fake_file,
    sha,
)
from ingest.tests.test_student_artifacts import ONE_MICROSECOND, make_user

LAST_WEEK = MID_WEEK - timedelta(days=7)
NEXT_WEEK_MID = MID_WEEK + timedelta(days=7)
CONTROL = 99  # The number of the in-week item that exclusion tests also ingest.

# (week_start, week_end, first moment, first moment of the next week)
WEEKS = {
    "no clock change": (WEEK_START, WEEK_END, FIRST_MOMENT, NEXT_WEEK),
    # Clocks go back on Sunday, Nov 1, 2026, so this week is 169 hours long.
    "clocks go back": (
        date(2026, 10, 26),
        date(2026, 11, 1),
        datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc),
    ),
    # Clocks go forward on Sunday, Mar 14, 2027, so this week is 167 hours long.
    "clocks go forward": (
        date(2027, 3, 8),
        date(2027, 3, 14),
        datetime(2027, 3, 8, 5, 0, tzinfo=timezone.utc),
        datetime(2027, 3, 15, 4, 0, tzinfo=timezone.utc),
    ),
}


def pr_id(number):
    return f"{FULL_NAME}:pr:{number}"


def issue_id(number):
    return f"{FULL_NAME}:issue:{number}"


def github_user(login):
    """GitHub sends a null user or its placeholder `ghost` for a deleted account."""
    return SimpleNamespace(login=login) if login else None


def fake_pull(
    number,
    title="Add the task page",
    body="Adds a page that lists tasks.",
    created=MID_WEEK,
    merged=None,
    closed=False,
    updated=None,
    login=STUDENT,
    files=None,
    reviews=(),
):
    """A PR as PyGithub lists it. Its files and reviews each need a separate call."""
    state = "closed" if merged or closed else "open"
    updated = updated or merged or created
    user = github_user(login)
    url = f"https://github.com/{FULL_NAME}/pull/{number}"
    changed_files = [fake_file()] if files is None else files
    return SimpleNamespace(
        number=number,
        html_url=url,
        title=title,
        body=body,
        user=user,
        state=state,
        merged=merged is not None,
        merged_at=merged,
        created_at=created,
        updated_at=updated,
        get_files=lambda: iter(changed_files),
        get_reviews=lambda: iter(reviews),
        as_issue=SimpleNamespace(
            number=number,
            html_url=url,
            title=title,
            body=body,
            user=user,
            state=state,
            created_at=created,
            updated_at=updated,
            pull_request=SimpleNamespace(html_url=url),
        ),
    )


def fake_issue(
    number,
    title="The task page is slow",
    body="It takes ten seconds to load.",
    created=MID_WEEK,
    closed=False,
    updated=None,
    login=STUDENT,
):
    """An issue that isn't a PR, as PyGithub lists it. `pull_request` is None."""
    return SimpleNamespace(
        number=number,
        html_url=f"https://github.com/{FULL_NAME}/issues/{number}",
        title=title,
        body=body,
        user=github_user(login),
        state="closed" if closed else "open",
        created_at=created,
        updated_at=updated or created,
        pull_request=None,
    )


class ActivityTestCase(IngestTestCase):
    def ingest_activity(self, pulls=(), issues=(), **week):
        return self.ingest({}, pulls=pulls, issues=issues, **week)

    def assert_ids(self, artifacts, *expected_ids):
        # assertCountEqual also catches an item that's returned twice.
        self.assertCountEqual([artifact.artifact_id for artifact in artifacts], expected_ids)

    def saved(self, artifact_id):
        return Artifact.objects.get(repo=self.repo, artifact_id=artifact_id)


class PullRequestWeekTests(ActivityTestCase):
    def test_a_pr_opened_last_week_and_merged_this_week_appears_this_week(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1, created=LAST_WEEK, merged=MID_WEEK)])
        self.assert_ids(artifacts, pr_id(1))
        self.assertEqual(self.saved(pr_id(1)).timestamp, MID_WEEK)

    def test_an_old_pr_merged_this_week_is_found_behind_prs_opened_since(self):
        # GitHub lists the newest PRs first, so stopping at the week's start would miss it.
        artifacts = self.ingest_activity(
            pulls=[
                fake_pull(1, created=NEXT_WEEK_MID),
                fake_pull(2, created=LAST_WEEK - timedelta(days=30)),
                fake_pull(3, created=LAST_WEEK - timedelta(days=60), merged=MID_WEEK),
            ]
        )
        self.assert_ids(artifacts, pr_id(3))

    def test_a_pr_opened_this_week_and_merged_next_week_appears_only_next_week(self):
        pulls = [fake_pull(CONTROL), fake_pull(1, created=MID_WEEK, merged=NEXT_WEEK_MID)]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))
        next_week = self.ingest_activity(
            pulls=pulls,
            week_start=WEEK_START + timedelta(days=7),
            week_end=WEEK_END + timedelta(days=7),
        )
        self.assert_ids(next_week, pr_id(1))

    def test_a_pr_opened_and_merged_last_week_doesnt_appear_this_week(self):
        pulls = [fake_pull(CONTROL), fake_pull(1, created=LAST_WEEK, merged=LAST_WEEK)]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))

    def test_an_unmerged_pr_counts_in_the_week_it_was_created(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1)])
        self.assert_ids(artifacts, pr_id(1))
        self.assertEqual(self.saved(pr_id(1)).timestamp, MID_WEEK)

    def test_an_unmerged_pr_from_last_week_doesnt_appear_this_week(self):
        pulls = [fake_pull(CONTROL), fake_pull(1, created=LAST_WEEK, updated=MID_WEEK)]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))

    def test_a_pr_closed_without_merging_counts_in_the_week_it_was_created(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1, closed=True)])
        self.assert_ids(artifacts, pr_id(1))
        self.assertEqual(self.saved(pr_id(1)).timestamp, MID_WEEK)

    def test_a_pr_from_last_week_closed_without_merging_this_week_doesnt_appear(self):
        pulls = [
            fake_pull(CONTROL),
            fake_pull(1, created=LAST_WEEK, closed=True, updated=MID_WEEK),
        ]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))

    def test_a_pr_that_merges_later_leaves_the_week_it_was_first_stored_in(self):
        self.assert_ids(self.ingest_activity(pulls=[fake_pull(1)]), pr_id(1))
        pulls = [fake_pull(CONTROL), fake_pull(1, created=MID_WEEK, merged=NEXT_WEEK_MID)]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))

    def test_week_boundaries_use_the_merged_date(self):
        for name, (week_start, week_end, first, next_week) in WEEKS.items():
            long_ago = first - timedelta(days=30)
            with self.subTest(name):
                Artifact.objects.all().delete()
                artifacts = self.ingest_activity(
                    pulls=[
                        fake_pull(1, created=long_ago, merged=next_week),
                        fake_pull(2, created=long_ago, merged=next_week - ONE_MICROSECOND),
                        fake_pull(3, created=long_ago, merged=first),
                        fake_pull(4, created=long_ago, merged=first - ONE_MICROSECOND),
                    ],
                    week_start=week_start,
                    week_end=week_end,
                )
                self.assert_ids(artifacts, pr_id(2), pr_id(3))

    def test_week_boundaries_use_the_created_date_when_unmerged(self):
        for name, (week_start, week_end, first, next_week) in WEEKS.items():
            with self.subTest(name):
                Artifact.objects.all().delete()
                artifacts = self.ingest_activity(
                    pulls=[
                        fake_pull(1, created=next_week),
                        fake_pull(2, created=next_week - ONE_MICROSECOND),
                        fake_pull(3, created=first),
                        fake_pull(4, created=first - ONE_MICROSECOND),
                    ],
                    week_start=week_start,
                    week_end=week_end,
                )
                self.assert_ids(artifacts, pr_id(2), pr_id(3))


class IssueWeekTests(ActivityTestCase):
    def test_an_issue_counts_in_the_week_it_was_created(self):
        artifacts = self.ingest_activity(issues=[fake_issue(1)])
        self.assert_ids(artifacts, issue_id(1))
        self.assertEqual(self.saved(issue_id(1)).timestamp, MID_WEEK)

    def test_a_closed_issue_counts_in_the_week_it_was_created(self):
        artifacts = self.ingest_activity(issues=[fake_issue(1, closed=True, updated=MID_WEEK)])
        self.assert_ids(artifacts, issue_id(1))

    def test_an_issue_updated_after_the_week_still_counts_in_the_week_it_was_created(self):
        artifacts = self.ingest_activity(issues=[fake_issue(1, updated=NEXT_WEEK_MID)])
        self.assert_ids(artifacts, issue_id(1))

    def test_an_issue_from_last_week_closed_this_week_doesnt_appear(self):
        issues = [
            fake_issue(CONTROL),
            fake_issue(1, created=LAST_WEEK, closed=True, updated=MID_WEEK),
        ]
        self.assert_ids(self.ingest_activity(issues=issues), issue_id(CONTROL))

    def test_an_issue_from_last_week_updated_this_week_doesnt_appear(self):
        issues = [fake_issue(CONTROL), fake_issue(1, created=LAST_WEEK, updated=MID_WEEK)]
        self.assert_ids(self.ingest_activity(issues=issues), issue_id(CONTROL))

    def test_an_issue_from_next_week_doesnt_appear(self):
        issues = [fake_issue(CONTROL), fake_issue(1, created=NEXT_WEEK_MID)]
        self.assert_ids(self.ingest_activity(issues=issues), issue_id(CONTROL))

    def test_week_boundaries_use_the_created_date(self):
        for name, (week_start, week_end, first, next_week) in WEEKS.items():
            with self.subTest(name):
                Artifact.objects.all().delete()
                artifacts = self.ingest_activity(
                    issues=[
                        fake_issue(1, created=next_week),
                        fake_issue(2, created=next_week - ONE_MICROSECOND),
                        fake_issue(3, created=first),
                        fake_issue(4, created=first - ONE_MICROSECOND),
                    ],
                    week_start=week_start,
                    week_end=week_end,
                )
                self.assert_ids(artifacts, issue_id(2), issue_id(3))


class PullRequestNotAlsoIssueTests(ActivityTestCase):
    def assert_only_a_pr(self, pull):
        self.assert_ids(self.ingest_activity(pulls=[pull]), pr_id(pull.number))
        self.assert_ids(Artifact.objects.filter(repo=self.repo), pr_id(pull.number))

    def test_a_merged_pr_is_only_a_pr(self):
        self.assert_only_a_pr(fake_pull(1, merged=MID_WEEK))

    def test_an_open_pr_is_only_a_pr(self):
        self.assert_only_a_pr(fake_pull(1))

    def test_a_pr_closed_without_merging_is_only_a_pr(self):
        self.assert_only_a_pr(fake_pull(1, closed=True))

    def test_a_pr_merging_next_week_isnt_an_issue_this_week(self):
        # Listed as an issue, it was created this week. As a PR, it belongs to next week.
        pulls = [fake_pull(CONTROL), fake_pull(1, created=MID_WEEK, merged=NEXT_WEEK_MID)]
        self.assert_ids(self.ingest_activity(pulls=pulls), pr_id(CONTROL))
        self.assertFalse(Artifact.objects.filter(artifact_id=issue_id(1)).exists())

    def test_prs_and_issues_are_each_collected_as_their_own_type(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1)], issues=[fake_issue(2)])
        self.assert_ids(artifacts, pr_id(1), issue_id(2))
        self.assertEqual(self.saved(pr_id(1)).type, Artifact.Type.PR)
        self.assertEqual(self.saved(issue_id(2)).type, Artifact.Type.ISSUE)


class StoredFieldsTests(ActivityTestCase):
    def test_stores_a_prs_details(self):
        self.ingest_activity(pulls=[fake_pull(12, title="Add login", body="Adds a login page.")])
        artifact = self.saved(pr_id(12))
        self.assertEqual(artifact.url, f"https://github.com/{FULL_NAME}/pull/12")
        self.assertEqual(artifact.title, "Add login")
        self.assertEqual(artifact.body, "Adds a login page.")
        self.assertEqual(artifact.author_login, STUDENT)
        artifact.full_clean()

    def test_stores_an_issues_details(self):
        self.ingest_activity(issues=[fake_issue(7, title="Login fails", body="It shows a 500.")])
        artifact = self.saved(issue_id(7))
        self.assertEqual(artifact.url, f"https://github.com/{FULL_NAME}/issues/7")
        self.assertEqual(artifact.title, "Login fails")
        self.assertEqual(artifact.body, "It shows a 500.")
        self.assertEqual(artifact.author_login, STUDENT)
        self.assertEqual(artifact.diff, "")
        self.assertFalse(artifact.diff_truncated)
        artifact.full_clean()

    def test_a_pr_with_no_description_is_stored_with_an_empty_body(self):
        # GitHub sends a null body, which the Artifact's body column can't hold.
        self.ingest_activity(pulls=[fake_pull(1, body=None)])
        self.assertEqual(self.saved(pr_id(1)).body, "")

    def test_an_issue_with_no_description_is_stored_with_an_empty_body(self):
        self.ingest_activity(issues=[fake_issue(1, body=None)])
        self.assertEqual(self.saved(issue_id(1)).body, "")


class PullRequestDiffTests(ActivityTestCase):
    def ingest_diff(self, files):
        self.ingest_activity(pulls=[fake_pull(1, files=files)])
        return self.saved(pr_id(1))

    def test_stores_each_changed_files_name_and_changes(self):
        files = [
            fake_file("app/models.py", "@@ -0,0 +1 @@\n+class Task: pass"),
            fake_file("app/views.py", "@@ -1 +1 @@\n-old_view\n+new_view"),
        ]
        artifact = self.ingest_diff(files)
        for file in files:
            self.assertIn(file.filename, artifact.diff)
            self.assertIn(file.patch, artifact.diff)
        self.assertFalse(artifact.diff_truncated)

    def test_a_binary_file_with_no_patch_is_named_without_failing(self):
        artifact = self.ingest_diff([fake_file("app/logo.png", None)])
        self.assertIn("app/logo.png", artifact.diff)
        self.assertNotIn("None", artifact.diff)

    def test_a_very_large_diff_is_cut_to_the_commit_limit_and_marked_as_truncated(self):
        artifact = self.ingest_diff([fake_file(patch="+" * (services.MAX_DIFF_CHARS + 1))])
        self.assertEqual(len(artifact.diff), services.MAX_DIFF_CHARS)
        self.assertTrue(artifact.diff_truncated)

    def test_a_diff_exactly_at_the_limit_is_not_marked_as_truncated(self):
        whole = self.ingest_diff([fake_file()]).diff
        Artifact.objects.all().delete()
        with mock.patch("ingest.services.MAX_DIFF_CHARS", len(whole)):
            artifact = self.ingest_diff([fake_file()])
        self.assertEqual(artifact.diff, whole)
        self.assertFalse(artifact.diff_truncated)

    def test_a_diff_one_character_over_the_limit_is_marked_as_truncated(self):
        whole = self.ingest_diff([fake_file()]).diff
        Artifact.objects.all().delete()
        with mock.patch("ingest.services.MAX_DIFF_CHARS", len(whole) - 1):
            artifact = self.ingest_diff([fake_file()])
        self.assertEqual(artifact.diff, whole[:-1])
        self.assertTrue(artifact.diff_truncated)


class DeletedAccountTests(ActivityTestCase):
    def assert_collected_without_credit(self, artifact_id, artifacts):
        self.assert_ids(artifacts, artifact_id)
        self.assertEqual(self.saved(artifact_id).author_login, "")

    def test_a_pr_with_a_null_user_is_collected_without_credit(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1, login=None)])
        self.assert_collected_without_credit(pr_id(1), artifacts)

    def test_a_pr_from_the_ghost_account_is_collected_without_credit(self):
        artifacts = self.ingest_activity(pulls=[fake_pull(1, login="ghost")])
        self.assert_collected_without_credit(pr_id(1), artifacts)

    def test_an_issue_with_a_null_user_is_collected_without_credit(self):
        artifacts = self.ingest_activity(issues=[fake_issue(1, login=None)])
        self.assert_collected_without_credit(issue_id(1), artifacts)

    def test_an_issue_from_the_ghost_account_is_collected_without_credit(self):
        artifacts = self.ingest_activity(issues=[fake_issue(1, login="ghost")])
        self.assert_collected_without_credit(issue_id(1), artifacts)

    def test_deleted_accounts_activity_isnt_credited_to_a_student(self):
        from ingest.services import student_artifacts

        self.ingest_activity(
            pulls=[fake_pull(1, login=None), fake_pull(2, login="ghost"), fake_pull(CONTROL)],
            issues=[fake_issue(3, login=None), fake_issue(4, login="ghost")],
        )
        credited = student_artifacts(make_user(STUDENT), self.team, WEEK_START, WEEK_START + timedelta(days=6))
        self.assert_ids(credited, pr_id(CONTROL))


class ReturnedArtifactsTests(ActivityTestCase):
    def test_returns_commits_prs_and_issues_together(self):
        artifacts = self.ingest(
            {"main": [fake_commit(sha("a"))]}, pulls=[fake_pull(1)], issues=[fake_issue(2)]
        )
        self.assert_ids(artifacts, f"{FULL_NAME}:commit:{sha('a')}", pr_id(1), issue_id(2))

    def test_a_second_run_creates_no_duplicates(self):
        activity = {"pulls": [fake_pull(1, merged=MID_WEEK)], "issues": [fake_issue(2)]}
        first = self.ingest_activity(**activity)
        second = self.ingest_activity(**activity)
        self.assertCountEqual(second, first)
        self.assertEqual(Artifact.objects.filter(repo=self.repo).count(), 2)
