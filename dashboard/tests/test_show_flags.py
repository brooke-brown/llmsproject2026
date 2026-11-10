"""Tests for US-20 (#62): see flagged teams first.

Pages read stored Flag rows, so these tests create flags directly instead of running
the rules. A flag belongs to a finished sprint every student has a complete summary for (US-41),
so make_flag sets that up too. Each team and each student is rendered in its own <article>, so the
tests check what appears inside a given team's or student's article.
"""

from django.test import TestCase
from django.utils.html import escape

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag

from .sprint_helpers import flaggable_sprint
from .test_course_overview import enroll, make_user, overview_url, team_url

Role = Enrollment.Role
Severity = Flag.Severity


def make_flag(team, severity, reason, user=None):
    return Flag.objects.create(
        team=team,
        user=user,
        rule="low-requirement-share",
        reason=reason,
        severity=severity,
        sprint=flaggable_sprint(team),
    )


def article_containing(content, text):
    """The <article> on the page that contains `text`."""
    at = content.index(text)
    return content[content.rindex("<article", 0, at) : content.index("</article>", at)]


class ShowFlagsFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.alpha = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.beta = Team.objects.create(project=cls.project, name="Team Beta")
        cls.charlie = Team.objects.create(project=cls.project, name="Team Charlie")
        cls.delta = Team.objects.create(project=cls.project, name="Team Delta")

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.alpha)
        # Only a team with students can raise flags.
        for team in [cls.beta, cls.charlie, cls.delta]:
            student = make_user(f"sample-{team.name.split()[-1].lower()}")
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=team)

    def get_overview(self):
        self.client.force_login(self.instructor)
        return self.client.get(overview_url(self.course.id)).content.decode()

    def get_team_page(self, team=None):
        self.client.force_login(self.instructor)
        return self.client.get(team_url((team or self.alpha).id)).content.decode()


class OverviewOrderTests(ShowFlagsFixture):
    def test_lists_flagged_teams_then_warned_teams_then_the_rest(self):
        make_flag(self.delta, Severity.FLAG, "Delta reason")
        make_flag(self.beta, Severity.WARNING, "Beta reason")

        content = self.get_overview()

        delta, beta = content.index("Team Delta"), content.index("Team Beta")
        alpha, charlie = content.index("Team Alpha"), content.index("Team Charlie")
        self.assertLess(delta, beta)
        self.assertLess(beta, alpha)
        self.assertLess(beta, charlie)

    def test_teams_in_the_same_group_are_sorted_by_name(self):
        make_flag(self.delta, Severity.FLAG, "Delta reason")
        make_flag(self.charlie, Severity.FLAG, "Charlie reason")

        content = self.get_overview()

        self.assertLess(content.index("Team Charlie"), content.index("Team Delta"))
        self.assertLess(content.index("Team Delta"), content.index("Team Alpha"))
        self.assertLess(content.index("Team Alpha"), content.index("Team Beta"))

    def test_a_team_with_a_flag_and_a_warning_counts_as_flagged(self):
        make_flag(self.delta, Severity.FLAG, "Delta flag reason")
        make_flag(self.delta, Severity.WARNING, "Delta warning reason")
        make_flag(self.beta, Severity.WARNING, "Beta reason")

        content = self.get_overview()

        self.assertLess(content.index("Team Delta"), content.index("Team Beta"))

    def test_teams_are_ordered_within_their_own_project(self):
        project_two = Project.objects.create(course=self.course, name="Project Two")
        flagged_later = Team.objects.create(project=project_two, name="Team Aardvark")
        TeamMembership.objects.create(user=self.ada, team=flagged_later)
        make_flag(flagged_later, Severity.FLAG, "Aardvark reason")

        content = self.get_overview()

        # Project One's teams still come before Project Two's flagged team.
        self.assertLess(content.index("Team Delta"), content.index("Project Two"))
        self.assertLess(content.index("Project Two"), content.index("Team Aardvark"))


