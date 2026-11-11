"""Tests for US-6 (#22): identifying commits that match no one on the team.

These reuse the US-2 fakes, so nothing here touches the network. `unattributed_commits`
is imported per test rather than at module level, so each criterion passes or fails on
its own.

Each test that says a commit isn't unattributed or isn't credited also expects something
positive, so that it can't pass just because the query returned nothing. The exception is
LoginFixTests.test_the_same_week_ingest_credits_a_co_author_after_the_fix, which guards
behavior that ingest already has.
"""

from datetime import date, datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.urls import reverse

from ingest.models import Artifact
from ingest.tests.test_co_author_credit import (
    PARTNER,
    PARTNER_TRAILER,
    CoAuthorTestCase,
    message_with,
)
from ingest.tests.test_ingest_commits import (
    FIRST_MOMENT,
    FULL_NAME,
    MID_WEEK,
    NEXT_WEEK,
    STUDENT,
    WEEK_START,
    fake_commit,
    sha,
)
from ingest.tests.test_student_artifacts import (
    NEW_YORK,
    ONE_MICROSECOND,
    add_repo,
    lookup,
    make_team,
)

STUDENT_NAME = "Sample Student"
STUDENT_EMAIL = "student.account@example.com"
WRONG_LOGIN = "sample-studnet"
STRANGER = "sample-stranger"
BOTS = ("dependabot[bot]", "github-actions[bot]")
GIT_NAME = "Laptop User"
GIT_EMAIL = "laptop.user@example.com"
FOLLOWING_MONDAY = date(2026, 10, 26)
FOLLOWING_SUNDAY = date(2026, 11, 1)


def unattributed(repo, week_start=WEEK_START):
    from ingest.services import unattributed_commits

    return unattributed_commits(repo, week_start, week_start + timedelta(days=6))


def commit_by(character, login=None, name=GIT_NAME, email=GIT_EMAIL, message="Add the task model"):
    """A commit with this git identity. With no login, its email has no linked account."""
    commit = fake_commit(sha(character), login=login, message=message)
    commit.commit.author.name = name
    commit.commit.author.email = email
    return commit


def commit_id(character):
    return f"{FULL_NAME}:commit:{sha(character)}"


class UnattributedTestCase(CoAuthorTestCase):
    """Starts with STUDENT, PARTNER and TEAMMATE on the team."""

    def setUp(self):
        super().setUp()
        self.student = self.add_student(STUDENT, STUDENT_EMAIL)

    def stored_commit(self, number, repo=None, timestamp=MID_WEEK, author_login="", **fields):
        repo = repo or self.repo
        return Artifact.objects.create(
            repo=repo,
            artifact_id=f"{repo.full_name}:commit:{number}",
            type=Artifact.Type.COMMIT,
            url=f"https://github.com/{repo.full_name}/commit/{number}",
            timestamp=timestamp,
            author_login=author_login,
            **fields,
        )

    def assert_unattributed(self, *characters, repo=None, week_start=WEEK_START):
        # assertCountEqual also catches a commit that's returned twice.
        self.assertCountEqual(
            [artifact.artifact_id for artifact in unattributed(repo or self.repo, week_start)],
            [commit_id(character) for character in characters],
        )

    def assert_returns(self, expected, repo=None, week_start=WEEK_START):
        """Compares by primary key, because an artifact_id can repeat across teams."""
        self.assertCountEqual(
            [artifact.pk for artifact in unattributed(repo or self.repo, week_start)],
            [artifact.pk for artifact in expected],
        )

    def credited_ids(self, user, week_start=WEEK_START):
        return [artifact.artifact_id for artifact in lookup(user, self.team, week_start)]


