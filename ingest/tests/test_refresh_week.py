"""Tests for US-7 (#23): refreshing a week safely.

These reuse the US-2, US-4 and US-5 fakes, so nothing here touches the network. A refresh
is another `ingest` run, which is what the "Generate summaries" button does.

Ingest has saved with update_or_create since US-2, so most of these guard behavior it
already has. The ones that drive new work are in LaterRefreshTests: an issue or review
stored for one week has to pick up changes when a different week is refreshed, as a PR
already does.
"""

from datetime import date, timedelta

from django.urls import reverse

from core.models import Enrollment
from ingest.models import Artifact
from ingest.tests.test_ingest_commits import (
    FULL_NAME,
    MID_WEEK,
    fake_commit,
    fake_file,
    sha,
)
from ingest.tests.test_ingest_prs_issues import (
    LAST_WEEK,
    NEXT_WEEK_MID,
    ActivityTestCase,
    fake_issue,
    fake_pull,
    issue_id,
    pr_id,
)
from ingest.tests.test_review_credit import fake_review, old_pull, review_id
from ingest.tests.test_student_week_artifacts import add_repo, make_team, make_user
from summaries.models import Claim, Summary

COMMIT_ID = f"{FULL_NAME}:commit:{sha('a')}"
PREVIOUS_WEEK = {"week_start": date(2026, 10, 12), "week_end": date(2026, 10, 18)}
FOLLOWING_WEEK = {"week_start": date(2026, 10, 26), "week_end": date(2026, 11, 1)}
EDITED = "Edited on GitHub"


def one_of_each():
    """A commit, PR, review and issue in the week, as keyword arguments for `ingest`."""
    return {
        "branches": {"main": [fake_commit(sha("a"))]},
        "pulls": [fake_pull(1, reviews=[fake_review(1)])],
        "issues": [fake_issue(2)],
    }


EVERY_ID = (COMMIT_ID, pr_id(1), review_id(1), issue_id(2))


class NoDuplicatesTests(ActivityTestCase):
    def test_refreshing_a_week_creates_no_duplicates(self):
        self.ingest(**one_of_each())
        artifacts = self.ingest(**one_of_each())
        self.assert_ids(artifacts, *EVERY_ID)
        self.assertEqual(Artifact.objects.count(), len(EVERY_ID))

    def test_refreshing_a_week_keeps_the_same_rows(self):
        # Claims point at these rows, so a refresh has to update them rather than replace them.
        first = {artifact.artifact_id: artifact.pk for artifact in self.ingest(**one_of_each())}
        second = {artifact.artifact_id: artifact.pk for artifact in self.ingest(**one_of_each())}
        self.assertEqual(first, second)

    def test_a_pr_that_changes_weeks_is_still_one_artifact(self):
        self.ingest_activity(pulls=[fake_pull(1)])
        merged = fake_pull(1, merged=NEXT_WEEK_MID)
        self.ingest_activity(pulls=[merged])
        self.ingest_activity(pulls=[merged], **FOLLOWING_WEEK)
        self.assertEqual(Artifact.objects.filter(artifact_id=pr_id(1)).count(), 1)

    def test_refreshing_one_teams_repo_leaves_another_teams_copy_alone(self):
        # A later project reusing a GitHub repo has its own Repo row and copies.
        other_repo = add_repo(make_team(self.project, name="Team B"), FULL_NAME)
        self.ingest_activity(pulls=[fake_pull(1)])
        self.ingest_activity(pulls=[fake_pull(1)], repo=other_repo)

        self.ingest_activity(pulls=[fake_pull(1, title=EDITED)])

        self.assertEqual(self.saved(pr_id(1)).title, EDITED)
        self.assertNotEqual(Artifact.objects.get(repo=other_repo).title, EDITED)
        self.assertEqual(Artifact.objects.count(), 2)


