"""Tests for T-21 (#125), part 2: requirements belong to a sprint only, never to a project.

`Requirement.project` is removed and every query reaches requirements through `sprint`. A
requirement left without a sprint has nothing to belong to, so the migration deletes it.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core import services
from core.models import Course, Project, Requirement, Sprint
from summaries.tests.test_weekly_data_removed import T21MigrationTestCase


class RequirementBelongsToASprintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=course, name="Project 1")
        cls.sprint = Sprint.objects.create(
            project=cls.project, name="Sprint 1", start_date=date(2026, 10, 23),
            end_date=date(2026, 10, 29),
        )

    def test_project_is_gone(self):
        with self.assertRaises(FieldDoesNotExist):
            Requirement._meta.get_field("project")

    def test_a_project_has_no_requirements_of_its_own(self):
        self.assertFalse(hasattr(Project, "requirements"))

    def test_sprint_is_required(self):
        self.assertFalse(Requirement._meta.get_field("sprint").null)

    def test_the_database_refuses_a_requirement_without_a_sprint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Requirement.objects.create(number=1, text="Log in")

    def test_a_requirement_needs_only_its_sprint(self):
        requirement = Requirement.objects.create(sprint=self.sprint, number=1, text="Log in")
        self.assertEqual(requirement.sprint.project, self.project)

    def test_only_the_per_sprint_constraint_is_left(self):
        names = {constraint.name for constraint in Requirement._meta.constraints}
        self.assertEqual(names, {"core_requirement_unique_per_sprint"})

    def test_admin_lists_and_filters_by_sprint_only(self):
        from django.contrib import admin

        requirement_admin = admin.site._registry[Requirement]
        for option in (
            requirement_admin.list_display, requirement_admin.list_filter, requirement_admin.ordering
        ):
            self.assertNotIn("project", option)


class ProjectRequirementCodeIsGoneTests(TestCase):
    def test_the_project_requirement_services_are_removed(self):
        for name in ("import_requirements", "add_requirement", "ALREADY_HAS_REQUIREMENTS"):
            self.assertFalse(hasattr(services, name), name)
        self.assertTrue(callable(services.import_sprint_requirements))
        self.assertTrue(callable(services.add_sprint_requirement))

    def test_the_project_requirements_import_in_admin_is_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:core_project_import_requirements", args=[1])
        reverse("admin:core_sprint_import_requirements", args=[1])

    def test_the_admin_add_form_asks_for_a_sprint_and_no_project(self):
        get_user_model().objects.create_superuser("root", password="x", github_login="root")
        self.client.login(username="root", password="x")
        page = self.client.get(reverse("admin:core_requirement_add")).content.decode()
        self.assertIn('name="sprint"', page)
        self.assertNotIn('name="project"', page)


class RequirementMigrationTests(T21MigrationTestCase):
    def setUp(self):
        super().setUp()
        course = self.old_model("core", "Course").objects.create(name="CS 4501")
        project = self.old_model("core", "Project").objects.create(course=course, name="Project 1")
        sprint = self.old_model("core", "Sprint").objects.create(
            project=project, name="Sprint 1", start_date=date(2026, 10, 23),
            end_date=date(2026, 10, 29),
        )
        Requirement_ = self.old_model("core", "Requirement")
        self.in_sprint = Requirement_.objects.create(
            project=project, sprint=sprint, number=2, text="Log in"
        )
        self.sprint_id = sprint.pk
        # Added on the project's own requirements page, which the alpha had.
        self.project_only = Requirement_.objects.create(project=project, number=1, text="Sign up")

    def migrate(self):
        return self.migrate_to_latest().get_model("core", "Requirement")

    def test_deletes_requirements_with_no_sprint(self):
        Requirement_ = self.migrate()
        self.assertFalse(Requirement_.objects.filter(pk=self.project_only.pk).exists())

    def test_keeps_each_sprint_requirement_with_its_number_and_text(self):
        Requirement_ = self.migrate()
        kept = Requirement_.objects.get(pk=self.in_sprint.pk)
        self.assertEqual(
            (kept.sprint_id, kept.number, kept.text), (self.sprint_id, 2, "Log in")
        )
