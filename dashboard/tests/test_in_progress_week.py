"""Tests for how a partial weekly summary is labelled and what it says. Weekly summaries are
the alpha's, and no new ones can be generated (T-21): these only keep the pages that still
show them honest until PR B deletes them. Generating by sprint, and its in-progress wording,
is tested in test_sprint_generation, and flags and shares follow sprints (US-41).

The clock is fixed at Wednesday Nov 4, 2026, noon in New York, so the current week starts
Nov 2 and the most recent completed week starts Oct 26.
"""

import re
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from dashboard import flags
from dashboard.models import Flag
from summaries.models import Claim, Summary, ValidationRun

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_generate_summary import team_article

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
    """Give the student a summary for the week, with one verified, requirement-tied claim per
    significance."""
    summary, _ = Summary.objects.get_or_create(team=team, user=student, week_start=week)
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
    """Log a generation of this summary, as generate_summary does each time."""
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
        Requirement.objects.create(project=cls.project, number=1, text="Sample requirement")
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


class LabelTests(InProgressFixture):
    def summary_page(self, summary):
        return self.client.get(reverse("dashboard:summary", args=[summary.id]))

    def test_team_page_labels_a_summary_in_a_running_week(self):
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        self.assertContains(self.client.get(team_url(self.team.id)), "in progress")

    def test_team_page_labels_a_stale_one_partial(self):
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(NEXT_TUESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, "partial")
        self.assertNotContains(response, "in progress")

    def test_team_page_doesnt_label_a_complete_summary(self):
        self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.set_clock(WEDNESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertNotContains(response, "in progress")
        self.assertNotContains(response, "partial")

    def test_summary_page_explains_a_running_week(self):
        summary = self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        response = self.summary_page(summary)

        self.assertContains(response, "This week is still in progress")
        self.assertContains(response, "Nov 4")
        self.assertContains(response, "doesn't count toward flags until")
        self.assertNotContains(response, "or shares")

    def test_summary_page_says_to_generate_a_stale_one_again(self):
        summary = self.generated(self.ada, WEDNESDAY)
        self.set_clock(NEXT_TUESDAY)

        response = self.summary_page(summary)

        self.assertContains(response, "before the week ended")
        self.assertContains(response, "Generate this week again")
        self.assertContains(response, "count it toward flags.")
        self.assertNotContains(response, "still in progress")

    def test_summary_page_says_nothing_for_a_complete_summary(self):
        summary = self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.set_clock(WEDNESDAY)

        response = self.summary_page(summary)

        self.assertNotContains(response, "in progress")
        self.assertNotContains(response, "before the week ended")

    def test_week_switcher_labels_partial_weeks(self):
        self.generated(self.ada, WEDNESDAY)
        complete = self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.set_clock(WEDNESDAY)

        self.assertContains(self.summary_page(complete), "in progress")

    def test_the_note_uses_new_york_time_for_when_it_was_generated(self):
        late = datetime(2026, 11, 4, 23, 30, tzinfo=NEW_YORK)
        summary = self.generated(self.ada, late)
        self.set_clock(late)

        self.assertContains(self.summary_page(summary), "Nov 4")


class TeamStatusTests(InProgressFixture):
    def overview_card(self):
        content = self.client.get(overview_url(self.course.id)).content.decode()
        return team_article(content, self.team.name)

    def test_overview_says_in_progress_rather_than_no_flags(self):
        """Nothing has been checked yet, so "No flags" would read as an all-clear."""
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        card = self.overview_card()

        self.assertIn("In progress", card)
        self.assertNotIn("No flags", card)

    def test_team_page_says_in_progress_rather_than_no_flags(self):
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, "In progress")
        self.assertNotContains(response, "No flags")

    def test_a_completed_week_still_says_no_flags(self):
        self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        card = self.overview_card()

        self.assertIn("No flags", card)
        self.assertNotIn(">In progress<", card)

    def test_latest_week_on_the_overview_is_marked_when_partial(self):
        self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        self.assertIn("Latest week: Nov 2 (in progress)", self.overview_card())

    def test_latest_week_says_partial_once_the_week_has_ended(self):
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(NEXT_TUESDAY)

        self.assertIn("Latest week: Nov 2 (partial)", self.overview_card())

    def test_latest_week_isnt_marked_when_complete(self):
        self.generated(self.ada, WEDNESDAY, COMPLETED)
        self.set_clock(WEDNESDAY)

        self.assertRegex(self.overview_card(), r"Latest week: Oct 26\s*<")
