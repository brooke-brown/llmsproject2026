"""Tests for US-16 (#53): see a team's students and their summaries, one sprint at a time.

URLs are reversed per test rather than at module level, so each criterion passes
or fails on its own instead of the whole module failing on a single
NoReverseMatch.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership
from summaries.models import Summary
from summaries.tests.week_shim import sprint_for_week

from .test_course_overview import enroll, make_user, overview_url, team_url

Role = Enrollment.Role


def summary_url(summary_id):
    return reverse("dashboard:summary", args=[summary_id])


class TeamPageFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/app", full_name="sample-org/app"
        )
        Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/api", full_name="sample-org/api"
        )

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

        # Each sprint is a Monday-Sunday week named for its Monday, as "Week of Oct 19".
        cls.ada_oct_19 = Summary.objects.create(
            team=cls.team, user=cls.ada, sprint=sprint_for_week(cls.project, date(2026, 10, 19))
        )
        cls.ada_oct_26 = Summary.objects.create(
            team=cls.team, user=cls.ada, sprint=sprint_for_week(cls.project, date(2026, 10, 26))
        )

        other_project = Project.objects.create(course=cls.course, name="Project Two")
        cls.other_team = Team.objects.create(project=other_project, name="Team Beta")

    def get_team_page(self, user=None, team=None):
        self.client.force_login(user or self.instructor)
        return self.client.get(team_url((team or self.team).id))


class TeamPageHeaderTests(TeamPageFixture):
    def test_shows_team_project_course_and_repos(self):
        response = self.get_team_page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team Alpha")
        self.assertContains(response, "Project One")
        self.assertContains(response, f'href="{overview_url(self.course.id)}"')
        self.assertContains(response, "CS 4501, Fall 2026")
        self.assertContains(response, "sample-org/app")
        self.assertContains(response, "sample-org/api")

    def test_lists_only_this_teams_repos(self):
        Repo.objects.create(
            team=self.other_team,
            url="https://github.com/sample-org/elsewhere",
            full_name="sample-org/elsewhere",
        )

        response = self.get_team_page()

        self.assertContains(response, "sample-org/app")
        self.assertNotContains(response, "sample-org/elsewhere")


class TeamPageStudentTests(TeamPageFixture):
    def test_lists_each_students_name(self):
        response = self.get_team_page()

        self.assertContains(response, "Ada Sample")
        self.assertContains(response, "Grace Example")

    def test_lists_only_this_teams_students(self):
        outsider = make_user("sample-outsider", "Otto", "Elsewhere")
        TeamMembership.objects.create(user=outsider, team=self.other_team)

        response = self.get_team_page()

        self.assertContains(response, "Ada Sample")
        self.assertNotContains(response, "Otto Elsewhere")

    def test_team_with_no_students_says_so(self):
        response = self.get_team_page(team=self.other_team)

        self.assertContains(response, "No students on this team yet.")

    def test_team_with_students_lists_them_instead_of_saying_it_has_none(self):
        response = self.get_team_page()

        self.assertContains(response, "Ada Sample")
        self.assertNotContains(response, "No students on this team yet.")


class TeamPageSummaryTests(TeamPageFixture):
    """The page shows one sprint at a time: by default the latest the team has a summary for.
    Choosing a sprint is tested in test_team_sprint (US-42)."""

    def test_labels_each_summary_with_its_sprint(self):
        response = self.get_team_page()

        self.assertContains(
            response,
            f'<a href="{summary_url(self.ada_oct_26.id)}">Week of Oct 26',
        )

    def test_each_summary_links_to_its_summary_page(self):
        self.assertContains(self.get_team_page(), f'href="{summary_url(self.ada_oct_26.id)}"')

        self.client.force_login(self.instructor)
        earlier = self.client.get(
            team_url(self.team.id), {"sprint": self.ada_oct_19.sprint_id}
        )
        self.assertContains(earlier, f'href="{summary_url(self.ada_oct_19.id)}"')

    def test_summaries_appear_under_their_student(self):
        grace_summary = Summary.objects.create(
            team=self.team, user=self.grace, sprint=self.ada_oct_26.sprint
        )

        content = self.get_team_page().content.decode()

        ada = content.index("Ada Sample")
        grace = content.index("Grace Example")
        ada_pill = content.index(f'href="{summary_url(self.ada_oct_26.id)}">Week of')
        grace_pill = content.index(f'href="{summary_url(grace_summary.id)}">Week of')
        # Students are listed by last name, so Grace Example comes before Ada Sample.
        self.assertLess(grace, grace_pill)
        self.assertLess(grace_pill, ada)
        self.assertLess(ada, ada_pill)

    def test_student_with_no_summary_for_the_sprint_says_so(self):
        response = self.get_team_page()

        self.assertContains(response, "<li>Not generated</li>", count=1, html=True)

    def test_lists_only_a_students_summaries_for_this_team(self):
        TeamMembership.objects.create(user=self.ada, team=self.other_team)
        Summary.objects.create(
            team=self.other_team,
            user=self.ada,
            sprint=sprint_for_week(self.other_team.project, date(2026, 9, 28)),
        )

        response = self.get_team_page()

        self.assertContains(response, "Week of Oct 26")
        self.assertNotContains(response, "Week of Sep 28")


class TeamPageStudentLinkTests(TeamPageFixture):
    """US-22 (#83): each student's name leads to their newest summary."""

    def test_a_students_name_links_to_their_newest_summary(self):
        response = self.get_team_page()

        self.assertContains(
            response, f'<a href="{summary_url(self.ada_oct_26.id)}">Ada Sample</a>', html=True
        )

    def test_the_link_ignores_the_students_summaries_on_other_teams(self):
        TeamMembership.objects.create(user=self.ada, team=self.other_team)
        elsewhere = Summary.objects.create(
            team=self.other_team,
            user=self.ada,
            sprint=sprint_for_week(self.other_team.project, date(2026, 11, 2)),
        )

        response = self.get_team_page()

        self.assertContains(
            response, f'<a href="{summary_url(self.ada_oct_26.id)}">Ada Sample</a>', html=True
        )
        self.assertNotContains(response, f'href="{summary_url(elsewhere.id)}"')

    def test_a_student_with_no_summaries_is_plain_text_and_says_so(self):
        content = self.get_team_page().content.decode()

        self.assertIn("<strong>Grace Example</strong>", content)
        self.assertNotIn(">Grace Example</a>", content)
        self.assertIn("<li>Not generated</li>", content)


class TeamPageAccessTests(TeamPageFixture):
    def test_instructor_enrolled_in_course_sees_the_team_page(self):
        response = self.get_team_page(self.instructor)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Sample")
        self.assertContains(response, "Week of Oct 26")

    def test_ta_enrolled_in_course_sees_the_team_page(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.get_team_page(ta)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Sample")
        self.assertContains(response, "Week of Oct 26")

    def test_student_on_the_team_gets_403(self):
        response = self.get_team_page(self.ada)

        self.assertEqual(response.status_code, 403)

    def test_user_not_enrolled_gets_403(self):
        response = self.get_team_page(make_user("sample-outsider"))

        self.assertEqual(response.status_code, 403)

    def test_instructor_of_another_course_gets_403(self):
        other_instructor = make_user("sample-other-instructor")
        enroll(other_instructor, Course.objects.create(name="CS 2100"), Role.INSTRUCTOR)

        response = self.get_team_page(other_instructor)

        self.assertEqual(response.status_code, 403)

    def test_missing_team_returns_404(self):
        self.client.force_login(self.instructor)

        response = self.client.get(team_url(self.team.id + 1000))

        self.assertEqual(response.status_code, 404)

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = team_url(self.team.id)

        response = self.client.get(url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )
