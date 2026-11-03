"""Tests for US-5 (#21): crediting PR reviews to the reviewer.

PyGithub's client is replaced by fakes shaped like the objects it returns, so nothing
here touches the network. A PR's reviews come from its own get_reviews() call, and a
pending review has a null submitted date, as on GitHub.

Most PRs here were merged long ago, so that a review is the only artifact in the week.
Each exclusion test also ingests a review that must be returned, so that it can't pass
just because nothing was collected.
"""

from datetime import timedelta
from types import SimpleNamespace

from ingest.models import Artifact
from ingest.services import student_week_artifacts
from ingest.tests.test_ingest_commits import (
    FULL_NAME,
    MID_WEEK,
    STUDENT,
    WEEK_END,
    WEEK_START,
)
from ingest.tests.test_ingest_prs_issues import (
    LAST_WEEK,
    NEXT_WEEK_MID,
    WEEKS,
    ActivityTestCase,
    fake_pull,
    github_user,
    pr_id,
)
from ingest.tests.test_student_week_artifacts import ONE_MICROSECOND, make_user

REVIEWER = "sample-reviewer"
LONG_AGO = MID_WEEK - timedelta(days=60)
CONTROL = 999  # The ID of the in-week review that exclusion tests also ingest.


def review_id(number):
    return f"{FULL_NAME}:review:{number}"


def fake_review(
    number,
    login=REVIEWER,
    state="APPROVED",
    submitted=MID_WEEK,
    body="The done button works as described.",
    pull_number=1,
):
    """A review as PyGithub lists it. GitHub sends a null body if the reviewer wrote none."""
    return SimpleNamespace(
        id=number,
        html_url=f"https://github.com/{FULL_NAME}/pull/{pull_number}#pullrequestreview-{number}",
        user=github_user(login),
        state=state,
        submitted_at=submitted,
        body=body,
    )


def old_pull(*reviews, number=1, login=STUDENT):
    """A PR merged long before the week, so it isn't an artifact of the week itself."""
    return fake_pull(number, created=LONG_AGO, merged=LONG_AGO, login=login, reviews=reviews)


class ReviewCreditTests(ActivityTestCase):
    def test_a_review_is_saved_as_a_review_artifact(self):
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.assert_ids(artifacts, review_id(1))
        self.assertEqual(self.saved(review_id(1)).type, Artifact.Type.REVIEW)

    def test_a_review_is_credited_to_the_reviewer_not_the_pr_author(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.assertEqual(self.saved(review_id(1)).author_login, REVIEWER)

    def test_a_review_links_to_itself_on_github(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.assertEqual(
            self.saved(review_id(1)).url,
            f"https://github.com/{FULL_NAME}/pull/1#pullrequestreview-1",
        )

    def test_a_review_is_dated_when_it_was_submitted(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1, submitted=MID_WEEK))])
        self.assertEqual(self.saved(review_id(1)).timestamp, MID_WEEK)

    def test_a_review_stores_its_body(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1, body="Please add a test."))])
        self.assertEqual(self.saved(review_id(1)).body, "Please add a test.")

    def test_a_review_with_no_body_is_saved_with_an_empty_one(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1, body=None))])
        self.assertEqual(self.saved(review_id(1)).body, "")

    def test_a_saved_review_passes_the_artifact_models_validation(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.saved(review_id(1)).full_clean()

    def test_each_review_on_a_pr_is_credited_to_its_own_reviewer(self):
        self.ingest_activity(
            pulls=[old_pull(fake_review(1, login="reviewer-a"), fake_review(2, login="reviewer-b"))]
        )
        self.assertEqual(self.saved(review_id(1)).author_login, "reviewer-a")
        self.assertEqual(self.saved(review_id(2)).author_login, "reviewer-b")

    def test_two_reviews_by_one_reviewer_are_both_credited(self):
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1), fake_review(2))])
        self.assert_ids(artifacts, review_id(1), review_id(2))

    def test_reviews_are_collected_from_every_pr(self):
        artifacts = self.ingest_activity(
            pulls=[old_pull(fake_review(1), number=1), old_pull(fake_review(2), number=2)]
        )
        self.assert_ids(artifacts, review_id(1), review_id(2))

    def test_a_review_on_a_pr_closed_without_merging_is_credited(self):
        pull = fake_pull(1, created=LONG_AGO, closed=True, reviews=[fake_review(1)])
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))

    def test_a_review_on_an_open_pr_is_credited(self):
        pull = fake_pull(1, created=LONG_AGO, reviews=[fake_review(1)])
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))

    def test_ingesting_a_week_twice_doesnt_duplicate_a_review(self):
        pull = old_pull(fake_review(1))
        self.ingest_activity(pulls=[pull])
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))
        self.assertEqual(Artifact.objects.filter(artifact_id=review_id(1)).count(), 1)


