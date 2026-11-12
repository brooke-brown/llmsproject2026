"""Tests for T-14 (#102): what a student can reach and where each person lands after login.

The account side (`must_change_password`, `set_temporary_password`, the forced password
change) is in `core/tests/test_student_logins.py`.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from dashboard import services
from summaries.models import Summary
from summaries.tests.week_shim import sprint_for_week

from .test_course_overview import enroll, make_user

Role = Enrollment.Role
User = get_user_model()

PASSWORD = "not-a-real-password"


class AccessFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.other_team = Team.objects.create(project=cls.project, name="Team Beta")

        cls.student = make_user("sample-casey")
        enroll(cls.student, cls.course, Role.STUDENT)
        cls.membership = TeamMembership.objects.create(user=cls.student, team=cls.team)


class IsCourseStudentTests(AccessFixture):
    def test_true_for_a_student_in_the_course(self):
        self.assertIs(services.is_course_student(self.student, self.course), True)

    def test_false_for_a_student_of_another_course(self):
        self.assertIs(services.is_course_student(self.student, self.other_course), False)

    def test_false_for_instructors_and_tas(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                user = make_user(f"sample-{role}")
                enroll(user, self.course, role)
                self.assertIs(services.is_course_student(user, self.course), False)

    def test_false_for_someone_not_enrolled(self):
        self.assertIs(services.is_course_student(make_user("sample-none"), self.course), False)


class GetTeamForStudentTests(AccessFixture):
    def test_returns_the_students_team(self):
        self.assertEqual(services.get_team_for_student(self.student, self.team.id), self.team)

    def test_403_for_another_team_in_the_same_course(self):
        with self.assertRaises(PermissionDenied):
            services.get_team_for_student(self.student, self.other_team.id)

    def test_403_for_the_courses_staff_who_are_not_on_the_team(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                user = make_user(f"sample-{role}")
                enroll(user, self.course, role)
                with self.assertRaises(PermissionDenied):
                    services.get_team_for_student(user, self.team.id)

    def test_404_for_a_team_that_does_not_exist(self):
        with self.assertRaises(Http404):
            services.get_team_for_student(self.student, self.other_team.id + 1000)


class LoginLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")

    def log_in(self, user):
        return self.client.post(
            reverse("login"), {"username": user.username, "password": PASSWORD}, follow=True
        )

    def assert_landed_on(self, response, url):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], url)

    def test_a_student_in_one_course_lands_on_the_student_home(self):
        student = make_user("sample-casey")
        enroll(student, self.course, Role.STUDENT)
        self.assert_landed_on(self.log_in(student), reverse("dashboard:student_home"))

    def test_a_student_in_several_courses_lands_on_the_student_home(self):
        student = make_user("sample-casey")
        enroll(student, self.course, Role.STUDENT)
        enroll(student, self.other_course, Role.STUDENT)
        self.assert_landed_on(self.log_in(student), reverse("dashboard:student_home"))

    def test_staff_of_one_course_land_on_its_overview_as_today(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                user = make_user(f"sample-{role}")
                enroll(user, self.course, role)
                self.assert_landed_on(
                    self.log_in(user), reverse("dashboard:course_overview", args=[self.course.id])
                )
                self.client.logout()

    def test_staff_of_several_courses_land_on_the_dashboard_home_as_today(self):
        user = make_user("sample-instructor")
        enroll(user, self.course, Role.INSTRUCTOR)
        enroll(user, self.other_course, Role.TA)
        self.assert_landed_on(self.log_in(user), reverse("dashboard:home"))

    def test_someone_enrolled_nowhere_lands_on_the_dashboard_home_as_today(self):
        # "A student in every course they're enrolled in" must not count zero courses.
        self.assert_landed_on(self.log_in(make_user("sample-none")), reverse("dashboard:home"))

    def test_a_student_in_one_course_and_staff_in_another_lands_on_the_home_listing_both(self):
        for role in (Role.TA, Role.INSTRUCTOR):
            with self.subTest(role=role):
                user = make_user(f"sample-both-{role}")
                enroll(user, self.course, Role.STUDENT)
                enroll(user, self.other_course, role)
                response = self.log_in(user)
                self.assert_landed_on(response, reverse("dashboard:home"))
                self.assertContains(response, self.course.name)
                self.assertContains(response, self.other_course.name)
                self.client.logout()


    def test_the_home_links_a_student_course_to_the_student_home_not_its_overview(self):
        user = make_user("sample-both")
        enroll(user, self.course, Role.STUDENT)
        enroll(user, self.other_course, Role.TA)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, reverse("dashboard:student_home"))
        self.assertNotContains(
            response, reverse("dashboard:course_overview", args=[self.course.id])
        )


class StudentsAreRefusedOnStaffPagesTests(AccessFixture):
    """Named one by one: pages added later for students must not have to be excluded here."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sprint = sprint_for_week(cls.project, date(2026, 11, 2))
        cls.requirement = Requirement.objects.create(sprint=cls.sprint, number=1, text="Log in")
        cls.repo = Repo.objects.create(
            team=cls.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        cls.summary = Summary.objects.create(team=cls.team, user=cls.student, sprint=cls.sprint)

    def staff_pages(self):
        course, project, team = self.course.id, self.project.id, self.team.id
        requirement, membership, repo = self.requirement.id, self.membership.id, self.repo.id
        return [
            ("course_overview", course),
            ("course_generating_status", course),
            ("project_create", course),
            ("team", team),
            ("generate_team_summaries", team),
            ("retry_failed_summaries", team),
            ("team_rename", team),
            ("team_delete", team),
            ("student_add", team),
            ("repo_add", team),
            ("summary", self.summary.id),
            ("project_setup", project),
            ("project_delete", project),
            ("project_rename", project),
            ("sprint", self.sprint.id),
            ("sprint_requirements_import", self.sprint.id),
            ("sprint_requirement_add", self.sprint.id),
            ("team_create", project),
            ("requirement_edit", requirement),
            ("requirement_delete", requirement),
            ("membership_edit", membership),
            ("membership_remove", membership),
            ("repo_change", repo),
            ("repo_remove", repo),
        ]

    def test_403_on_every_instructor_and_ta_page(self):
        self.client.force_login(self.student)
        for name, object_id in self.staff_pages():
            with self.subTest(page=name):
                url = reverse(f"dashboard:{name}", args=[object_id])
                statuses = {self.client.get(url).status_code, self.client.post(url).status_code}
                # 405 is the method a page doesn't accept. It never reaches the page's work.
                self.assertIn(403, statuses)
                self.assertLessEqual(statuses, {403, 405})
