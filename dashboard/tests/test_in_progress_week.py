"""Tests for generating the current week: its summaries are marked as partial. They count
toward the contribution shares on the team page, which say so, but not toward flags until
the week is generated again after it ends.

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
from summaries.models import Summary, ValidationRun

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_flag_rules import give
from .test_generate_summary import generate_url, page_messages, team_article

Role = Enrollment.Role
NEW_YORK = ZoneInfo("America/New_York")
WEDNESDAY = datetime(2026, 11, 4, 12, 0, tzinfo=NEW_YORK)
WEEK_ENDED = datetime(2026, 11, 9, 0, 0, tzinfo=NEW_YORK)
LAST_SECOND = datetime(2026, 11, 8, 23, 59, 59, tzinfo=NEW_YORK)
NEXT_TUESDAY = datetime(2026, 11, 10, 12, 0, tzinfo=NEW_YORK)
CURRENT = date(2026, 11, 2)
COMPLETED = date(2026, 10, 26)
NEXT = date(2026, 11, 9)


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


class PickingTheWeekTests(InProgressFixture):
    def setUp(self):
        super().setUp()
        self.set_clock(WEDNESDAY)
        self.ingest = mock.patch("ingest.services.ingest", return_value=[]).start()
        self.generate = mock.patch("summaries.services.generate_summary").start()
        self.generate.side_effect = lambda user, team, week_start: Summary.objects.get_or_create(
            team=team, user=user, week_start=week_start
        )[0]
        self.addCleanup(mock.patch.stopall)

    def post(self, week):
        return self.client.post(
            generate_url(self.team.id), {"week_start": week.isoformat()}, follow=True
        )

    def test_the_picker_allows_the_current_week_and_defaults_to_the_completed_one(self):
        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, f'value="{COMPLETED.isoformat()}"')
        self.assertContains(response, f'max="{CURRENT.isoformat()}"')

    def test_the_picker_only_allows_mondays(self):
        """A step of 7 days from a Monday is what makes browsers treat other days as invalid."""
        content = self.client.get(overview_url(self.course.id)).content.decode()

        picker = re.search(r'<input type="date" name="week_start"[^>]*>', content).group(0)
        self.assertIn('step="7"', picker)
        anchor = date.fromisoformat(re.search(r'min="([\d-]+)"', picker).group(1))
        self.assertEqual(anchor.weekday(), 0)
        self.assertLess(anchor, COMPLETED)

    def test_a_submitted_non_monday_is_still_rejected(self):
        message = page_messages(self.post(date(2026, 11, 3)))

        self.assertIn("The week must start on a Monday.", message)
        self.ingest.assert_not_called()

    def test_the_current_week_can_be_generated(self):
        self.post(CURRENT)

        self.ingest.assert_called()
        self.assertEqual(self.ingest.call_args.args[1:], (CURRENT, date(2026, 11, 8)))
        self.assertEqual(self.generate.call_count, 2)

    def test_the_message_says_the_week_is_in_progress(self):
        message = page_messages(self.post(CURRENT))

        self.assertIn("still in progress", message)

    def test_a_completed_weeks_message_doesnt(self):
        self.assertNotIn("in progress", page_messages(self.post(COMPLETED)))

    def test_a_week_that_hasnt_started_is_rejected_and_nothing_runs(self):
        message = page_messages(self.post(NEXT))

        self.assertIn("That week hasn't started yet.", message)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()


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


class FlagsAndSharesTests(InProgressFixture):
    def give_completed_week(self):
        """Ada did most of the completed week's work, so Grace gets a warning."""
        self.generated(self.ada, WEDNESDAY, COMPLETED, "major")
        self.generated(self.grace, WEDNESDAY, COMPLETED, "minor")

    def test_the_flag_rule_skips_a_partial_week_but_the_chart_doesnt(self):
        self.give_completed_week()
        self.generated(self.ada, WEDNESDAY, CURRENT, "minor")
        self.generated(self.grace, WEDNESDAY, CURRENT, "major")

        self.assertEqual(flags.requirement_shares(self.team)[0], COMPLETED)
        self.assertEqual(flags.requirement_shares(self.team, include_partial=True)[0], CURRENT)
    def test_requirement_shares_only_includes_a_partial_week_when_asked(self):
        self.generated(self.ada, WEDNESDAY, CURRENT, "major")
        self.generated(self.grace, WEDNESDAY, CURRENT, "minor")

        self.assertIsNone(flags.requirement_shares(self.team))
        self.assertEqual(flags.requirement_shares(self.team, include_partial=True)[0], CURRENT)
    def test_it_doesnt_count_once_the_week_ends_until_generated_again(self):
        self.give_completed_week()
        ada = self.generated(self.ada, WEDNESDAY, CURRENT, "major")
        grace = self.generated(self.grace, WEDNESDAY, CURRENT, "major")

        with at(NEXT_TUESDAY):
            self.assertEqual(flags.requirement_shares(self.team)[0], COMPLETED)
            run_on(ada)
            self.assertEqual(flags.requirement_shares(self.team)[0], COMPLETED)
            run_on(grace)
            self.assertEqual(flags.requirement_shares(self.team)[0], CURRENT)

    def team_page_with_a_partial_week(self, ada="major", grace="minor"):
        self.give_completed_week()
        flags.compute_flags(self.team)
        self.generated(self.ada, WEDNESDAY, CURRENT, ada)
        self.generated(self.grace, WEDNESDAY, CURRENT, grace)
        self.set_clock(WEDNESDAY)
        return self.client.get(team_url(self.team.id))

    def test_the_chart_shows_the_partial_week_and_says_so(self):
        response = self.team_page_with_a_partial_week()

        self.assertContains(response, "week of Nov 2 (in progress)")
        self.assertContains(response, "shares and any flags may change")
        self.assertEqual(response.context["share_week"], CURRENT)
        shares = {m.user_id: m.contribution_share for m in response.context["memberships"]}
        self.assertEqual(shares, {self.ada.id: 75, self.grace.id: 25})

    def test_the_chart_says_partial_once_the_week_has_ended(self):
        self.generated(self.ada, WEDNESDAY, CURRENT, "major")
        self.generated(self.grace, WEDNESDAY, CURRENT, "minor")
        self.set_clock(NEXT_TUESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, "week of Nov 2 (partial)")

    def test_a_complete_chart_has_no_note(self):
        self.give_completed_week()
        self.set_clock(WEDNESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, "work, week of Oct 26</h2>")
        self.assertNotContains(response, "shares and any flags may change")

    def test_a_student_missing_from_the_partial_week_keeps_the_chart_on_a_full_week(self):
        self.give_completed_week()
        self.generated(self.ada, WEDNESDAY, CURRENT, "major")
        self.set_clock(WEDNESDAY)

        response = self.client.get(team_url(self.team.id))

        self.assertEqual(response.context["share_week"], COMPLETED)

    def test_a_flag_from_another_week_doesnt_mark_the_partial_chart(self):
        """Grace's warning is about Oct 26, and she's at 75% in the week on the chart."""
        response = self.team_page_with_a_partial_week(ada="minor", grace="major")

        rows = {m.user_id: m for m in response.context["legend"]}
        self.assertIsNone(rows[self.grace.id].chart_flag)
        self.assertNotIn("var(--warn)", rows[self.grace.id].pie["color"])
        self.assertNotIn("var(--flag)", rows[self.grace.id].pie["color"])

    def test_that_flag_still_shows_on_the_students_card_naming_its_week(self):
        response = self.team_page_with_a_partial_week(ada="minor", grace="major")

        self.assertContains(response, "in the week of Oct 26")

    def test_a_flag_for_the_week_on_the_chart_still_marks_it(self):
        self.give_completed_week()
        flags.compute_flags(self.team)
        self.set_clock(WEDNESDAY)

        response = self.client.get(team_url(self.team.id))

        rows = {m.user_id: m for m in response.context["legend"]}
        self.assertEqual(rows[self.grace.id].chart_flag.severity, Flag.Severity.WARNING)
        self.assertEqual(rows[self.grace.id].pie["color"], "var(--warn)")

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
        self.assertContains(response, "so it may change")

    def test_summary_page_says_to_generate_a_stale_one_again(self):
        summary = self.generated(self.ada, WEDNESDAY)
        self.set_clock(NEXT_TUESDAY)

        response = self.summary_page(summary)

        self.assertContains(response, "before the week ended")
        self.assertContains(response, "Generate this week again")
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

    def test_a_warned_team_still_shows_its_warning(self):
        self.generated(self.ada, WEDNESDAY, COMPLETED, "major")
        self.generated(self.grace, WEDNESDAY, COMPLETED, "minor")
        flags.compute_flags(self.team)
        self.generated(self.ada, WEDNESDAY)
        self.set_clock(WEDNESDAY)

        self.assertIn("<mark>Warning</mark>", self.overview_card())