class OverviewFlagContentTests(ShowFlagsFixture):
    """US-22 (#83): the overview shows one label per team and leaves the reasons to the
    team page, so an instructor clicks into a team to see which student a flag is about."""

    def test_flagged_team_shows_its_label_but_not_the_reason(self):
        reason = "Ada Sample did 10% of the team's requirement-tied work."
        make_flag(self.alpha, Severity.FLAG, reason, user=self.ada)

        team = article_containing(self.get_overview(), "Team Alpha")

        self.assertIn("<mark>Flag</mark>", team)
        self.assertNotIn(escape(reason), team)

    def test_warned_team_shows_its_label_but_not_the_reason(self):
        make_flag(self.beta, Severity.WARNING, "Beta did less this week")

        team = article_containing(self.get_overview(), "Team Beta")

        self.assertIn("<mark>Warning</mark>", team)
        self.assertNotIn("Beta did less this week", team)

    def test_team_with_a_flag_and_a_warning_shows_one_flag_label(self):
        make_flag(self.alpha, Severity.WARNING, "First reason", user=self.ada)
        make_flag(self.alpha, Severity.FLAG, "Second reason", user=self.grace)

        team = article_containing(self.get_overview(), "Team Alpha")

        self.assertEqual(team.count("<mark>"), 1)
        self.assertIn("<mark>Flag</mark>", team)

    def test_team_with_several_warnings_shows_one_warning_label(self):
        make_flag(self.alpha, Severity.WARNING, "First reason", user=self.ada)
        make_flag(self.alpha, Severity.WARNING, "Second reason", user=self.grace)

        team = article_containing(self.get_overview(), "Team Alpha")

        self.assertEqual(team.count("<mark>"), 1)
        self.assertIn("<mark>Warning</mark>", team)

    def test_team_with_no_flags_shows_no_label(self):
        make_flag(self.delta, Severity.FLAG, "Delta reason")

        team = article_containing(self.get_overview(), "Team Alpha")

        self.assertNotIn("<mark>", team)

    def test_a_label_appears_only_under_its_own_team(self):
        make_flag(self.delta, Severity.FLAG, "Delta reason")

        content = self.get_overview()

        self.assertIn("<mark>Flag</mark>", article_containing(content, "Team Delta"))
        self.assertNotIn("<mark>", article_containing(content, "Team Alpha"))

    def test_flags_from_another_course_never_appear(self):
        other_course = Course.objects.create(name="CS 3240, Fall 2026")
        other_project = Project.objects.create(course=other_course, name="Other Project")
        other_team = Team.objects.create(project=other_project, name="Team Elsewhere")
        make_flag(other_team, Severity.FLAG, "Elsewhere reason")

        self.assertNotIn("<mark>", self.get_overview())


class TeamPageFlagTests(ShowFlagsFixture):
    def test_flagged_student_is_highlighted_with_the_reason(self):
        reason = "Ada Sample did 10% of the team's requirement-tied work."
        make_flag(self.alpha, Severity.FLAG, reason, user=self.ada)

        student = article_containing(self.get_team_page(), "Ada Sample")

        self.assertIn("<mark>", student)
        self.assertIn("Flag", student)
        self.assertIn(escape(reason), student)

    def test_warned_student_is_highlighted_with_the_reason(self):
        make_flag(self.alpha, Severity.WARNING, "Ada did less this week", user=self.ada)

        student = article_containing(self.get_team_page(), "Ada Sample")

        self.assertIn("<mark>", student)
        self.assertIn("Warning", student)
        self.assertIn("Ada did less this week", student)

    def test_other_students_are_not_highlighted(self):
        make_flag(self.alpha, Severity.FLAG, "Ada reason", user=self.ada)

        student = article_containing(self.get_team_page(), "Grace Example")

        self.assertNotIn("<mark>", student)
        self.assertNotIn("Flag", student)
        self.assertNotIn("Ada reason", student)

    def test_team_with_no_flags_highlights_nobody(self):
        content = self.get_team_page()

        self.assertNotIn("<mark>", content)
        self.assertNotIn("Warning", content)

    def test_a_flag_from_the_students_other_team_doesnt_appear(self):
        TeamMembership.objects.create(user=self.ada, team=self.beta)
        make_flag(self.beta, Severity.FLAG, "Beta reason", user=self.ada)

        content = self.get_team_page(self.alpha)

        self.assertNotIn("Beta reason", content)
        self.assertNotIn("<mark>", content)

    def test_a_flag_about_the_whole_team_appears_on_the_team_page(self):
        make_flag(self.alpha, Severity.WARNING, "The whole team did less this week")

        content = self.get_team_page()

        self.assertIn("The whole team did less this week", content)
        self.assertIn("Warning", content)
