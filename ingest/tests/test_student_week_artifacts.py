"""Tests for T-4 (#15): the student-week artifact lookup.

The lookup is imported per test rather than at module level, so each criterion
passes or fails on its own instead of the whole module failing on one ImportError.

Each exclusion test also creates an artifact that must be returned, so that it
can't pass just because the lookup returned nothing.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as django_timezone

from core.models import Course, Project, Repo, Team
from ingest.models import Artifact
from ingest.tests.test_sample_artifacts_fixture import (
    TARGET_STUDENT,
    TARGET_TEAM,
    TARGET_WEEK_START,
    load_fixture,
)

NEW_YORK = ZoneInfo("America/New_York")
ONE_MICROSECOND = timedelta(microseconds=1)

STUDENT = "sample-student"
TEAMMATE = "sample-teammate"
WEEK_START = date(2026, 10, 19)  # A Monday, with no clock change that week.
MID_WEEK = datetime(2026, 10, 21, 12, 0, tzinfo=NEW_YORK)


def lookup(user, team, week_start=WEEK_START):
    from ingest.services import student_week_artifacts

    return student_week_artifacts(user, team, week_start)


def make_user(github_login):
    return get_user_model().objects.create_user(
        username=github_login, email=f"{github_login}@example.com", github_login=github_login
    )


def make_team(project, name="Team A"):
    return Team.objects.create(project=project, name=name)


def make_project(course=None, name="Project 1"):
    course = course or Course.objects.create(name="CS 3240, Fall 2026")
    return Project.objects.create(course=course, name=name)


def add_repo(team, full_name="sample-org/app"):
    return Repo.objects.create(
        team=team, url=f"https://github.com/{full_name}", full_name=full_name
    )


class LookupTestCase(TestCase):
    """Compares by primary key, because an artifact_id can repeat across teams."""

    def setUp(self):
        self.student = make_user(STUDENT)
        self.team = make_team(make_project())
        self.repo = add_repo(self.team)
        self.next_number = 1

    def artifact(self, repo=None, type_="pr", timestamp=MID_WEEK, author_login=STUDENT, **fields):
        repo = repo or self.repo
        number = self.next_number
        self.next_number += 1
        return Artifact.objects.create(
            repo=repo,
            artifact_id=f"{repo.full_name}:{type_}:{number}",
            type=type_,
            url=f"https://github.com/{repo.full_name}/{type_}/{number}",
            timestamp=timestamp,
            author_login=author_login,
            **fields,
        )

    def assert_returns(self, expected, user=None, team=None, week_start=WEEK_START):
        # assertCountEqual also catches an artifact that's returned twice.
        self.assertCountEqual(
            [artifact.pk for artifact in lookup(user or self.student, team or self.team, week_start)],
            [artifact.pk for artifact in expected],
        )


class IncludedArtifactTests(LookupTestCase):
    def test_returns_each_type_the_student_authored(self):
        expected = [self.artifact(type_=type_) for type_ in ("commit", "pr", "review", "issue")]
        self.assert_returns(expected)

    def test_returns_a_commit_the_student_co_authored(self):
        commit = self.artifact(type_="commit", author_login=TEAMMATE, co_author_logins=[STUDENT])
        self.assert_returns([commit])

    def test_returns_a_co_authored_commit_with_no_linked_author(self):
        # The commit's own author is unattributed, but the co-author is still known.
        commit = self.artifact(type_="commit", author_login="", co_author_logins=[STUDENT])
        self.assert_returns([commit])

    def test_returns_a_review_where_the_student_is_the_reviewer(self):
        # A review artifact's author_login is the reviewer.
        review = self.artifact(type_="review", author_login=STUDENT)
        self.assert_returns([review])

    def test_returns_artifacts_from_all_of_the_teams_repos(self):
        second_repo = add_repo(self.team, "sample-org/docs")
        expected = [self.artifact(), self.artifact(repo=second_repo)]
        self.assert_returns(expected)

    def test_returns_an_artifact_once_when_it_credits_the_student_twice(self):
        commit = self.artifact(type_="commit", co_author_logins=[STUDENT, STUDENT.upper()])
        self.assert_returns([commit])


class ExcludedArtifactTests(LookupTestCase):
    def setUp(self):
        super().setUp()
        self.expected = self.artifact()

    def test_excludes_artifacts_outside_the_week(self):
        self.artifact(timestamp=MID_WEEK - timedelta(days=7))
        self.artifact(timestamp=MID_WEEK + timedelta(days=7))
        self.assert_returns([self.expected])

    def test_excludes_another_teams_repo(self):
        other_team = make_team(self.team.project, "Team B")
        self.artifact(repo=add_repo(other_team, "sample-org/other"))
        self.assert_returns([self.expected])

    def test_excludes_the_copy_held_by_a_later_project_reusing_the_repo(self):
        # Same GitHub repo and same artifact_id, but a different Repo row.
        later_project = make_project(self.team.project.course, "Project 2")
        later_repo = add_repo(make_team(later_project), self.repo.full_name)
        later_copy = Artifact.objects.create(
            repo=later_repo,
            artifact_id=self.expected.artifact_id,
            type=self.expected.type,
            url=self.expected.url,
            timestamp=self.expected.timestamp,
            author_login=STUDENT,
        )
        self.assert_returns([self.expected])
        self.assert_returns([later_copy], team=later_repo.team)

    def test_excludes_artifacts_by_other_people(self):
        self.artifact(author_login=TEAMMATE)
        self.artifact(type_="review", author_login=TEAMMATE)
        self.artifact(type_="commit", author_login=TEAMMATE, co_author_logins=["sample-other"])
        self.assert_returns([self.expected])

    def test_excludes_an_unattributed_commit(self):
        # Git recorded a matching name, but only a GitHub login earns credit.
        self.artifact(
            type_="commit",
            author_login="",
            git_author_name=STUDENT,
            git_author_email=f"{STUDENT}@example.com",
        )
        self.assert_returns([self.expected])

    def test_excludes_a_co_author_that_could_not_be_matched_to_a_login(self):
        self.artifact(
            type_="commit",
            author_login=TEAMMATE,
            unmatched_co_authors=[{"name": STUDENT, "email": f"{STUDENT}@example.com"}],
        )
        self.assert_returns([self.expected])

    def test_excludes_logins_that_only_contain_the_students_login(self):
        # Co-authors are stored as JSON, where a substring search would match these.
        for login in (f"{STUDENT}-2", f"x-{STUDENT}"):
            with self.subTest(login=login):
                self.artifact(author_login=login)
                self.artifact(type_="commit", author_login=TEAMMATE, co_author_logins=[login])
                self.assert_returns([self.expected])


class WeekBoundaryTests(LookupTestCase):
    def assert_week_boundaries(self, week_start, first_moment, first_moment_of_next_week):
        inside = [
            self.artifact(timestamp=first_moment),
            self.artifact(timestamp=first_moment_of_next_week - ONE_MICROSECOND),
        ]
        self.artifact(timestamp=first_moment - ONE_MICROSECOND)
        self.artifact(timestamp=first_moment_of_next_week)
        self.assert_returns(inside, week_start=week_start)

    def test_includes_the_first_and_last_moments_of_the_week(self):
        self.assert_week_boundaries(
            WEEK_START,
            datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc),  # Midnight in New York (EDT).
            datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc),
        )

    def test_boundaries_hold_when_the_clocks_go_back_during_the_week(self):
        # Clocks go back on Sunday, Nov 1, 2026, so this week is 169 hours long.
        self.assert_week_boundaries(
            date(2026, 10, 26),
            datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc),  # Midnight in EDT.
            datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc),  # Midnight in EST.
        )

    def test_boundaries_hold_when_the_clocks_go_forward_during_the_week(self):
        # Clocks go forward on Sunday, Mar 14, 2027, so this week is 167 hours long.
        self.assert_week_boundaries(
            date(2027, 3, 8),
            datetime(2027, 3, 8, 5, 0, tzinfo=timezone.utc),  # Midnight in EST.
            datetime(2027, 3, 15, 4, 0, tzinfo=timezone.utc),  # Midnight in EDT.
        )

    def test_week_stays_in_new_york_time_whatever_time_zone_is_active(self):
        first_moment = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        next_week = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)
        inside = [
            self.artifact(timestamp=first_moment),
            self.artifact(timestamp=next_week - ONE_MICROSECOND),
        ]
        self.artifact(timestamp=first_moment - ONE_MICROSECOND)
        self.artifact(timestamp=next_week)
        with django_timezone.override(ZoneInfo("Asia/Tokyo")):
            self.assert_returns(inside)


class LoginCaseTests(LookupTestCase):
    def test_author_login_matching_ignores_case(self):
        expected = [
            self.artifact(author_login=STUDENT.upper()),
            self.artifact(author_login=STUDENT.title()),
        ]
        self.assert_returns(expected)

    def test_reviewer_login_matching_ignores_case(self):
        review = self.artifact(type_="review", author_login=STUDENT.upper())
        self.assert_returns([review])

    def test_co_author_login_matching_ignores_case(self):
        commit = self.artifact(
            type_="commit", author_login=TEAMMATE, co_author_logins=[STUDENT.upper()]
        )
        self.assert_returns([commit])

    def test_matching_ignores_case_in_the_users_own_login(self):
        user = make_user("Sample-Mixed-Case")
        expected = [
            self.artifact(author_login="sample-mixed-case"),
            self.artifact(
                type_="commit", author_login=TEAMMATE, co_author_logins=["sample-mixed-case"]
            ),
        ]
        self.assert_returns(expected, user=user)


class SampleFixtureTests(TestCase):
    """The shared fixture is built so that each of its special cases tests one rule."""

    def test_returns_exactly_the_target_students_artifacts_for_the_target_week(self):
        load_fixture()
        team = Team.objects.get(name=TARGET_TEAM)
        student = get_user_model().objects.get(github_login=TARGET_STUDENT)

        found = lookup(student, team, TARGET_WEEK_START)

        repo = "sample-strive-org/team-a-app"
        self.assertCountEqual(
            [artifact.artifact_id for artifact in found],
            [
                f"{repo}:commit:{'a1' * 20}",  # Authored.
                f"{repo}:pr:1",  # Authored.
                f"{repo}:review:1001",  # Reviewer.
                f"{repo}:issue:3",  # 11:30pm on the last Sunday, already Monday in UTC.
                f"{repo}:commit:{'b2' * 20}",  # Co-authored.
            ],
        )
