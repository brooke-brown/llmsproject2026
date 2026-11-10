"""Tests for US-48 (#138): clear 403, 404, 500 and empty-state pages.

Every name here is made up. The test client runs with DEBUG off, so the custom error pages
are what a user really gets.
"""

import re
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership
from summaries.models import Summary

from .test_course_overview import enroll, make_user

Role = Enrollment.Role


class ErrorFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor", "Iris", "Instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta", "Tess", "Assistant")
        enroll(cls.ta, cls.course, Role.TA)
        cls.student = make_user("sample-student", "Grace", "Example")
        enroll(cls.student, cls.course, Role.STUDENT)

        # A second course the student can't see into.
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.other_project = Project.objects.create(course=cls.other_course, name="Secret Project")
        cls.other_team = Team.objects.create(project=cls.other_project, name="Secret Team")

    def get(self, user, url):
        client = Client()
        if user is not None:
            client.force_login(user)
        return client.get(url)

    def text(self, response):
        return re.sub(r"\s+", " ", response.content.decode())


class NotFoundTests(ErrorFixture):
    missing = "/teams/999999/"

    def test_it_says_the_page_was_not_found_with_a_404_status(self):
        response = self.get(self.instructor, self.missing)
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "doesn't exist", status_code=404)

    def test_it_links_home_for_each_role(self):
        cases = [
            (self.instructor, "Back to your courses", reverse("dashboard:home")),
            (self.ta, "Back to your courses", reverse("dashboard:home")),
            (self.student, "Back to your projects", reverse("dashboard:student_home")),
        ]
        for user, label, href in cases:
            response = self.get(user, self.missing)
            self.assertContains(response, label, status_code=404)
            self.assertContains(response, f'href="{href}"', status_code=404)

    def test_someone_not_signed_in_is_pointed_to_log_in(self):
        response = self.get(None, "/no/such/page/")
        self.assertContains(response, "Log in", status_code=404)
        self.assertContains(response, reverse("login"), status_code=404)

    def test_it_does_not_echo_the_address_that_was_asked_for(self):
        response = self.get(self.instructor, "/teams/<b>x</b>-secret-token/")
        self.assertNotContains(response, "secret-token", status_code=404)


class ForbiddenTests(ErrorFixture):
    def test_it_says_access_is_not_allowed_with_a_403_status(self):
        response = self.get(self.student, reverse("dashboard:team", args=[self.other_team.id]))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "don't have access", status_code=403)

    def test_it_tells_the_user_what_to_do_and_links_home_for_their_role(self):
        url = reverse("dashboard:team", args=[self.other_team.id])
        response = self.get(self.student, url)
        self.assertContains(response, "ask your instructor", status_code=403)
        self.assertContains(response, reverse("dashboard:student_home"), status_code=403)
        response = self.get(self.ta, reverse("dashboard:project_setup", args=[self.other_project.id]))
        self.assertContains(response, "Back to your courses", status_code=403)

    def test_it_never_shows_the_data_the_user_was_refused(self):
        response = self.get(self.student, reverse("dashboard:team", args=[self.other_team.id]))
        for private in ("Secret Team", "Secret Project", "CS 3240"):
            self.assertNotContains(response, private, status_code=403)


class ServerErrorTests(ErrorFixture):
    def test_a_crash_shows_a_plain_page_with_no_trace_or_detail(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.instructor)
        with mock.patch("dashboard.views.services.course_cards", side_effect=RuntimeError("secret-detail-xyz")):
            response = client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 500)
        html = response.content.decode()
        self.assertIn("Something went wrong", html)
        for leaked in ("Traceback", "secret-detail-xyz", "RuntimeError", "dashboard/views.py"):
            self.assertNotIn(leaked, html)


class EmptyStateTests(ErrorFixture):
    def page(self, user, url):
        response = self.get(user, url)
        self.assertEqual(response.status_code, 200, url)
        return self.text(response)

    def overview(self):
        return reverse("dashboard:course_overview", args=[self.course.id])

    def test_a_course_with_no_projects_says_what_is_missing_and_what_to_do(self):
        text = self.page(self.instructor, self.overview())
        self.assertIn("No projects in this course yet. Create one with New project.", text)
        text = self.page(self.ta, self.overview())
        self.assertIn("No projects in this course yet. An instructor needs to create one.", text)

    def test_a_project_with_no_teams(self):
        Project.objects.create(course=self.course, name="Project One")
        text = self.page(self.instructor, self.overview())
        self.assertIn("No teams in this project yet. Add one below.", text)
        text = self.page(self.ta, self.overview())
        self.assertIn("No teams in this project yet. An instructor needs to add teams.", text)

    def test_the_requirements_panel_says_who_can_add_them(self):
        Project.objects.create(course=self.course, name="Project One")
        text = self.page(self.ta, self.overview())
        self.assertIn("No requirements for this project yet. An instructor can add them.", text)

    def test_a_team_card_with_no_students(self):
        project = Project.objects.create(course=self.course, name="Project One")
        Team.objects.create(project=project, name="Team Alpha")
        text = self.page(self.instructor, self.overview())
        self.assertIn("No students on this team yet. Open the team to add them.", text)
        text = self.page(self.ta, self.overview())
        self.assertIn("No students on this team yet. An instructor needs to add them.", text)

    def test_a_team_page_with_no_students_and_no_summaries(self):
        project = Project.objects.create(course=self.course, name="Project One")
        team = Team.objects.create(project=project, name="Team Alpha")
        url = reverse("dashboard:team", args=[team.id])
        self.assertIn("No students on this team yet. Add one below.", self.page(self.instructor, url))
        self.assertIn("An instructor needs to add students.", self.page(self.ta, url))
        TeamMembership.objects.create(user=self.student, team=team)
        text = self.page(self.instructor, url)
        self.assertIn("No summaries yet. Generate them from the course overview.", text)

    def test_a_summary_with_no_activity_suggests_checking_the_repo_and_username(self):
        project = Project.objects.create(course=self.course, name="Project One")
        team = Team.objects.create(project=project, name="Team Alpha")
        TeamMembership.objects.create(user=self.student, team=team)
        summary = Summary.objects.create(team=team, user=self.student, week_start="2026-10-26", artifact_count=0)
        text = self.page(self.instructor, reverse("dashboard:summary", args=[summary.id]))
        self.assertIn("No activity that week.", text)
        self.assertIn("Check that the team's repo and the student's GitHub username are right.", text)

    def test_a_student_on_no_team_is_told_to_ask_their_instructor(self):
        text = self.page(self.student, reverse("dashboard:student_home"))
        self.assertIn("You aren't on a team yet.", text)
        self.assertIn("Your instructor adds you to one", text)

    def test_a_student_on_a_team_keeps_the_usual_text(self):
        project = Project.objects.create(course=self.course, name="Project One")
        team = Team.objects.create(project=project, name="Team Alpha")
        TeamMembership.objects.create(user=self.student, team=team)
        text = self.page(self.student, reverse("dashboard:student_home"))
        self.assertNotIn("You aren't on a team yet.", text)

    def test_someone_with_no_courses_is_told_what_to_do(self):
        nobody = make_user("sample-nobody")
        text = self.page(nobody, reverse("dashboard:home"))
        self.assertIn("You aren't an instructor or TA in any course yet.", text)
        self.assertIn("Ask the course's instructor to enroll you.", text)