class StudentCreditTests(ActivityTestCase):
    """A student who mainly reviews shouldn't look inactive."""

    def setUp(self):
        super().setUp()
        self.reviewer = make_user(REVIEWER)
        self.author = make_user(STUDENT)

    def credited_ids(self, user):
        artifacts = student_week_artifacts(user, self.team, WEEK_START)
        return [artifact.artifact_id for artifact in artifacts]

    def test_a_student_who_only_reviewed_has_that_review_this_week(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.assertEqual(self.credited_ids(self.reviewer), [review_id(1)])

    def test_the_pr_author_isnt_credited_with_a_review_of_their_pr(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        self.assertEqual(self.credited_ids(self.reviewer), [review_id(1)])
        self.assertEqual(self.credited_ids(self.author), [])

    def test_a_review_from_last_week_isnt_credited_this_week(self):
        pull = old_pull(fake_review(1, submitted=LAST_WEEK), fake_review(CONTROL))
        self.ingest_activity(pulls=[pull])
        self.ingest_activity(
            pulls=[pull],
            week_start=WEEK_START - timedelta(days=7),
            week_end=WEEK_END - timedelta(days=7),
        )
        self.assertEqual(self.credited_ids(self.reviewer), [review_id(CONTROL)])


class ReviewStateTests(ActivityTestCase):
    """`review_state` holds GitHub's value unchanged."""

    def test_a_review_stores_its_state(self):
        for number, state in enumerate(["APPROVED", "CHANGES_REQUESTED", "COMMENTED"], start=1):
            with self.subTest(state):
                self.ingest_activity(pulls=[old_pull(fake_review(number, state=state))])
                self.assertEqual(self.saved(review_id(number)).review_state, state)

    def test_a_dismissed_review_still_counts_because_it_was_submitted(self):
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1, state="DISMISSED"))])
        self.assert_ids(artifacts, review_id(1))
        self.assertEqual(self.saved(review_id(1)).review_state, "DISMISSED")

    def test_a_review_dismissed_after_ingest_updates_its_state_on_the_next_run(self):
        self.ingest_activity(pulls=[old_pull(fake_review(1))])
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1, state="DISMISSED"))])
        self.assert_ids(artifacts, review_id(1))
        self.assertEqual(self.saved(review_id(1)).review_state, "DISMISSED")

    def test_only_reviews_have_a_state(self):
        self.ingest_activity(pulls=[fake_pull(1, reviews=[fake_review(1)])])
        self.assertEqual(self.saved(pr_id(1)).review_state, "")


class ReviewWeekTests(ActivityTestCase):
    def test_a_review_this_week_on_a_pr_merged_in_an_earlier_week_counts_this_week(self):
        pull = fake_pull(1, created=LONG_AGO, merged=LAST_WEEK, reviews=[fake_review(1)])
        # The PR itself belongs to the week it merged in, so only its review is returned.
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))
        self.assertEqual(self.saved(review_id(1)).timestamp, MID_WEEK)

    def test_a_review_this_week_on_a_pr_opened_in_an_earlier_week_counts_this_week(self):
        pull = fake_pull(1, created=LAST_WEEK, reviews=[fake_review(1)])
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))

    def test_a_review_this_week_on_a_pr_merged_next_week_counts_this_week(self):
        pull = fake_pull(1, created=LONG_AGO, merged=NEXT_WEEK_MID, reviews=[fake_review(1)])
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(1))

    def test_a_review_on_an_old_pr_is_found_behind_prs_opened_since(self):
        # GitHub lists the newest PRs first, so stopping at the week's start would miss it.
        artifacts = self.ingest_activity(
            pulls=[fake_pull(2, created=NEXT_WEEK_MID), old_pull(fake_review(1))]
        )
        self.assert_ids(artifacts, review_id(1))

    def test_a_review_from_an_earlier_week_doesnt_count_even_if_the_pr_merged_this_week(self):
        pull = fake_pull(
            1,
            created=LONG_AGO,
            merged=MID_WEEK,
            reviews=[fake_review(1, submitted=LAST_WEEK), fake_review(CONTROL)],
        )
        self.assert_ids(self.ingest_activity(pulls=[pull]), pr_id(1), review_id(CONTROL))

    def test_a_review_from_a_later_week_doesnt_count_this_week(self):
        pull = old_pull(fake_review(1, submitted=NEXT_WEEK_MID), fake_review(CONTROL))
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_review_counts_in_the_week_it_was_submitted_when_that_week_is_ingested(self):
        artifacts = self.ingest_activity(
            pulls=[old_pull(fake_review(1, submitted=LAST_WEEK))],
            week_start=WEEK_START - timedelta(days=7),
            week_end=WEEK_END - timedelta(days=7),
        )
        self.assert_ids(artifacts, review_id(1))

    def test_week_boundaries_use_the_submitted_date(self):
        for name, (week_start, week_end, first, next_week) in WEEKS.items():
            with self.subTest(name):
                Artifact.objects.all().delete()
                artifacts = self.ingest_activity(
                    pulls=[
                        old_pull(
                            fake_review(1, submitted=next_week),
                            fake_review(2, submitted=next_week - ONE_MICROSECOND),
                            fake_review(3, submitted=first),
                            fake_review(4, submitted=first - ONE_MICROSECOND),
                        )
                    ],
                    week_start=week_start,
                    week_end=week_end,
                )
                self.assert_ids(artifacts, review_id(2), review_id(3))