class WhatCountsAsUnattributedTests(UnattributedTestCase):
    def test_a_commit_with_no_github_login_is_unattributed(self):
        self.ingest({"main": [commit_by("a", login=None), commit_by("b", login=STUDENT)]})
        self.assert_unattributed("a")

    def test_a_commit_whose_login_is_not_on_the_team_is_unattributed(self):
        self.ingest({"main": [commit_by("a", login=STRANGER), commit_by("b", login=STUDENT)]})
        self.assert_unattributed("a")

    def test_a_bots_commit_is_unattributed(self):
        self.ingest(
            {
                "main": [
                    commit_by("a", login=BOTS[0]),
                    commit_by("b", login=BOTS[1]),
                    commit_by("c", login=STUDENT),
                ]
            }
        )
        self.assert_unattributed("a", "b")

    def test_a_student_on_another_team_is_not_on_this_team(self):
        other_team = make_team(self.project, name="Team B")
        self.add_student("sample-other-student", "other@example.com", team=other_team)
        self.ingest(
            {"main": [commit_by("a", login="sample-other-student"), commit_by("b", login=STUDENT)]}
        )
        self.assert_unattributed("a")

    def test_every_team_member_is_attributed(self):
        self.ingest(
            {
                "main": [
                    commit_by("a", login=STUDENT),
                    commit_by("b", login=PARTNER),
                    commit_by("c", login=STRANGER),
                ]
            }
        )
        self.assert_unattributed("c")

    def test_logins_are_compared_ignoring_case(self):
        # GitHub logins aren't case-sensitive, so this is the student.
        self.ingest({"main": [commit_by("a", login=STUDENT.upper()), commit_by("b")]})
        self.assert_unattributed("b")

    def test_a_co_author_does_not_make_the_commit_attributed(self):
        # The criterion is about the commit's author. The co-author's credit is tested below.
        message = message_with(PARTNER_TRAILER)
        self.ingest(
            {
                "main": [
                    commit_by("a", login=None, message=message),
                    commit_by("b", login=STRANGER, message=message),
                    commit_by("c", login=STUDENT, message=message),
                ]
            }
        )
        self.assert_unattributed("a", "b")

    def test_only_commits_are_returned(self):
        commit = self.stored_commit(1)
        for number, type_ in enumerate(("pr", "review", "issue"), start=2):
            self.stored_artifact(number, type_=type_)
        self.assert_returns([commit])


class GitIdentityTests(UnattributedTestCase):
    def assert_keeps_git_identity(self, login, name=GIT_NAME, email=GIT_EMAIL):
        self.ingest({"main": [commit_by("a", login=login, name=name, email=email)]})
        [artifact] = unattributed(self.repo)
        self.assertEqual(artifact.git_author_name, name)
        self.assertEqual(artifact.git_author_email, email)

    def test_keeps_the_git_name_and_email_of_a_commit_with_no_login(self):
        self.assert_keeps_git_identity(login=None)

    def test_keeps_the_git_name_and_email_of_a_login_not_on_the_team(self):
        self.assert_keeps_git_identity(login=STRANGER)

    def test_keeps_the_git_name_and_email_of_a_bot(self):
        self.assert_keeps_git_identity(
            login=BOTS[0],
            name=BOTS[0],
            email=f"49699333+{BOTS[0]}@users.noreply.github.com",
        )

    def test_keeps_what_git_recorded_even_if_it_is_not_a_valid_address(self):
        self.assert_keeps_git_identity(login=None, name="root", email="root@localhost")

    def test_ingesting_again_keeps_the_git_name_and_email(self):
        commit = commit_by("a", login=None)
        self.ingest({"main": [commit]})
        self.ingest({"main": [commit]})
        [artifact] = unattributed(self.repo)
        self.assertEqual(artifact.git_author_name, GIT_NAME)
        self.assertEqual(artifact.git_author_email, GIT_EMAIL)


