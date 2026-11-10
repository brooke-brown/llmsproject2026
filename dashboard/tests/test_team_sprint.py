"""Tests for US-42 (#124): choose a sprint on the team page.

The team page has a sprint menu, a GET form, defaulting to the latest sprint the team has a
summary for. Flags, shares, the pie chart and each student's summary link are all for the
selected sprint.

The clock is fixed at Tuesday Nov 10, 2026, noon in New York (see test_sprint_flags). Sprint 1
(Oct 12-25) and Sprint 2 (Oct 26-Nov 1) have ended; Sprint 3 (Nov 2-15) is still running.
"""

from datetime import date, datetime

from django.test import TestCase

from core.models import Course, Enrollment, Project, Sprint, Team, TeamMembership
from dashboard.models import Flag, FlagDispute

from .test_course_overview import enroll, make_user, team_url
from .test_show_flags import article_containing
from .test_sprint_flags import AFTER_SPRINT_2, NEW_YORK, NOW, RULE, SprintFlagFixture
from .test_team_page import summary_url

Role = Enrollment.Role
# Nov 7, during Sprint 3: a summary written then covers through Nov 7.
DURING_SPRINT_3 = datetime(2026, 11, 7, 15, 0, tzinfo=NEW_YORK)


class TeamSprintFixture(SprintFlagFixture):
    def team_page(self, sprint=None, user=None, raw=None):
        self.client.force_login(user or self.instructor)
        query = {}
        if sprint is not None:
            query["sprint"] = sprint.id
        if raw is not None:
            query["sprint"] = raw
        return self.client.get(team_url(self.pair.id), query)

    def content(self, **kwargs):
        return self.team_page(**kwargs).content.decode()

    def shares(self, response):
        return {m.user_id: m.contribution_share for m in response.context["memberships"]}

    def give_sprint2(self):
        """A finished Sprint 2 where Grace did little, so she gets a warning."""
        self.give(self.ada, self.pair, self.sprint2, "major")
        self.give(self.grace, self.pair, self.sprint2, "minor")

    def give_sprint3(self, generated=DURING_SPRINT_3):
        """A running Sprint 3 where Ada did little."""
        self.give(self.ada, self.pair, self.sprint3, "minor", generated=generated)
        self.give(self.grace, self.pair, self.sprint3, "major", "major", generated=generated)


class SprintMenuTests(TeamSprintFixture):
    def test_the_page_has_a_sprint_menu_as_a_get_form(self):
        self.give_sprint2()

        content = self.content()

        select = content.index('<select name="sprint"')
        form = content[content.rindex("<form", 0, select) : content.index("</form>", select)]
        self.assertIn('method="get"', form)
        self.assertIn('<button type="submit"', form)
        for sprint in (self.sprint1, self.sprint2, self.sprint3):
            self.assertIn(f'<option value="{sprint.id}"', content)

    def test_the_menu_lists_only_started_sprints(self):
        later = Sprint.objects.create(
            project=self.project, name="Sprint 4", start_date=date(2026, 11, 16),
            end_date=date(2026, 11, 22),
        )

        self.assertNotIn(f'<option value="{later.id}"', self.content())

    def test_the_menu_lists_the_newest_sprint_first(self):
        content = self.content()

        self.assertLess(
            content.index(f'<option value="{self.sprint3.id}"'),
            content.index(f'<option value="{self.sprint1.id}"'),
        )

    def test_it_defaults_to_the_latest_sprint_with_summaries(self):
        self.give_sprint2()

        response = self.team_page()

        self.assertEqual(response.context["sprint"], self.sprint2)
        self.assertContains(response, f'<option value="{self.sprint2.id}" selected>')

    def test_a_running_sprint_with_summaries_is_the_default(self):
        self.give_sprint2()
        self.give_sprint3()

        self.assertEqual(self.team_page().context["sprint"], self.sprint3)

    def test_one_students_summary_is_enough_to_make_it_the_default(self):
        self.give_sprint2()
        self.give(self.ada, self.pair, self.sprint3, "major", generated=DURING_SPRINT_3)

        self.assertEqual(self.team_page().context["sprint"], self.sprint3)

    def test_another_teams_summaries_dont_change_the_default(self):
        self.give_sprint2()
        self.give(self.ada, self.team, self.sprint3, "major", generated=DURING_SPRINT_3)

        self.assertEqual(self.team_page().context["sprint"], self.sprint2)

    def test_with_no_summaries_it_defaults_to_the_running_sprint(self):
        self.assertEqual(self.team_page().context["sprint"], self.sprint3)

    def test_choosing_a_sprint_shows_it(self):
        self.give_sprint2()
        self.give_sprint3()

        response = self.team_page(sprint=self.sprint2)

        self.assertEqual(response.context["sprint"], self.sprint2)
        self.assertContains(response, f'<option value="{self.sprint2.id}" selected>')

    def test_a_sprint_from_another_project_falls_back_to_the_default(self):
        other = Project.objects.create(course=self.course, name="Project Two")
        elsewhere = Sprint.objects.create(
            project=other, name="Elsewhere", start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 7),
        )
        self.give_sprint2()

        response = self.team_page(sprint=elsewhere)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sprint"], self.sprint2)
        self.assertNotContains(response, "Elsewhere")

    def test_a_sprint_value_that_isnt_an_id_falls_back_to_the_default(self):
        self.give_sprint2()

        response = self.team_page(raw="not-a-sprint")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sprint"], self.sprint2)


