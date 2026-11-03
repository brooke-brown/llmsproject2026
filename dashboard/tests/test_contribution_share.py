"""Tests for the contribution share shown beside each student on the team page.

The share is the one the low-share flag rule uses: each student's verified,
requirement-tied claims weighted by significance, as a percentage of the team's total,
for the latest week in which every student on the team has a summary. It's shown for
every team, flagged or not.
"""

from datetime import date

from django.test import TestCase

from core.models import Course, Enrollment, Project, Team, TeamMembership
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, team_url
from .test_flag_rules import give

WEEK = date(2026, 10, 26)
EARLIER_WEEK = date(2026, 10, 19)


class ContributionShareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, course, Enrollment.Role.INSTRUCTOR)
        project = Project.objects.create(course=course, name="Project One")
        cls.team = Team.objects.create(project=project, name="Team Alpha")
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, course, Enrollment.Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

    def page(self):
        self.client.force_login(self.instructor)
        return self.client.get(team_url(self.team.id)).content.decode()

    def share_beside(self, content, name):
        """The text from a student's name to the end of their header."""
        start = content.index(name)
        return content[start : content.index("</header>", start)]

    def test_shows_each_students_share_beside_their_name(self):
        give(self.ada, self.team, "major")  # 3 of 4 points.
        give(self.grace, self.team, "minor")  # 1 of 4 points.

        content = self.page()

        self.assertIn("75%", self.share_beside(content, "Ada Sample"))
        self.assertIn("25%", self.share_beside(content, "Grace Example"))

    def test_a_balanced_team_with_no_flags_still_shows_shares(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "major")

        content = self.page()

        self.assertFalse(self.team.flags.exists())
        self.assertIn("50%", self.share_beside(content, "Ada Sample"))
        self.assertIn("50%", self.share_beside(content, "Grace Example"))

    def test_says_which_week_the_shares_are_for(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "minor")

        self.assertIn("week of Oct 26, 2026", self.page())

    def test_uses_the_latest_week_every_student_has_a_summary(self):
        give(self.ada, self.team, "major", week=EARLIER_WEEK)
        give(self.grace, self.team, "major", week=EARLIER_WEEK)
        give(self.ada, self.team, "major")  # Grace has no summary this week.

        content = self.page()

        self.assertIn("week of Oct 19, 2026", content)
        self.assertIn("50%", self.share_beside(content, "Ada Sample"))

    def test_only_verified_requirement_tied_claims_count(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "major")
        give(self.grace, self.team, "major", tied=False)
        give(self.grace, self.team, "major", status=Claim.Status.COULDNT_VERIFY)

        content = self.page()

        self.assertIn("50%", self.share_beside(content, "Grace Example"))

    def test_no_shares_until_every_student_has_a_summary(self):
        give(self.ada, self.team, "major")

        content = self.page()

        self.assertNotIn("%", self.share_beside(content, "Ada Sample"))
        self.assertNotIn("requirement-tied work", content)

    def test_no_shares_when_nobody_has_requirement_tied_work(self):
        give(self.ada, self.team, "major", tied=False)
        Summary.objects.create(team=self.team, user=self.grace, week_start=WEEK)

        content = self.page()

        self.assertNotIn("%", self.share_beside(content, "Ada Sample"))
        self.assertNotIn("%", self.share_beside(content, "Grace Example"))