class ExcludedReviewTests(ActivityTestCase):
    def test_a_pending_review_doesnt_count(self):
        pull = old_pull(fake_review(1, state="PENDING", submitted=None), fake_review(CONTROL))
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_pending_review_doesnt_count_even_with_a_date_in_the_week(self):
        pull = old_pull(fake_review(1, state="PENDING"), fake_review(CONTROL))
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_review_with_no_submitted_date_doesnt_count(self):
        pull = old_pull(fake_review(1, submitted=None), fake_review(CONTROL))
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_review_on_your_own_pr_doesnt_count(self):
        pull = old_pull(
            fake_review(1, login=STUDENT, state="COMMENTED"),
            fake_review(CONTROL),
            login=STUDENT,
        )
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_review_on_your_own_pr_doesnt_count_when_the_login_case_differs(self):
        # GitHub logins ignore case, as student_week_artifacts does when it credits them.
        pull = old_pull(
            fake_review(1, login=STUDENT.upper(), state="COMMENTED"),
            fake_review(CONTROL),
            login=STUDENT,
        )
        self.assert_ids(self.ingest_activity(pulls=[pull]), review_id(CONTROL))

    def test_a_review_on_your_own_pr_isnt_stored_at_all(self):
        pull = old_pull(fake_review(1, login=STUDENT), fake_review(CONTROL), login=STUDENT)
        self.ingest_activity(pulls=[pull])
        self.assertFalse(Artifact.objects.filter(artifact_id=review_id(1)).exists())
        self.assertTrue(Artifact.objects.filter(artifact_id=review_id(CONTROL)).exists())


class DeletedReviewerTests(ActivityTestCase):
    def assert_collected_without_credit(self, artifact_id, artifacts):
        self.assert_ids(artifacts, artifact_id)
        self.assertEqual(self.saved(artifact_id).author_login, "")

    def test_a_review_with_a_null_user_is_collected_without_credit(self):
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1, login=None))])
        self.assert_collected_without_credit(review_id(1), artifacts)

    def test_a_review_from_the_ghost_account_is_collected_without_credit(self):
        artifacts = self.ingest_activity(pulls=[old_pull(fake_review(1, login="ghost"))])
        self.assert_collected_without_credit(review_id(1), artifacts)

    def test_a_deleted_reviewer_on_a_deleted_authors_pr_isnt_treated_as_their_own_pr(self):
        # Two deleted accounts both show as ghost or null, which says nothing about who they were.
        for number, login in enumerate(["ghost", None], start=1):
            with self.subTest(login=login):
                Artifact.objects.all().delete()
                artifacts = self.ingest_activity(
                    pulls=[old_pull(fake_review(number, login=login), number=number, login=login)]
                )
                self.assert_collected_without_credit(review_id(number), artifacts)

    def test_a_review_on_a_deleted_authors_pr_is_still_credited_to_the_reviewer(self):
        for number, login in enumerate(["ghost", None], start=1):
            with self.subTest(login=login):
                self.ingest_activity(
                    pulls=[old_pull(fake_review(number), number=number, login=login)]
                )
                self.assertEqual(self.saved(review_id(number)).author_login, REVIEWER)