class SelectedSprintSharesTests(TeamSprintFixture):
    def test_shares_and_chart_are_for_the_selected_sprint(self):
        self.give_sprint2()
        self.give_sprint3()

        sprint2 = self.team_page(sprint=self.sprint2)
        sprint3 = self.team_page(sprint=self.sprint3)

        self.assertEqual(self.shares(sprint2), {self.ada.id: 75, self.grace.id: 25})
        self.assertContains(sprint2, "requirement-tied work, Sprint 2")
        self.assertEqual(self.shares(sprint3), {self.ada.id: 14, self.grace.id: 86})
        self.assertContains(sprint3, "requirement-tied work, Sprint 3")

    def test_a_sprint_without_every_students_summary_has_no_shares_or_chart(self):
        self.give(self.ada, self.pair, self.sprint3, "major", generated=DURING_SPRINT_3)

        response = self.team_page(sprint=self.sprint3)

        self.assertEqual(self.shares(response), {self.ada.id: None, self.grace.id: None})
        self.assertNotContains(response, 'class="pie"')
        self.assertContains(response, "once every student has a summary for Sprint 3")

    def test_flags_are_for_the_selected_sprint(self):
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")
        self.give_sprint2()
        self.compute(self.pair)

        sprint1 = self.content(sprint=self.sprint1)
        sprint2 = self.content(sprint=self.sprint2)

        self.assertIn("Ada Sample did 14% of the team's requirement-tied work in Sprint 1", sprint1)
        self.assertNotIn("work in Sprint 2", sprint1)
        self.assertIn("Grace Example did 25% of the team's requirement-tied work in Sprint 2", sprint2)
        self.assertNotIn("work in Sprint 1", sprint2)

    def test_the_chart_marks_only_the_selected_sprints_flags(self):
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")
        self.give_sprint2()
        self.compute(self.pair)

        legend = {m.user_id: m for m in self.team_page(sprint=self.sprint1).context["legend"]}

        self.assertEqual(legend[self.ada.id].chart_flag.severity, Flag.Severity.FLAG)
        self.assertIsNone(legend[self.grace.id].chart_flag)

    def test_a_dispute_shows_only_with_its_sprint(self):
        self.give_sprint2()
        self.compute(self.pair)
        FlagDispute.objects.create(
            team=self.pair, student=self.grace, rule=RULE, sprint=self.sprint2,
            explanation="I paired on most of Sprint 2.",
        )

        self.assertIn("I paired on most of Sprint 2.", self.content(sprint=self.sprint2))
        self.assertNotIn("I paired on most of Sprint 2.", self.content(sprint=self.sprint1))

    def test_a_running_sprint_shows_shares_but_no_flags(self):
        self.give_sprint3()
        self.compute(self.pair)

        response = self.team_page(sprint=self.sprint3)

        self.assertEqual(self.shares(response), {self.ada.id: 14, self.grace.id: 86})
        self.assertNotContains(response, "<mark>Flag</mark>")
        self.assertNotContains(response, "<mark>Warning</mark>")


