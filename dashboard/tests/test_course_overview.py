"""Tests for US-15 (#42): see every team in my course.

URLs are reversed per test rather than at module level, so each criterion passes
or fails on its own instead of the whole module failing on a single
NoReverseMatch.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership

Role = Enrollment.Role


def make_user(login, first_name="", last_name=""):
    return get_user_model().objects.create_user(
        username=login,
        password="not-a-real-password",
        github_login=login,
        first_name=first_name,
        last_name=last_name,
    )


def enroll(user, course, role):
    return Enrollment.objects.create(user=user, course=course, role=role)


def overview_url(course_id):
    return reverse("dashboard:course_overview", args=[course_id])


def team_url(team_id):
    return reverse("dashboard:team", args=[team_id])


def home_url():
    return reverse("dashboard:home")


class CourseOverviewFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project_one = Project.objects.create(course=cls.course, name="Project One")
        cls.project_two = Project.objects.create(course=cls.course, name="Project Two")
        cls.empty_project = Project.objects.create(course=cls.course, name="Project Empty")

        cls.team_alpha = Team.objects.create(project=cls.project_one, name="Team Alpha")
        cls.team_beta = Team.objects.create(project=cls.project_two, name="Team Beta")

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student, team in [(cls.ada, cls.team_alpha), (cls.grace, cls.team_beta)]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=team)

        other_course = Course.objects.create(name="CS 3240, Fall 2026")
        other_project = Project.objects.create(course=other_course, name="Other Project")
        cls.other_team = Team.objects.create(project=other_project, name="Team Elsewhere")

    def get_overview(self, user=None):
        self.client.force_login(user or self.instructor)
        return self.client.get(overview_url(self.course.id))


class CourseOverviewContentTests(CourseOverviewFixture):
    def test_lists_the_courses_teams(self):
        response = self.get_overview()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team Alpha")
        self.assertContains(response, "Team Beta")

    def test_does_not_list_other_courses_teams(self):
        response = self.get_overview()

        self.assertNotContains(response, "Team Elsewhere")

    def test_groups_teams_under_their_project(self):
        content = self.get_overview().content.decode()

        project_one = content.index("Project One")
        team_alpha = content.index("Team Alpha")
        project_two = content.index("Project Two")
        team_beta = content.index("Team Beta")
        self.assertLess(project_one, team_alpha)
        self.assertLess(team_alpha, project_two)
        self.assertLess(project_two, team_beta)

    def test_shows_each_students_name(self):
        response = self.get_overview()

        self.assertContains(response, "Ada Sample")
        self.assertContains(response, "Grace Example")

    def test_each_team_links_to_its_team_page(self):
        response = self.get_overview()

        self.assertContains(response, f'href="{team_url(self.team_alpha.id)}"')
        self.assertContains(response, f'href="{team_url(self.team_beta.id)}"')

    def test_project_with_no_teams_says_so(self):
        response = self.get_overview()

        self.assertContains(response, "Project Empty")
        self.assertContains(response, "No teams in this project yet.")

    def test_project_with_teams_does_not_say_it_has_none(self):
        Project.objects.filter(pk=self.empty_project.pk).delete()

        response = self.get_overview()

        self.assertNotContains(response, "No teams in this project yet.")


class CourseOverviewAccessTests(CourseOverviewFixture):
    def test_instructor_enrolled_in_course_can_view(self):
        response = self.get_overview(self.instructor)

        self.assertEqual(response.status_code, 200)

    def test_ta_enrolled_in_course_can_view(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.get_overview(ta)

        self.assertEqual(response.status_code, 200)

    def test_student_in_course_gets_403(self):
        response = self.get_overview(self.ada)

        self.assertEqual(response.status_code, 403)

    def test_user_not_enrolled_gets_403(self):
        response = self.get_overview(make_user("sample-outsider"))

        self.assertEqual(response.status_code, 403)

    def test_instructor_of_another_course_gets_403(self):
        # Roles are per course: teaching one course grants nothing in another.
        other_instructor = make_user("sample-other-instructor")
        enroll(other_instructor, Course.objects.create(name="CS 2100"), Role.INSTRUCTOR)

        response = self.get_overview(other_instructor)

        self.assertEqual(response.status_code, 403)

    def test_missing_course_returns_404(self):
        self.client.force_login(self.instructor)

        response = self.client.get(overview_url(self.course.id + 1000))

        self.assertEqual(response.status_code, 404)

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = overview_url(self.course.id)

        response = self.client.get(url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )


class HomeLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.second_course = Course.objects.create(name="CS 3240, Fall 2026")

    def get_home(self, user):
        self.client.force_login(user)
        return self.client.get(home_url())

    def test_instructor_with_one_course_lands_on_its_overview(self):
        instructor = make_user("sample-instructor")
        enroll(instructor, self.course, Role.INSTRUCTOR)

        response = self.get_home(instructor)

        self.assertRedirects(
            response, overview_url(self.course.id), fetch_redirect_response=False
        )

    def test_ta_with_one_course_lands_on_its_overview(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.get_home(ta)

        self.assertRedirects(
            response, overview_url(self.course.id), fetch_redirect_response=False
        )

    def test_a_ta_who_is_also_a_student_sees_both_courses_to_pick_from(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        enroll(ta, self.second_course, Role.STUDENT)

        response = self.get_home(ta)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CS 4501, Fall 2026")
        self.assertContains(response, "CS 3240, Fall 2026")

    def test_instructor_with_several_courses_sees_list_to_pick_from(self):
        instructor = make_user("sample-instructor")
        enroll(instructor, self.course, Role.INSTRUCTOR)
        enroll(instructor, self.second_course, Role.TA)

        response = self.get_home(instructor)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{overview_url(self.course.id)}"')
        self.assertContains(response, "CS 4501, Fall 2026")
        self.assertContains(response, f'href="{overview_url(self.second_course.id)}"')
        self.assertContains(response, "CS 3240, Fall 2026")

    def test_student_only_user_is_sent_to_the_student_home(self):
        student = make_user("sample-student")
        enroll(student, self.course, Role.STUDENT)

        response = self.get_home(student)

        self.assertRedirects(response, reverse("dashboard:student_home"))
