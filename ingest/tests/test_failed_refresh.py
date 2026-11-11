"""Tests for US-8 (#24): understanding a failed refresh.

These reuse the fakes from earlier stories, so nothing here touches the network. Errors are
built by PyGithub's own Requester.createException from a status and message, so each one is
the exception class that response would really raise. A 429 or a 401 other than "Bad
credentials", for example, is only a plain GithubException.

Refreshes run inside TestCase's transaction, where a rollback is a savepoint rollback, so
leftover writes from a failed run are still visible to these tests.
"""

import logging
import os
import traceback
from unittest import mock

from github.Requester import Requester

from core.models import Repo, TeamMembership
from ingest import services
from ingest.models import Artifact
from ingest.tests.test_ingest_commits import (
    FULL_NAME,
    WEEK_END,
    WEEK_START,
    FakeGitHubRepo,
    fake_commit,
    sha,
)
from ingest.tests.test_ingest_prs_issues import (
    NEXT_WEEK_MID,
    ActivityTestCase,
    fake_issue,
    fake_pull,
    issue_id,
    pr_id,
)
from ingest.tests.test_refresh_week import COMMIT_ID, EVERY_ID, one_of_each
from ingest.tests.test_review_credit import fake_review, review_id
from ingest.tests.test_student_artifacts import make_user

TOKEN = "sample-secret-token-value"
EDITED = "Edited on GitHub"

BAD_CREDENTIALS = (401, "Bad credentials")
NOT_AUTHENTICATED = (401, "Requires authentication")
NO_ACCESS = (403, "Resource not accessible by personal access token")
NOT_FOUND = (404, "Not Found")
RATE_LIMITED = (403, "API rate limit exceeded for user ID 1.")
SECONDARY_RATE_LIMITED = (403, "You have exceeded a secondary rate limit.")
TOO_MANY_REQUESTS = (429, "Too many requests")
SERVER_ERROR = (500, "Server Error")


def github_error(status, message, echoing=""):
    """The exception PyGithub raises for this response. `echoing` is added to all of its text."""
    return Requester.createException(
        status, {"x-echo": echoing}, {"message": f"{message} {echoing}".strip()}
    )


def raises(error):
    def call(*args, **kwargs):
        raise error

    return call


def then_raises(items, error):
    """A paginated list that fails on a later page, as PyGithub's lazy lists can."""
    yield from items
    raise error


class CommitWhoseFilesFail:
    """A commit whose `files` needs a request, as in PyGithub, and that request fails."""

    def __init__(self, commit, error):
        self._commit = commit
        self._error = error

    def __getattr__(self, name):
        return getattr(self._commit, name)

    @property
    def files(self):
        raise self._error


class FailedRefreshTestCase(ActivityTestCase):
    def attempt(self, github_repo=None, get_repo_error=None, token=TOKEN):
        """Runs ingest, which must fail, and returns what it raised."""
        with (
            mock.patch("ingest.services.Github") as self.github,
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": token or ""}),
        ):
            if token is None:
                del os.environ["GITHUB_TOKEN"]
            get_repo = self.github.return_value.get_repo
            get_repo.return_value = github_repo
            get_repo.side_effect = get_repo_error
            try:
                services.ingest(self.repo, WEEK_START, WEEK_END)
            except Exception as error:
                return error
        self.fail("ingest should have raised, but it returned as if the week were complete.")

    def attempt_with_failing_repo(self, status_and_message):
        return self.attempt(get_repo_error=github_error(*status_and_message))


class DistinctFailureTests(FailedRefreshTestCase):
    def test_a_bad_token_is_reported_as_a_bad_token(self):
        for response in (BAD_CREDENTIALS, NOT_AUTHENTICATED, NO_ACCESS):
            with self.subTest(response):
                error = self.attempt_with_failing_repo(response)
                self.assertIsInstance(error, services.BadTokenError)

    def test_a_missing_repo_is_reported_as_a_missing_repo(self):
        error = self.attempt_with_failing_repo(NOT_FOUND)
        self.assertIsInstance(error, services.RepoNotFoundError)

    def test_a_rate_limit_is_reported_as_a_rate_limit(self):
        for response in (RATE_LIMITED, SECONDARY_RATE_LIMITED, TOO_MANY_REQUESTS):
            with self.subTest(response):
                error = self.attempt_with_failing_repo(response)
                self.assertIsInstance(error, services.RateLimitError)

    def test_the_three_failures_are_unrelated_classes_with_different_messages(self):
        errors = [
            self.attempt_with_failing_repo(response)
            for response in (BAD_CREDENTIALS, NOT_FOUND, RATE_LIMITED)
        ]
        kinds = [type(error) for error in errors]
        for kind in kinds:
            others = tuple(other for other in kinds if other is not kind)
            self.assertFalse(issubclass(kind, others), f"{kind} is a kind of {others}")
        self.assertEqual(len({str(error) for error in errors}), 3)

    def test_the_view_can_catch_every_failure_through_one_base_class(self):
        for response in (BAD_CREDENTIALS, NOT_FOUND, RATE_LIMITED, SERVER_ERROR):
            with self.subTest(response):
                error = self.attempt_with_failing_repo(response)
                self.assertIsInstance(error, services.IngestError)

    def test_any_other_github_error_is_none_of_the_three(self):
        error = self.attempt_with_failing_repo(SERVER_ERROR)
        self.assertIs(type(error), services.IngestError)

    def test_a_missing_token_is_reported_as_a_bad_token_without_calling_github(self):
        for token in (None, ""):
            with self.subTest(token=token):
                error = self.attempt(token=token)
                self.assertIsInstance(error, services.BadTokenError)
                self.github.assert_not_called()

    def test_the_bad_token_message_says_which_setting_to_fix(self):
        self.assertIn("GITHUB_TOKEN", str(self.attempt_with_failing_repo(BAD_CREDENTIALS)))

    def test_the_missing_repo_message_names_the_repo(self):
        self.assertIn(FULL_NAME, str(self.attempt_with_failing_repo(NOT_FOUND)))

    def test_the_rate_limit_message_says_it_is_a_rate_limit(self):
        self.assertIn("rate limit", str(self.attempt_with_failing_repo(RATE_LIMITED)).lower())

    def test_a_rate_limit_fails_straight_away_instead_of_waiting_for_it_to_reset(self):
        # PyGithub's default retry sleeps until the limit resets, up to an hour, with no cap.
        # The TA's request would hang, and the failure would never be reported.
        self.attempt_with_failing_repo(RATE_LIMITED)
        self.assertIn("retry", self.github.call_args.kwargs)
        self.assertIsNone(self.github.call_args.kwargs["retry"])

    def test_a_failure_after_the_run_has_started_is_reported_the_same_way(self):
        cases = (
            (BAD_CREDENTIALS, "BadTokenError"),
            (NOT_FOUND, "RepoNotFoundError"),
            (RATE_LIMITED, "RateLimitError"),
        )
        for response, expected in cases:
            with self.subTest(expected):
                github_repo = FakeGitHubRepo({})
                github_repo.get_pulls = raises(github_error(*response))
                error = self.attempt(github_repo)
                self.assertIsInstance(error, getattr(services, expected))


