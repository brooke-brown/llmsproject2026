"""Tests for US-47 (#137): a header that matches the signed-in user's role.

Every name here is made up. Pages are fetched through the real test client.
"""

import re
from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from summaries.models import Summary

from .test_course_overview import enroll, make_user

Role = Enrollment.Role


class NavigationFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        Repo.objects.create(team=cls.team, url="https://github.com/acme/app", full_name="acme/app")
        Requirement.objects.create(project=cls.project, number=1, text="Log in")

        cls.instructor = make_user("sample-instructor", "Iris", "Instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta", "Tess", "Assistant")
        enroll(cls.ta, cls.course, Role.TA)
        cls.student = make_user("sample-student", "Grace", "Example")
        enroll(cls.student, cls.course, Role.STUDENT)
        TeamMembership.objects.create(user=cls.student, team=cls.team)
        cls.summary = Summary.objects.create(team=cls.team, user=cls.student, week_start=date(2026, 10, 26))

        # Staff in one course and a student in another.
        cls.both = make_user("sample-both", "Bea", "Both")
        enroll(cls.both, cls.course, Role.TA)
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        enroll(cls.both, cls.other_course, Role.STUDENT)

    def header(self, user, url):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode()
        return re.search(r"<header.*?</header>", html, re.S).group(0)

    def home(self):
        return reverse("dashboard:home")

    def student_home(self):
        return reverse("dashboard:student_home")


class RoleLinksTests(NavigationFixture):
    def test_instructors_and_tas_see_courses_and_not_my_projects(self):
        for user in (self.instructor, self.ta):
            header = self.header(user, reverse("dashboard:course_overview", args=[self.course.id]))
            self.assertIn(">Courses<", header, user)
            self.assertIn(f'href="{self.home()}"', header)
            self.assertNotIn("My projects", header)

    def test_students_see_my_projects_and_not_courses(self):
        header = self.header(self.student, self.student_home())
        self.assertIn(">My projects<", header)
        self.assertIn(f'href="{self.student_home()}"', header)
        self.assertNotIn(">Courses<", header)

    def test_someone_who_is_both_sees_both_links(self):
        header = self.header(self.both, self.home())
        self.assertIn(">Courses<", header)
        self.assertIn(">My projects<", header)

    def test_the_links_are_on_every_kind_of_page(self):
        pages = [
            reverse("dashboard:course_overview", args=[self.course.id]),
            reverse("dashboard:team", args=[self.team.id]),
            reverse("dashboard:summary", args=[self.summary.id]),
            reverse("dashboard:project_requirements", args=[self.project.id]),
            reverse("dashboard:project_setup", args=[self.project.id]),
        ]
        for url in pages:
            self.assertIn(">Courses<", self.header(self.instructor, url), url)


class SignedInTests(NavigationFixture):
    def test_every_page_shows_who_is_signed_in_by_name_and_a_log_out_post_form(self):
        cases = [
            (self.instructor, reverse("dashboard:course_overview", args=[self.course.id]), "Iris Instructor"),
            (self.student, self.student_home(), "Grace Example"),
        ]
        for user, url, name in cases:
            header = self.header(user, url)
            self.assertIn(name, header)
            form = re.search(r'<form[^>]*action="%s"[^>]*>.*?</form>' % reverse("logout"), header, re.S).group(0)
            self.assertIn('method="post"', form)
            self.assertIn("csrfmiddlewaretoken", form)
            self.assertIn("Log out", form)

    def test_a_user_with_no_name_is_shown_by_their_github_login(self):
        nameless = make_user("nameless-dev")
        enroll(nameless, self.course, Role.STUDENT)
        self.assertIn("nameless-dev", self.header(nameless, self.student_home()))


class BreadcrumbTests(NavigationFixture):
    def crumb(self, url):
        self.client.force_login(self.instructor)
        html = self.client.get(url).content.decode()
        return re.search(r'<span class="crumb">.*?</span>', html, re.S).group(0)

    def test_team_summary_and_requirement_pages_link_back_to_the_overview(self):
        overview = reverse("dashboard:course_overview", args=[self.course.id])
        for name, arg in (("team", self.team.id), ("summary", self.summary.id), ("project_requirements", self.project.id)):
            crumb = self.crumb(reverse(f"dashboard:{name}", args=[arg]))
            self.assertIn(f'href="{overview}"', crumb, name)

    def test_a_summary_page_also_links_to_its_team(self):
        crumb = self.crumb(reverse("dashboard:summary", args=[self.summary.id]))
        self.assertIn(reverse("dashboard:team", args=[self.team.id]), crumb)


class ForcedPasswordChangeTests(NavigationFixture):
    def test_a_user_who_must_change_their_password_sees_only_that_page_and_log_out(self):
        self.student.must_change_password = True
        self.student.save()
        header = self.header(self.student, reverse("password_change"))
        self.assertIn("Log out", header)
        self.assertNotIn(">My projects<", header)
        self.assertNotIn(">Courses<", header)
        self.assertNotIn('class="crumb"', header)

    def test_their_brand_is_not_a_link_that_would_only_bounce_them_back(self):
        self.student.must_change_password = True
        self.student.save()
        header = self.header(self.student, reverse("password_change"))
        self.assertNotIn(f'href="{self.home()}"', header)


class PhoneWidthTests(NavigationFixture):
    def test_the_page_scales_to_the_device_and_the_header_wraps(self):
        self.client.force_login(self.student)
        html = self.client.get(self.student_home()).content.decode()
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html)
        rule = re.search(r"\.bar \{[^}]*\}", html).group(0)
        self.assertIn("flex-wrap: wrap", rule)

    def test_the_links_have_a_rule_that_keeps_them_in_the_header_on_a_phone(self):
        self.client.force_login(self.student)
        html = self.client.get(self.student_home()).content.decode()
        self.assertIn(".bar .nav", html)