class DemoFlagsOnPartialWeeksTests(InProgressFixture):
    """Demo branch only: flags count an unfinished week as if it were complete."""

    def test_generating_an_unfinished_week_raises_its_flags(self):
        self.generated(self.ada, WEDNESDAY, CURRENT, "major", "major", "major")
        self.generated(self.grace, WEDNESDAY, CURRENT, "minor")

        raised = flags.compute_flags(self.team)

        self.assertEqual([(flag.user, flag.week_start) for flag in raised], [(self.grace, CURRENT)])
        self.assertEqual(raised[0].severity, Flag.Severity.FLAG)

    def test_the_overview_and_chart_show_it(self):
        self.generated(self.ada, WEDNESDAY, CURRENT, "major", "major", "major")
        self.generated(self.grace, WEDNESDAY, CURRENT, "minor")
        flags.compute_flags(self.team)
        self.set_clock(WEDNESDAY)

        card = self.client.get(overview_url(self.course.id)).content.decode()
        page = self.client.get(team_url(self.team.id))

        self.assertIn("<mark>Flag</mark>", team_article(card, self.team.name))
        rows = {m.user_id: m for m in page.context["legend"]}
        self.assertEqual(rows[self.grace.id].chart_flag.severity, Flag.Severity.FLAG)

    def test_nothing_says_flags_skip_the_week(self):
        summary = self.generated(self.ada, WEDNESDAY, CURRENT, "major")
        self.generated(self.grace, WEDNESDAY, CURRENT, "minor")
        self.set_clock(WEDNESDAY)

        for url in [team_url(self.team.id), reverse("dashboard:summary", args=[summary.id])]:
            with self.subTest(url=url):
                content = self.client.get(url).content.decode()
                self.assertNotIn("toward flags", content)
                self.assertNotIn("flags don't use them", content)