class InProgressLabelTests(TeamSprintFixture):
    def test_a_running_sprint_is_in_progress_through_its_summaries_date(self):
        self.give_sprint3()

        response = self.team_page(sprint=self.sprint3)

        self.assertContains(response, "Sprint 3 (in progress, through Nov 7)")
        self.assertContains(response, "flags don't use them")

    def test_the_earliest_date_any_summary_covers_through_is_shown(self):
        self.give(self.ada, self.pair, self.sprint3, "major", generated=DURING_SPRINT_3)
        self.give(self.grace, self.pair, self.sprint3, "major", generated=NOW)

        self.assertContains(self.team_page(sprint=self.sprint3), "in progress, through Nov 7")

    def test_an_ended_sprint_with_a_partial_summary_is_in_progress_too(self):
        self.give(self.ada, self.pair, self.sprint2, "major", generated=datetime(
            2026, 10, 30, 9, 0, tzinfo=NEW_YORK))
        self.give(self.grace, self.pair, self.sprint2, "minor")

        response = self.team_page(sprint=self.sprint2)

        self.assertContains(response, "Sprint 2 (in progress, through Oct 30)")
        self.assertEqual(self.shares(response), {self.ada.id: 75, self.grace.id: 25})

    def test_a_complete_sprint_has_no_in_progress_label(self):
        self.give_sprint2()

        response = self.team_page(sprint=self.sprint2)

        self.assertContains(response, "requirement-tied work, Sprint 2</h2>")
        self.assertNotContains(response, "in progress")

    def test_a_students_summary_pill_says_in_progress_with_its_date(self):
        self.give_sprint3()

        article = article_containing(self.content(sprint=self.sprint3), "Ada Sample")

        self.assertIn("in progress, through Nov 7", article)


class StudentSummaryLinkTests(TeamSprintFixture):
    def test_each_students_link_is_their_summary_for_the_selected_sprint(self):
        ada2 = self.give(self.ada, self.pair, self.sprint2, "major")
        self.give(self.grace, self.pair, self.sprint2, "minor")
        ada3 = self.give(self.ada, self.pair, self.sprint3, "major", generated=DURING_SPRINT_3)

        content = self.content(sprint=self.sprint2)

        self.assertIn(f'<a href="{summary_url(ada2.id)}">Ada Sample</a>', content)
        self.assertNotIn(f'href="{summary_url(ada3.id)}"', content)

    def test_a_student_with_no_summary_for_the_sprint_shows_not_generated(self):
        self.give(self.ada, self.pair, self.sprint3, "major", generated=DURING_SPRINT_3)
        self.give(self.grace, self.pair, self.sprint2, "minor")

        content = self.content(sprint=self.sprint3)

        grace = article_containing(content, "Grace Example")
        self.assertIn("Not generated", grace)
        self.assertIn("<strong>Grace Example</strong>", grace)
        self.assertNotIn("Not generated", article_containing(content, "Ada Sample"))


class NoSprintsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, course, Role.INSTRUCTOR)
        project = Project.objects.create(course=course, name="Project One")
        cls.team = Team.objects.create(project=project, name="Team Alpha")
        student = make_user("sample-ada", "Ada", "Sample")
        enroll(student, course, Role.STUDENT)
        TeamMembership.objects.create(user=student, team=cls.team)

    def test_a_project_with_no_sprints_says_so(self):
        self.client.force_login(self.instructor)

        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, "No sprints yet")
        self.assertNotContains(response, '<select name="sprint"')
        self.assertIsNone(response.context["sprint"])


class AccessTests(TeamSprintFixture):
    def test_a_ta_can_choose_a_sprint(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.give_sprint2()

        response = self.team_page(sprint=self.sprint2, user=ta)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sprint"], self.sprint2)

    def test_a_student_on_the_team_gets_403(self):
        self.assertEqual(self.team_page(sprint=self.sprint2, user=self.ada).status_code, 403)

    def test_an_instructor_of_another_course_gets_403(self):
        other = make_user("sample-other-instructor")
        enroll(other, Course.objects.create(name="CS 2100"), Role.INSTRUCTOR)

        self.assertEqual(self.team_page(sprint=self.sprint2, user=other).status_code, 403)
