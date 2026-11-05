"""Tests for US-23 (#87): show students by name, without their GitHub username repeated.

Everywhere except the summary page heading, a student appears as their name only (or their
username once, if they have no name). The summary heading shows the name in bold with the
username below it. The `User.display_name` property is tested in core/tests.
"""

import re
from datetime import date

from django.test import TestCase

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard import flags
from dashboard.services import SummaryFailure, TeamGeneration
from summaries.models import Summary

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_flag_rules import WEEK, give
from .test_summary_page import summary_url

Role = Enrollment.Role


class DisplayNameFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=project, name="Team Alpha")

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.first_only = make_user("sample-first", first_name="Linus")
        cls.last_only = make_user("sample-last", last_name="Hopper")
        cls.nameless = make_user("sample-nameless")
        cls.students = [cls.ada, cls.first_only, cls.last_only, cls.nameless]
        for student in cls.students:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

    def get(self, url):
        self.client.force_login(self.instructor)
        return self.client.get(url).content.decode()

    def assert_name_only(self, content, student, shown):
        self.assertIn(shown, content)
        self.assertNotIn(student.github_login, content)


class CourseOverviewNameTests(DisplayNameFixture):
    def content(self):
        return self.get(overview_url(self.course.id))

    def test_a_student_with_a_full_name_shows_the_name_without_the_username(self):
        self.assert_name_only(self.content(), self.ada, "Ada Sample")

    def test_a_student_with_only_a_first_name_shows_it_not_the_username(self):
        self.assert_name_only(self.content(), self.first_only, "Linus")

    def test_a_student_with_only_a_last_name_shows_it_not_the_username(self):
        self.assert_name_only(self.content(), self.last_only, "Hopper")

    def test_a_student_with_no_name_shows_the_username_once(self):
        self.assertEqual(self.content().count("sample-nameless"), 1)


class TeamPageNameTests(DisplayNameFixture):
    def content(self):
        return self.get(team_url(self.team.id))

    def test_a_student_with_a_full_name_shows_the_name_without_the_username(self):
        self.assert_name_only(self.content(), self.ada, "Ada Sample")

    def test_a_student_with_only_a_first_name_shows_it_not_the_username(self):
        self.assert_name_only(self.content(), self.first_only, "Linus")

    def test_a_student_with_only_a_last_name_shows_it_not_the_username(self):
        self.assert_name_only(self.content(), self.last_only, "Hopper")

    def test_a_student_with_no_name_shows_the_username_once(self):
        self.assertEqual(self.content().count("sample-nameless"), 1)

    def test_the_contribution_chart_shows_names_without_usernames(self):
        for student in [self.ada, self.first_only]:
            give(student, self.team, "major")
        for student in [self.last_only, self.nameless]:
            give(student, self.team, "minor")

        content = self.content()

        for student in [self.ada, self.first_only, self.last_only]:
            self.assertNotIn(student.github_login, content)


class SummaryPageNameTests(DisplayNameFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.summaries = {
            student: Summary.objects.create(
                team=cls.team, user=student, week_start=date(2026, 10, 26)
            )
            for student in cls.students
        }

    def content(self, student):
        return self.get(summary_url(self.summaries[student].id))

    def section(self, content, pattern):
        return re.search(pattern, content, re.DOTALL).group(1)

    def title(self, student):
        return self.section(self.content(student), r"<title>(.*?)</title>")

    def crumb(self, student):
        return self.section(self.content(student), r'class="crumb">(.*?)</span>\s*</div>')

    def heading(self, student):
        """The page heading and anything directly below it."""
        return self.section(self.content(student), r"(<h1>.*?</h1>\s*(?:<code>.*?</code>)?)")

    def test_the_title_shows_the_name_not_the_username(self):
        title = self.title(self.ada)

        self.assertIn("Ada Sample", title)
        self.assertNotIn("sample-ada", title)

    def test_the_title_of_a_student_with_no_name_shows_the_username_once(self):
        self.assertEqual(self.title(self.nameless).count("sample-nameless"), 1)

    def test_the_title_of_a_student_with_only_a_first_name_shows_it(self):
        title = self.title(self.first_only)

        self.assertIn("Linus", title)
        self.assertNotIn("sample-first", title)

    def test_the_breadcrumb_shows_the_name_not_the_username(self):
        crumb = self.crumb(self.ada)

        self.assertIn("Ada Sample", crumb)
        self.assertNotIn("sample-ada", crumb)

    def test_the_breadcrumb_of_a_student_with_no_name_shows_the_username_once(self):
        self.assertEqual(self.crumb(self.nameless).count("sample-nameless"), 1)

    def test_the_heading_shows_the_name_in_bold_with_the_username_below(self):
        heading = self.heading(self.ada)

        self.assertRegex(heading, r"<h1>\s*Ada Sample\s*</h1>\s*<code>sample-ada</code>")

    def test_a_student_with_only_a_last_name_gets_the_name_and_the_username_below(self):
        heading = self.heading(self.last_only)

        self.assertRegex(heading, r"<h1>\s*Hopper\s*</h1>\s*<code>sample-last</code>")

    def test_a_student_with_no_name_gets_the_username_once_with_nothing_below(self):
        heading = self.heading(self.nameless)

        self.assertRegex(heading, r"^<h1>\s*sample-nameless\s*</h1>\s*$")


class FlagReasonNameTests(DisplayNameFixture):
    def reasons(self):
        give(self.ada, self.team, "major", "major", "major", "major")
        give(self.first_only, self.team, "major", "major", "major", "major")
        give(self.last_only, self.team, "minor")
        give(self.nameless, self.team, "minor")
        return {flag.user.id: flag.reason for flag in flags.low_requirement_share(self.team)}

    def test_a_flag_reason_names_a_student_by_their_name(self):
        reason = self.reasons()[self.last_only.id]

        self.assertTrue(reason.startswith("Hopper did "), reason)
        self.assertNotIn("sample-last", reason)

    def test_a_flag_reason_for_a_student_with_no_name_uses_the_username_once(self):
        reason = self.reasons()[self.nameless.id]

        self.assertTrue(reason.startswith("sample-nameless did "), reason)
        self.assertEqual(reason.count("sample-nameless"), 1)


class GenerationMessageNameTests(DisplayNameFixture):
    def message(self, *failed):
        failures = [SummaryFailure(student, "the model timed out") for student in failed]
        return TeamGeneration(self.team, WEEK, 4, failures).message

    def test_a_failure_names_the_student_by_name_without_the_username(self):
        message = self.message(self.ada)

        self.assertIn("Ada Sample (the model timed out)", message)
        self.assertNotIn("sample-ada", message)

    def test_a_failure_for_a_student_with_no_name_uses_the_username_once(self):
        message = self.message(self.nameless)

        self.assertEqual(message.count("sample-nameless"), 1)