class CreditTests(UnattributedTestCase):
    def assert_unattributed_and_uncredited(self, commit):
        self.ingest({"main": [commit, commit_by("b", login=STUDENT)]})
        self.assert_unattributed("a")
        # The student's own commit shows the lookup can return something.
        self.assertEqual(self.credited_ids(self.student), [commit_id("b")])

    def test_a_git_email_matching_a_student_earns_no_credit(self):
        self.assert_unattributed_and_uncredited(commit_by("a", email=STUDENT_EMAIL))

    def test_a_git_name_matching_a_student_earns_no_credit(self):
        self.assert_unattributed_and_uncredited(commit_by("a", name=STUDENT_NAME))

    def test_a_noreply_git_email_naming_a_student_earns_no_credit(self):
        # With no account linked, GitHub isn't vouching for this address.
        self.assert_unattributed_and_uncredited(
            commit_by("a", email=f"123+{STUDENT}@users.noreply.github.com")
        )

    def test_a_git_identity_matching_a_student_earns_no_credit_with_another_login(self):
        self.assert_unattributed_and_uncredited(
            commit_by("a", login=STRANGER, name=STUDENT_NAME, email=STUDENT_EMAIL)
        )

    def test_a_student_co_author_is_credited_and_no_one_else_is(self):
        self.ingest({"main": [commit_by("a", message=message_with(PARTNER_TRAILER))]})
        self.assert_unattributed("a")
        self.assertEqual(self.credited_ids(self.partner), [commit_id("a")])
        self.assertEqual(self.credited_ids(self.student), [])
        self.assertEqual(self.credited_ids(self.teammate), [])


class LoginFixTests(UnattributedTestCase):
    """A wrong login is fixed in admin, which saves the User."""

    def setUp(self):
        super().setUp()
        self.mistyped = self.add_student(WRONG_LOGIN, "mistyped@example.com")
        self.correct_login = "sample-mistyped"

    def fix_login(self):
        self.mistyped.github_login = self.correct_login
        self.mistyped.save()

    def noreply_trailer(self):
        return f"Co-authored-by: Sample Mistyped <123+{self.correct_login}@users.noreply.github.com>"

    def ingest_following_week(self):
        """Fetches nothing from the original week, since those commits are too old to list."""
        self.ingest({"main": []}, week_start=FOLLOWING_MONDAY, week_end=FOLLOWING_SUNDAY)

    def test_the_next_ingest_of_that_week_attributes_the_commit(self):
        branches = {"main": [commit_by("a", login=self.correct_login), commit_by("b")]}
        self.ingest(branches)
        self.assert_unattributed("a", "b")

        self.fix_login()
        self.ingest(branches)

        self.assert_unattributed("b")
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

    def test_the_next_ingest_of_a_later_week_attributes_the_commit(self):
        self.ingest({"main": [commit_by("a", login=self.correct_login), commit_by("b")]})
        self.assert_unattributed("a", "b")

        self.fix_login()
        self.ingest_following_week()

        self.assert_unattributed("b")
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

    def test_a_fix_takes_credit_away_from_a_login_that_was_someone_elses(self):
        # The wrong login is a real account, so that person's work looked like this student's.
        branches = {"main": [commit_by("a", login=WRONG_LOGIN), commit_by("b", login=STUDENT)]}
        self.ingest(branches)
        self.assert_unattributed()
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

        self.fix_login()
        self.ingest(branches)

        self.assert_unattributed("a")
        self.assertEqual(self.credited_ids(self.mistyped), [])

    def test_the_same_week_ingest_credits_a_co_author_after_the_fix(self):
        branches = {"main": [fake_commit(sha("a"), message=message_with(self.noreply_trailer()))]}
        self.ingest(branches)
        self.assertEqual(self.credited_ids(self.mistyped), [])

        self.fix_login()
        self.ingest(branches)

        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

    def test_a_later_ingest_credits_a_co_author_found_by_their_corrected_login(self):
        branches = {"main": [fake_commit(sha("a"), message=message_with(self.noreply_trailer()))]}
        self.ingest(branches)
        self.assertEqual(self.credited_ids(self.mistyped), [])

        self.fix_login()
        self.ingest_following_week()

        artifact = Artifact.objects.get(artifact_id=commit_id("a"))
        self.assertEqual(artifact.co_author_logins, [self.correct_login])
        self.assertEqual(artifact.unmatched_co_authors, [])
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

    def test_a_later_ingest_replaces_a_co_authors_wrong_login(self):
        # Found by email, so the wrong login is what the commit's credit was stored under.
        trailer = "Co-authored-by: Sample Mistyped <mistyped@example.com>"
        self.ingest({"main": [fake_commit(sha("a"), message=message_with(trailer))]})
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])

        self.fix_login()
        self.ingest_following_week()

        artifact = Artifact.objects.get(artifact_id=commit_id("a"))
        self.assertEqual(artifact.co_author_logins, [self.correct_login])
        self.assertEqual(self.credited_ids(self.mistyped), [commit_id("a")])


