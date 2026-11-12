"""Tests for when a summary counts as partial: written before its sprint ended, until it is
generated again. Each sprint here is a Monday-Sunday week (see week_shim). How a partial
summary is labelled is tested in test_sprint_generation and test_team_sprint.

The clock is fixed at Wednesday Nov 4, 2026, noon in New York, so the current sprint starts
Nov 2 and the most recent completed one starts Oct 26.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership
from summaries.models import Claim, Summary, ValidationRun
from summaries.tests.week_shim import sprint_for_week

from .test_course_overview import enroll, make_user

Role = Enrollment.Role
NEW_YORK = ZoneInfo("America/New_York")
WEDNESDAY = datetime(2026, 11, 4, 12, 0, tzinfo=NEW_YORK)
WEEK_ENDED = datetime(2026, 11, 9, 0, 0, tzinfo=NEW_YORK)
LAST_SECOND = datetime(2026, 11, 8, 23, 59, 59, tzinfo=NEW_YORK)
NEXT_TUESDAY = datetime(2026, 11, 10, 12, 0, tzinfo=NEW_YORK)
CURRENT = date(2026, 11, 2)
COMPLETED = date(2026, 10, 26)
NEXT = date(2026, 11, 9)


def give(student, team, *significances, week):
    """Give the student a summary for the week's sprint, with one verified, requirement-tied
    claim per significance."""
    sprint = sprint_for_week(team.project, week)
    summary, _ = Summary.objects.get_or_create(team=team, user=student, sprint=sprint)
    for significance in significances:
        Claim.objects.create(
            summary=summary, text=f"Sample {significance} work", category="feature",
            artifact_ids=[], requirement_ids=[1], significance=significance,
            status=Claim.Status.VERIFIED,
        )
    return summary


def at(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


def run_on(summary):
    """Log a generation of this summary, as generate_sprint_summary does each time."""
    return ValidationRun.objects.create(
        summary=summary, claims_total=0, claims_first_pass=0, claims_dropped=0, retries=0
    )


class InProgressFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        Repo.objects.create(
            team=cls.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

    def setUp(self):
        self.client.force_login(self.instructor)

    def set_clock(self, moment):
        patcher = at(moment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def generated(self, student, moment, week=CURRENT, *significances):
        """A summary for the week, generated at this moment."""
        with at(moment):
            summary = give(student, self.team, *(significances or ("major",)), week=week)
            run_on(summary)
        return summary


class IsPartialTests(InProgressFixture):
    def test_generated_during_the_week_is_partial(self):
        self.assertTrue(self.generated(self.ada, WEDNESDAY).is_partial)

    def test_it_stays_partial_after_the_week_ends_until_generated_again(self):
        summary = self.generated(self.ada, WEDNESDAY)

        with at(NEXT_TUESDAY):
            self.assertTrue(Summary.objects.get(pk=summary.pk).is_partial)

    def test_generating_again_after_the_week_ends_makes_it_complete(self):
        summary = self.generated(self.ada, WEDNESDAY)

        with at(NEXT_TUESDAY):
            run_on(summary)
            self.assertFalse(Summary.objects.get(pk=summary.pk).is_partial)

    def test_generating_again_during_the_week_leaves_it_partial(self):
        summary = self.generated(self.ada, WEDNESDAY)

        with at(LAST_SECOND):
            run_on(summary)

        self.assertTrue(Summary.objects.get(pk=summary.pk).is_partial)

    def test_the_week_ends_at_midnight_new_york_time(self):
        self.assertTrue(self.generated(self.ada, LAST_SECOND).is_partial)
        self.assertFalse(self.generated(self.grace, WEEK_ENDED).is_partial)

    def test_a_summary_with_no_run_goes_by_when_it_was_created(self):
        with at(WEDNESDAY):
            partial = give(self.ada, self.team, "major", week=CURRENT)
        with at(WEDNESDAY):
            complete = give(self.ada, self.team, "major", week=COMPLETED)

        self.assertTrue(partial.is_partial)
        self.assertFalse(complete.is_partial)

    def test_generated_at_is_the_latest_run(self):
        summary = self.generated(self.ada, WEDNESDAY)
        with at(NEXT_TUESDAY):
            run_on(summary)

        self.assertEqual(Summary.objects.get(pk=summary.pk).generated_at, NEXT_TUESDAY)