class GitHubChangesTests(ActivityTestCase):
    def test_an_edited_pr_appears_after_a_refresh(self):
        self.ingest_activity(pulls=[fake_pull(1)])
        self.ingest_activity(pulls=[fake_pull(1, title=EDITED, body="An edited description.")])
        self.assertEqual(self.saved(pr_id(1)).title, EDITED)
        self.assertEqual(self.saved(pr_id(1)).body, "An edited description.")

    def test_commits_pushed_to_a_pr_change_its_diff_after_a_refresh(self):
        self.ingest_activity(pulls=[fake_pull(1)])
        pushed = fake_file("app/views.py", "@@ -0,0 +1 @@\n+def index(): pass")
        self.ingest_activity(pulls=[fake_pull(1, files=[pushed])])
        self.assertIn("app/views.py", self.saved(pr_id(1)).diff)

    def test_an_edited_issue_appears_after_a_refresh(self):
        self.ingest_activity(issues=[fake_issue(1)])
        self.ingest_activity(issues=[fake_issue(1, title=EDITED, body="An edited description.")])
        self.assertEqual(self.saved(issue_id(1)).title, EDITED)
        self.assertEqual(self.saved(issue_id(1)).body, "An edited description.")

    def test_an_edited_review_appears_after_a_refresh(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.ingest_activity(pulls=[old_pull(fake_review(1, state="DISMISSED", body=EDITED))])
        self.assertEqual(self.saved(review_id(1)).review_state, "DISMISSED")
        self.assertEqual(self.saved(review_id(1)).body, EDITED)

    def test_a_commit_author_linked_to_an_account_appears_after_a_refresh(self):
        self.ingest({"main": [fake_commit(sha("a"), login=None)]})
        self.ingest({"main": [fake_commit(sha("a"))]})
        self.assertNotEqual(self.saved(COMMIT_ID).author_login, "")

    def test_new_activity_appears_after_a_refresh(self):
        self.ingest({"main": [fake_commit(sha("b"))]})
        artifacts = self.ingest(
            {"main": [fake_commit(sha("a")), fake_commit(sha("b"))]},
            pulls=[fake_pull(1, reviews=[fake_review(1)])],
            issues=[fake_issue(2)],
        )
        self.assert_ids(artifacts, *EVERY_ID, f"{FULL_NAME}:commit:{sha('b')}")

    def test_a_pr_merged_since_the_last_refresh_appears_in_the_week_it_merged(self):
        self.ingest_activity(pulls=[fake_pull(1, created=LAST_WEEK)], **PREVIOUS_WEEK)
        artifacts = self.ingest_activity(pulls=[fake_pull(1, created=LAST_WEEK, merged=MID_WEEK)])
        self.assert_ids(artifacts, pr_id(1))
        self.assertEqual(self.saved(pr_id(1)).timestamp, MID_WEEK)

    def test_a_pr_merged_in_a_later_week_leaves_the_week_it_was_opened_in(self):
        self.assert_ids(self.ingest_activity(pulls=[fake_pull(1)]), pr_id(1))
        merged = fake_pull(1, merged=NEXT_WEEK_MID)
        self.assert_ids(self.ingest_activity(pulls=[merged]))
        self.assert_ids(self.ingest_activity(pulls=[merged], **FOLLOWING_WEEK), pr_id(1))


class LaterRefreshTests(ActivityTestCase):
    """A change reaches stored artifacts whichever week is refreshed next."""

    def test_a_refresh_of_a_later_week_updates_a_stored_pr(self):
        self.ingest_activity(pulls=[fake_pull(1, created=LAST_WEEK)], **PREVIOUS_WEEK)
        edited = fake_pull(1, title=EDITED, created=LAST_WEEK, updated=MID_WEEK)
        self.assert_ids(self.ingest_activity(pulls=[edited]))
        self.assertEqual(self.saved(pr_id(1)).title, EDITED)

    def test_a_refresh_of_a_later_week_updates_a_stored_issue(self):
        self.ingest_activity(issues=[fake_issue(1, created=LAST_WEEK)], **PREVIOUS_WEEK)
        edited = fake_issue(1, title=EDITED, created=LAST_WEEK, updated=MID_WEEK)
        # It's updated in place, and stays in the week it was created.
        self.assert_ids(self.ingest_activity(issues=[edited]))
        self.assertEqual(self.saved(issue_id(1)).title, EDITED)
        self.assertEqual(self.saved(issue_id(1)).timestamp, LAST_WEEK)

    def test_a_stored_issue_edited_before_the_refreshed_week_is_still_updated(self):
        # GitHub's `since` filters on the updated date, so this edit is older than the week.
        self.ingest_activity(issues=[fake_issue(1, created=LAST_WEEK)], **PREVIOUS_WEEK)
        edited = fake_issue(
            1, title=EDITED, created=LAST_WEEK, updated=LAST_WEEK + timedelta(days=1)
        )
        self.ingest_activity(issues=[edited])
        self.assertEqual(self.saved(issue_id(1)).title, EDITED)

    def test_a_refresh_of_a_later_week_updates_a_stored_reviews_state(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1, submitted=LAST_WEEK))], **PREVIOUS_WEEK)
        dismissed = fake_review(1, state="DISMISSED", submitted=LAST_WEEK)
        # It's updated in place, and stays in the week it was submitted.
        self.assert_ids(self.ingest_activity(pulls=[old_pull(dismissed)]))
        self.assertEqual(self.saved(review_id(1)).review_state, "DISMISSED")
        self.assertEqual(self.saved(review_id(1)).timestamp, LAST_WEEK)

    def test_a_refresh_of_a_later_week_updates_a_stored_reviews_body(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1, submitted=LAST_WEEK))], **PREVIOUS_WEEK)
        edited = fake_review(1, body=EDITED, submitted=LAST_WEEK)
        self.ingest_activity(pulls=[old_pull(edited)])
        self.assertEqual(self.saved(review_id(1)).body, EDITED)


