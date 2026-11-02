"""Tests for T-1 (#12): the Django project and the core data model.

Models are looked up per test rather than imported at module level, so each
criterion passes or fails on its own instead of the whole module failing on a
single ImportError.
"""

import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

REPO_ROOT = Path(__file__).resolve().parents[2]

CORE_MODELS = [
    "User",
    "Course",
    "Enrollment",
    "Project",
    "Team",
    "TeamMembership",
    "Repo",
    "Requirement",
]


def core_model(name):
    return apps.get_model("core", name)


def make_user(login, **kwargs):
    return core_model("User").objects.create_user(
        username=login, password="not-a-real-password", github_login=login, **kwargs
    )


def make_course(name="CS 3240, Fall 2026"):
    return core_model("Course").objects.create(name=name)


def make_project(course=None, name="Project 1"):
    return core_model("Project").objects.create(course=course or make_course(), name=name)


def make_team(project=None, name="Team A"):
    return core_model("Team").objects.create(project=project or make_project(), name=name)


class ProjectSetupTests(SimpleTestCase):
    def test_manage_py_boots_the_project(self):
        manage_py = REPO_ROOT / "manage.py"
        self.assertTrue(manage_py.exists(), "manage.py is missing from the repo root")

        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        result = subprocess.run(
            [sys.executable, str(manage_py), "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_time_zone_is_new_york(self):
        self.assertEqual(settings.TIME_ZONE, "America/New_York")

    def test_auth_user_model_is_the_custom_user(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "core.User")
        self.assertIs(get_user_model(), core_model("User"))


class MigrationTests(TestCase):
    def test_core_has_migrations(self):
        self.assertIn("core", MigrationLoader(connection).migrated_apps)

    def test_all_core_tables_exist_after_migrating(self):
        # The test runner builds this database from scratch by running migrate.
        tables = connection.introspection.table_names()
        for name in CORE_MODELS:
            with self.subTest(model=name):
                self.assertIn(core_model(name)._meta.db_table, tables)

    def test_models_have_no_unmigrated_changes(self):
        for name in CORE_MODELS:
            core_model(name)
        call_command("makemigrations", "core", check=True, dry_run=True, stdout=StringIO())


class GithubLoginRequiredTests(TestCase):
    def test_validation_rejects_a_blank_login(self):
        user = core_model("User")(username="octocat", github_login="")
        user.set_unusable_password()
        with self.assertRaises(ValidationError) as caught:
            user.full_clean()
        self.assertIn("github_login", caught.exception.message_dict)

    def test_create_user_rejects_a_missing_login(self):
        User = core_model("User")
        with self.assertRaises((ValueError, ValidationError, IntegrityError)), transaction.atomic():
            User.objects.create_user(username="octocat", password="not-a-real-password")
        self.assertEqual(User.objects.count(), 0)

    def test_create_user_rejects_a_blank_login(self):
        User = core_model("User")
        with self.assertRaises((ValueError, ValidationError, IntegrityError)), transaction.atomic():
            User.objects.create_user(
                username="octocat", password="not-a-real-password", github_login=""
            )
        self.assertEqual(User.objects.count(), 0)

    def test_createsuperuser_prompts_for_the_login(self):
        self.assertIn("github_login", core_model("User").REQUIRED_FIELDS)

    def test_createsuperuser_rejects_a_missing_login(self):
        User = core_model("User")
        with mock.patch.dict(os.environ):
            os.environ.pop("DJANGO_SUPERUSER_GITHUB_LOGIN", None)
            with self.assertRaisesMessage(CommandError, "github_login"):
                call_command(
                    "createsuperuser",
                    interactive=False,
                    username="admin",
                    email="admin@example.com",
                    stdout=StringIO(),
                )
        self.assertEqual(User.objects.count(), 0)

    def test_createsuperuser_accepts_a_login(self):
        call_command(
            "createsuperuser",
            interactive=False,
            username="admin",
            email="admin@example.com",
            github_login="octocat",
            stdout=StringIO(),
        )
        user = core_model("User").objects.get(username="admin")
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.github_login, "octocat")


class GithubLoginUniqueTests(TestCase):
    def test_database_rejects_an_identical_login(self):
        make_user("octocat")
        with self.assertRaises(IntegrityError), transaction.atomic():
            core_model("User").objects.create_user(
                username="other", password="not-a-real-password", github_login="octocat"
            )

    def test_database_rejects_a_login_differing_only_by_case(self):
        make_user("Octocat")
        with self.assertRaises(IntegrityError), transaction.atomic():
            core_model("User").objects.create_user(
                username="other", password="not-a-real-password", github_login="octocat"
            )

    def test_validation_rejects_a_login_differing_only_by_case(self):
        make_user("Octocat")
        duplicate = core_model("User")(
            username="other", password="not-a-real-password", github_login="OCTOCAT"
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_different_logins_are_allowed(self):
        make_user("octocat")
        make_user("hubot")
        self.assertEqual(core_model("User").objects.count(), 2)


class EnrollmentRoleTests(TestCase):
    def enroll(self, role, user=None, course=None):
        return core_model("Enrollment").objects.create(
            user=user or make_user("octocat"), course=course or make_course(), role=role
        )

    def test_role_choices_are_exactly_instructor_ta_student(self):
        field = core_model("Enrollment")._meta.get_field("role")
        self.assertEqual({value for value, _ in field.flatchoices}, {"instructor", "ta", "student"})

    def test_validation_accepts_each_role(self):
        user, course = make_user("octocat"), make_course()
        for role in ("instructor", "ta", "student"):
            with self.subTest(role=role):
                enrollment = core_model("Enrollment")(user=user, course=course, role=role)
                enrollment.full_clean()

    def test_validation_rejects_other_roles(self):
        user, course = make_user("octocat"), make_course()
        for role in ("professor", "TA", ""):
            with self.subTest(role=role):
                enrollment = core_model("Enrollment")(user=user, course=course, role=role)
                with self.assertRaises(ValidationError) as caught:
                    enrollment.full_clean()
                self.assertIn("role", caught.exception.message_dict)

    def test_database_rejects_other_roles(self):
        # Choices alone are only enforced by full_clean(), which .create() skips.
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.enroll("professor")

    def test_user_has_no_role_field(self):
        with self.assertRaises(FieldDoesNotExist):
            core_model("User")._meta.get_field("role")

    def test_user_can_hold_different_roles_in_different_courses(self):
        user = make_user("octocat")
        course_a, course_b = make_course("CS 3240, Fall 2026"), make_course("CS 4501, Fall 2026")
        self.enroll("ta", user=user, course=course_a)
        self.enroll("student", user=user, course=course_b)

        Enrollment = core_model("Enrollment")
        self.assertEqual(Enrollment.objects.get(user=user, course=course_a).role, "ta")
        self.assertEqual(Enrollment.objects.get(user=user, course=course_b).role, "student")


class EnrollmentDuplicateTests(TestCase):
    def setUp(self):
        self.Enrollment = core_model("Enrollment")
        self.user = make_user("octocat")
        self.course = make_course()
        self.Enrollment.objects.create(user=self.user, course=self.course, role="student")

    def test_database_rejects_a_duplicate(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Enrollment.objects.create(user=self.user, course=self.course, role="student")

    def test_database_rejects_a_duplicate_with_a_different_role(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Enrollment.objects.create(user=self.user, course=self.course, role="ta")

    def test_validation_rejects_a_duplicate(self):
        with self.assertRaises(ValidationError):
            self.Enrollment(user=self.user, course=self.course, role="ta").full_clean()

    def test_other_users_can_enroll_in_the_same_course(self):
        self.Enrollment.objects.create(user=make_user("hubot"), course=self.course, role="student")
        self.assertEqual(self.Enrollment.objects.filter(course=self.course).count(), 2)


class TeamMembershipDuplicateTests(TestCase):
    def setUp(self):
        self.TeamMembership = core_model("TeamMembership")
        self.user = make_user("octocat")
        self.team = make_team()
        self.TeamMembership.objects.create(user=self.user, team=self.team)

    def test_database_rejects_a_duplicate(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.TeamMembership.objects.create(user=self.user, team=self.team)

    def test_validation_rejects_a_duplicate(self):
        with self.assertRaises(ValidationError):
            self.TeamMembership(user=self.user, team=self.team).full_clean()

    def test_a_team_can_have_several_members(self):
        self.TeamMembership.objects.create(user=make_user("hubot"), team=self.team)
        self.assertEqual(self.TeamMembership.objects.filter(team=self.team).count(), 2)

    def test_a_user_can_join_a_team_in_another_project(self):
        later_project = make_project(course=self.team.project.course, name="Project 2")
        self.TeamMembership.objects.create(user=self.user, team=make_team(project=later_project))
        self.assertEqual(self.TeamMembership.objects.filter(user=self.user).count(), 2)


class RequirementTests(TestCase):
    def setUp(self):
        self.Requirement = core_model("Requirement")
        self.project = make_project()
        self.Requirement.objects.create(project=self.project, number=1, text="Users can log in")

    def test_database_rejects_a_duplicate_number_in_a_project(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Requirement.objects.create(project=self.project, number=1, text="Something else")

    def test_validation_rejects_a_duplicate_number_in_a_project(self):
        with self.assertRaises(ValidationError):
            self.Requirement(project=self.project, number=1, text="Something else").full_clean()

    def test_a_project_can_have_several_numbers(self):
        self.Requirement.objects.create(project=self.project, number=2, text="Users can log out")
        self.assertEqual(self.Requirement.objects.filter(project=self.project).count(), 2)

    def test_another_project_can_reuse_a_number(self):
        other = make_project(course=self.project.course, name="Project 2")
        self.Requirement.objects.create(project=other, number=1, text="Users can log in")
        self.assertEqual(self.Requirement.objects.filter(number=1).count(), 2)

    def test_number_is_an_integer_separate_from_the_database_id(self):
        # Claims' requirement_ids hold Requirement.number, not the database ID.
        field = self.Requirement._meta.get_field("number")
        self.assertIsInstance(field, models.IntegerField)
        self.assertFalse(field.primary_key)

        requirement = self.Requirement.objects.create(
            project=self.project, number=9000, text="Numbered independently of pk"
        )
        self.assertEqual(self.Requirement.objects.get(pk=requirement.pk).number, 9000)
        self.assertNotEqual(requirement.pk, 9000)


class TeamStructureTests(TestCase):
    def test_team_requires_a_project(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            core_model("Team").objects.create(name="Team A")

    def test_team_reaches_its_course_through_its_project(self):
        course = make_course()
        team = make_team(project=make_project(course=course))
        self.assertEqual(team.project.course, course)

    def test_team_has_no_direct_course_field(self):
        with self.assertRaises(FieldDoesNotExist):
            core_model("Team")._meta.get_field("course")

    def test_team_can_have_more_than_one_repo(self):
        Repo = core_model("Repo")
        team = make_team()
        Repo.objects.create(team=team, url="https://github.com/acme/app")
        Repo.objects.create(team=team, url="https://github.com/acme/docs")
        self.assertEqual(Repo.objects.filter(team=team).count(), 2)

    def test_repo_requires_a_team(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            core_model("Repo").objects.create(url="https://github.com/acme/app")