class ExistingDataUnchangedTests(FailedRefreshTestCase):
    """Every test starts from a stored week, then fails while refreshing it with changes."""

    def setUp(self):
        super().setUp()
        self.ingest(**one_of_each())
        self.before = self.stored_rows()
        self.error = github_error(*RATE_LIMITED)

    @staticmethod
    def stored_rows():
        return list(Artifact.objects.order_by("pk").values())

    def changed_activity(self):
        """A new commit, and edits to the stored PR, review and issue."""
        return {
            "branches": {"main": [fake_commit(sha("b")), fake_commit(sha("a"), message=EDITED)]},
            "pulls": [
                fake_pull(
                    1, title=EDITED, reviews=[fake_review(1, state="DISMISSED", body=EDITED)]
                ),
                fake_pull(3),
            ],
            "issues": [fake_issue(2, title=EDITED), fake_issue(4)],
        }

    def assert_unchanged_after(self, github_repo):
        self.attempt(github_repo)
        self.assertEqual(self.stored_rows(), self.before)

    def test_a_failure_before_anything_is_fetched(self):
        self.attempt(get_repo_error=self.error)
        self.assertEqual(self.stored_rows(), self.before)

    def test_a_failure_fetching_a_later_commits_files(self):
        activity = self.changed_activity()
        first, second = activity["branches"]["main"]
        activity["branches"]["main"] = [first, CommitWhoseFilesFail(second, self.error)]
        self.assert_unchanged_after(FakeGitHubRepo(**activity))

    def test_a_failure_listing_prs(self):
        github_repo = FakeGitHubRepo(**self.changed_activity())
        github_repo.get_pulls = raises(self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_on_a_later_page_of_prs(self):
        activity = self.changed_activity()
        github_repo = FakeGitHubRepo(**activity)
        github_repo.get_pulls = lambda **kwargs: then_raises(activity["pulls"][:1], self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_fetching_a_prs_files(self):
        activity = self.changed_activity()
        activity["pulls"][1].get_files = raises(self.error)
        self.assert_unchanged_after(FakeGitHubRepo(**activity))

    def test_a_failure_fetching_a_prs_reviews(self):
        activity = self.changed_activity()
        activity["pulls"][1].get_reviews = raises(self.error)
        self.assert_unchanged_after(FakeGitHubRepo(**activity))

    def test_a_failure_listing_issues(self):
        github_repo = FakeGitHubRepo(**self.changed_activity())
        github_repo.get_issues = raises(self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_on_a_later_page_of_issues(self):
        activity = self.changed_activity()
        github_repo = FakeGitHubRepo(**activity)
        github_repo.get_issues = lambda **kwargs: then_raises(activity["issues"][:1], self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_after_a_pr_has_moved_to_another_week(self):
        github_repo = FakeGitHubRepo(
            {}, pulls=[fake_pull(1, merged=NEXT_WEEK_MID, reviews=[fake_review(1)])]
        )
        github_repo.get_issues = raises(self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_after_co_authors_have_been_matched_again(self):
        partner = make_user("sample-partner")
        TeamMembership.objects.create(user=partner, team=self.team)
        trailer = f"Co-authored-by: Sample Partner <{partner.email}>"
        Artifact.objects.filter(artifact_id=COMMIT_ID).update(title=f"Pair on it\n\n{trailer}")
        self.before = self.stored_rows()

        github_repo = FakeGitHubRepo({})
        github_repo.get_pulls = raises(self.error)
        self.assert_unchanged_after(github_repo)

    def test_a_failure_that_is_not_from_github(self):
        # The transaction covers the whole run, whatever goes wrong in it.
        github_repo = FakeGitHubRepo(**self.changed_activity())
        github_repo.get_issues = raises(ConnectionError("The network went away."))
        self.assert_unchanged_after(github_repo)

    def test_a_refresh_that_succeeds_after_a_failure_stores_the_changes(self):
        github_repo = FakeGitHubRepo(**self.changed_activity())
        github_repo.get_issues = raises(self.error)
        self.attempt(github_repo)

        artifacts = self.ingest(**self.changed_activity())

        self.assert_ids(
            artifacts,
            *EVERY_ID,
            f"{FULL_NAME}:commit:{sha('b')}",
            pr_id(3),
            issue_id(4),
        )
        self.assertEqual(self.saved(pr_id(1)).title, EDITED)
        self.assertEqual(self.saved(review_id(1)).review_state, "DISMISSED")


class TokenSecrecyTests(FailedRefreshTestCase):
    """The fake errors echo the token, so only ingest can be what keeps it out."""

    EVERY_FAILURE = (BAD_CREDENTIALS, NOT_FOUND, RATE_LIMITED, NO_ACCESS, SERVER_ERROR)

    def assert_token_absent(self, error):
        for text in (
            str(error),
            repr(error),
            repr(error.args),
            repr(vars(error)),
            # What a log or a debug page would print, including any chained exception.
            "".join(traceback.format_exception(error)),
        ):
            self.assertNotIn(TOKEN, text)

    def attempt_with_failure_echoing_token(self, response):
        return self.attempt(get_repo_error=github_error(*response, echoing=TOKEN))

    def test_the_token_is_not_in_the_error_when_github_echoes_it(self):
        for response in self.EVERY_FAILURE:
            with self.subTest(response):
                self.assert_token_absent(self.attempt_with_failure_echoing_token(response))

    def test_the_token_is_not_in_the_error_when_a_later_request_echoes_it(self):
        for response in self.EVERY_FAILURE:
            with self.subTest(response):
                github_repo = FakeGitHubRepo({})
                github_repo.get_issues = raises(github_error(*response, echoing=TOKEN))
                self.assert_token_absent(self.attempt(github_repo))

    def test_the_token_is_not_logged(self):
        records = []
        handler = logging.Handler(level=logging.DEBUG)
        handler.emit = lambda record: records.append(logging.Formatter().format(record))
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        self.addCleanup(root.removeHandler, handler)
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)

        for response in self.EVERY_FAILURE:
            self.attempt_with_failure_echoing_token(response)

        self.assertNotIn(TOKEN, "\n".join(records))

    def test_the_token_comes_from_the_environment(self):
        with mock.patch("ingest.services.Auth") as auth:
            self.attempt_with_failing_repo(NOT_FOUND)
        auth.Token.assert_called_once_with(TOKEN)

    def test_the_token_is_never_saved_to_the_database(self):
        with (
            mock.patch("ingest.services.Github") as github,
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}),
        ):
            github.return_value.get_repo.return_value = FakeGitHubRepo(**one_of_each())
            services.ingest(self.repo, WEEK_START, WEEK_END)

        self.assertEqual(Artifact.objects.count(), len(EVERY_ID))
        stored = [*Artifact.objects.values(), *Repo.objects.values()]
        self.assertNotIn(TOKEN, repr(stored))