class ExistingSummaryTests(ActivityTestCase):
    def setUp(self):
        super().setUp()
        self.ingest(**one_of_each())
        self.student = make_user("sample-summary-student")
        self.summary = Summary.objects.create(
            team=self.team, user=self.student, week_start=date(2026, 10, 19), artifact_count=4
        )
        self.claim = Claim.objects.create(
            summary=self.summary,
            text="Added the task page.",
            category=Claim.Category.FEATURE,
            significance=Claim.Significance.MODERATE,
            status=Claim.Status.VERIFIED,
            artifact_ids=list(EVERY_ID),
        )
        self.claim.artifacts.set(Artifact.objects.all())
        self.cited = {artifact.artifact_id: artifact.pk for artifact in Artifact.objects.all()}

    def assert_claim_still_links_to_every_artifact(self):
        self.claim.refresh_from_db()
        self.assertCountEqual(self.claim.artifact_ids, EVERY_ID)
        self.assertEqual(
            {artifact.artifact_id: artifact.pk for artifact in self.claim.artifacts.all()},
            self.cited,
        )

    def test_a_refresh_with_no_changes_keeps_the_links(self):
        self.ingest(**one_of_each())
        self.assert_claim_still_links_to_every_artifact()

    def test_a_refresh_with_edits_keeps_the_links(self):
        self.ingest(
            {"main": [fake_commit(sha("a"))]},
            pulls=[fake_pull(1, title=EDITED, reviews=[fake_review(1, state="DISMISSED")])],
            issues=[fake_issue(2, title=EDITED)],
        )
        self.assert_claim_still_links_to_every_artifact()
        self.assertEqual(self.claim.artifacts.get(artifact_id=pr_id(1)).title, EDITED)

    def test_a_cited_pr_that_moves_to_another_week_keeps_its_link(self):
        activity = one_of_each()
        activity["pulls"] = [fake_pull(1, merged=NEXT_WEEK_MID, reviews=[fake_review(1)])]
        artifacts = self.ingest(**activity)
        self.assertNotIn(pr_id(1), [artifact.artifact_id for artifact in artifacts])
        self.assert_claim_still_links_to_every_artifact()

    def test_a_cited_artifact_no_longer_on_github_keeps_its_link(self):
        # A force-push, or a branch deleted after a squash merge, removes commits from the list.
        self.ingest({})
        self.assert_claim_still_links_to_every_artifact()

    def test_the_summary_page_links_to_github_after_a_refresh(self):
        instructor = make_user("sample-instructor")
        Enrollment.objects.create(
            user=instructor, course=self.project.course, role=Enrollment.Role.INSTRUCTOR
        )
        self.ingest(
            {"main": [fake_commit(sha("a"))]},
            pulls=[fake_pull(1, title=EDITED, reviews=[fake_review(1)])],
            issues=[fake_issue(2, title=EDITED)],
        )
        self.client.force_login(instructor)
        response = self.client.get(reverse("dashboard:summary", args=[self.summary.pk]))
        self.assertEqual(response.status_code, 200)
        for artifact in Artifact.objects.all():
            self.assertContains(response, f'<a href="{artifact.url}">{artifact.artifact_id}</a>')