class QueryByRepoAndWeekTests(UnattributedTestCase):
    def test_returns_only_this_repos_commits(self):
        second_repo = add_repo(self.team, "sample-org/api")
        here = self.stored_commit(1)
        there = self.stored_commit(2, repo=second_repo)
        self.assert_returns([here])
        self.assert_returns([there], repo=second_repo)

    def test_tells_apart_two_teams_copies_of_one_github_repo(self):
        # A later project reusing a GitHub repo has its own Repo row and copies.
        other_team = make_team(self.project, name="Team B")
        other_repo = add_repo(other_team, FULL_NAME)
        self.add_student("sample-other-student", "other@example.com", team=other_team)
        here = self.stored_commit(1, author_login="sample-other-student")
        self.stored_commit(1, repo=other_repo, author_login="sample-other-student")
        there = self.stored_commit(2, repo=other_repo, author_login=STUDENT)

        # Each login is on one team only, so each copy is judged by its own team.
        self.assert_returns([here])
        self.assert_returns([there], repo=other_repo)

    def test_the_week_runs_monday_to_sunday_in_new_york(self):
        first = self.stored_commit(1, timestamp=FIRST_MOMENT)
        last = self.stored_commit(2, timestamp=NEXT_WEEK - ONE_MICROSECOND)
        self.stored_commit(3, timestamp=FIRST_MOMENT - ONE_MICROSECOND)
        self.stored_commit(4, timestamp=NEXT_WEEK)
        self.assert_returns([first, last])

    def test_a_week_with_a_clock_change_ends_at_midnight_in_new_york(self):
        # Clocks go back on Sunday, Nov 1, so this week is an hour longer than 168 hours.
        last = self.stored_commit(
            1, timestamp=datetime(2026, 11, 1, 23, 59, 59, tzinfo=NEW_YORK)
        )
        self.stored_commit(2, timestamp=datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc))
        self.assert_returns([last], week_start=FOLLOWING_MONDAY)


class AdminTests(UnattributedTestCase):
    """The alpha pages don't show unattributed commits, so instructors find them in admin."""

    def setUp(self):
        super().setUp()
        admin_user = get_user_model().objects.create_superuser(
            username="sample-admin",
            email="admin@example.com",
            password="sample-password",
            github_login="sample-admin",
        )
        self.client.force_login(admin_user)
        self.stored_commit(1, git_author_name=GIT_NAME, git_author_email=GIT_EMAIL)
        self.stored_commit(
            2,
            author_login=STRANGER,
            git_author_name="Stranger Name",
            git_author_email="stranger.name@example.com",
        )
        self.stored_commit(
            3,
            author_login=STUDENT,
            git_author_name="Attributed Name",
            git_author_email="attributed.name@example.com",
        )

    def changelist(self, **params):
        return self.client.get(reverse("admin:ingest_artifact_changelist"), params)

    def test_the_artifact_list_shows_git_names_and_emails(self):
        response = self.changelist()
        self.assertEqual(response.status_code, 200)
        for text in (GIT_NAME, GIT_EMAIL, "Attributed Name", "attributed.name@example.com"):
            self.assertContains(response, text)

    def test_the_list_filters_to_unattributed_commits(self):
        response = self.changelist(attribution="unattributed")
        self.assertEqual(response.status_code, 200)
        for text in (GIT_NAME, GIT_EMAIL, "Stranger Name", "stranger.name@example.com"):
            self.assertContains(response, text)
        self.assertNotContains(response, "Attributed Name")

    def test_the_list_can_be_narrowed_to_a_repo(self):
        second_repo = add_repo(self.team, "sample-org/api")
        self.stored_commit(4, repo=second_repo, git_author_name="Other Repo Name")
        response = self.changelist(attribution="unattributed", repo__id__exact=self.repo.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GIT_NAME)
        self.assertNotContains(response, "Other Repo Name")
