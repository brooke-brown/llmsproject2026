"""Tests for US-21 (#63): see a project's requirements.

Each project is rendered in its own <section>, so the tests check what appears inside
a given project's section.
"""

from django.test import TestCase

from core.models import Course, Enrollment, Project, Requirement, Team

from .test_course_overview import enroll, make_user, overview_url

Role = Enrollment.Role


def section_containing(content, text):
    """The <section> on the page that contains `text`."""
    at = content.index(text)
    return content[content.rindex("<section", 0, at) : content.index("</section>", at)]


class ProjectRequirementsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project_one = Project.objects.create(course=cls.course, name="Project One")
        cls.project_two = Project.objects.create(course=cls.course, name="Project Two")
        Team.objects.create(project=cls.project_one, name="Team Alpha")

        # Created out of order, to check they are sorted by number.
        Requirement.objects.create(project=cls.project_one, number=2, text="Users can log in")
        Requirement.objects.create(project=cls.project_one, number=10, text="Export a report")
        Requirement.objects.create(project=cls.project_one, number=1, text="Users can sign up")

        other_project = Project.objects.create(
            course=Course.objects.create(name="CS 3240"), name="Other Project"
        )
        Requirement.objects.create(project=other_project, number=1, text="Elsewhere requirement")

    def get_section(self, project_name):
        self.client.force_login(self.instructor)
        response = self.client.get(overview_url(self.course.id))
        self.assertEqual(response.status_code, 200)
        return section_containing(response.content.decode(), project_name)

    def test_shows_each_requirements_number_and_text_in_a_table(self):
        section = self.get_section("Project One")

        self.assertIn("<table", section)
        for number, text in [(1, "Users can sign up"), (2, "Users can log in"), (10, "Export a report")]:
            self.assertIn(f"<td>{number}</td>", section)
            self.assertIn(f"<td>{text}</td>", section)

    def test_requirements_are_sorted_by_number(self):
        section = self.get_section("Project One")

        sign_up = section.index("Users can sign up")
        log_in = section.index("Users can log in")
        export = section.index("Export a report")
        self.assertLess(sign_up, log_in)
        self.assertLess(log_in, export)

    def test_project_with_no_requirements_says_so(self):
        section = self.get_section("Project Two")

        self.assertIn("No requirements for this project yet.", section)
        self.assertNotIn("<table", section)

    def test_project_with_requirements_doesnt_say_it_has_none(self):
        section = self.get_section("Project One")

        self.assertNotIn("No requirements for this project yet.", section)

    def test_shows_only_that_projects_requirements(self):
        Requirement.objects.create(project=self.project_two, number=1, text="Project two only")

        one = self.get_section("Project One")
        two = self.get_section("Project Two")

        self.assertNotIn("Project two only", one)
        self.assertIn("Project two only", two)
        self.assertNotIn("Users can sign up", two)
        self.assertNotIn("Elsewhere requirement", one + two)
