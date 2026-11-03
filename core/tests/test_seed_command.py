"""Tests for T-5 (#16): the `seed` management command.

The command runs per test rather than in setUpTestData, so each criterion passes or
fails on its own instead of the whole class failing once during setup.

Nothing here names the seeded course, people or repos, because the demo repos are
still being agreed with Brooke. Each test finds what it needs through the data model.
"""

import os
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from ingest.models import Artifact
from ingest.tests.test_sample_artifacts_fixture import find_non_reserved_emails, find_secrets

BASE_DIR = Path(__file__).resolve().parents[2]
SEED_COMMAND_PATH = BASE_DIR / "core" / "management" / "commands" / "seed.py"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"

PASSWORD_VARIABLE = "SEED_PASSWORD"
ENV_EXAMPLE_PLACEHOLDER = "replace-me"
PASSWORD = "test-only-seed-password"

User = get_user_model()
Role = Enrollment.Role
SEEDED_MODELS = [User, Course, Enrollment, Project, Team, TeamMembership, Repo, Requirement]


def seed(password=PASSWORD):
    """Run the command and return what it printed."""
    stdout = StringIO()
    with mock.patch.dict(os.environ, {PASSWORD_VARIABLE: password}):
        call_command("seed", stdout=stdout, stderr=stdout)
    return stdout.getvalue()


def snapshot():
    """Every row's primary key, so a delete-and-recreate shows up as well as a duplicate."""
    return {
        model.__name__: sorted(model.objects.values_list("pk", flat=True))
        for model in SEEDED_MODELS
    }


def users_with_role(role):
    return User.objects.filter(enrollments__role=role)


class SeededDataTests(TestCase):
    """`manage.py seed` creates each thing the issue lists."""

    def setUp(self):
        seed()
        self.course = Course.objects.get()

    def test_creates_an_instructor_in_the_course(self):
        self.assertTrue(
            Enrollment.objects.filter(course=self.course, role=Role.INSTRUCTOR).exists()
        )

    def test_creates_a_ta_in_the_course(self):
        self.assertTrue(Enrollment.objects.filter(course=self.course, role=Role.TA).exists())

    def test_creates_a_project_in_the_course(self):
        self.assertTrue(Project.objects.filter(course=self.course).exists())

    def test_project_has_requirements(self):
        # The validator checks each claim's requirement_ids against these.
        project = Project.objects.get()
        self.assertGreaterEqual(project.requirements.count(), 2)

    def test_project_has_at_least_two_teams(self):
        self.assertGreaterEqual(Project.objects.get().teams.count(), 2)

    def test_every_team_has_students_enrolled_as_students(self):
        teams = Team.objects.all()
        self.assertTrue(teams)
        for team in teams:
            with self.subTest(team=team.name):
                members = User.objects.filter(team_memberships__team=team)
                self.assertGreaterEqual(members.count(), 2)
                for member in members:
                    self.assertEqual(
                        Enrollment.objects.get(user=member, course=self.course).role,
                        Role.STUDENT,
                    )

    def test_every_student_is_on_exactly_one_team(self):
        students = users_with_role(Role.STUDENT)
        self.assertTrue(students)
        for student in students:
            with self.subTest(student=student.github_login):
                self.assertEqual(student.team_memberships.count(), 1)

    def test_every_team_has_a_repo(self):
        teams = Team.objects.all()
        self.assertTrue(teams)
        for team in teams:
            with self.subTest(team=team.name):
                self.assertTrue(team.repos.exists())

    def test_every_repo_url_matches_its_full_name(self):
        # Only repo setup reads the URL, so a mismatch here would go unnoticed.
        repos = Repo.objects.all()
        self.assertTrue(repos)
        for repo in repos:
            with self.subTest(full_name=repo.full_name):
                self.assertEqual(repo.url, f"https://github.com/{repo.full_name}")

    def test_every_row_passes_model_validation(self):
        # Saving skips full_clean(), so nothing else catches an invalid row.
        for model in SEEDED_MODELS:
            rows = model.objects.all()
            self.assertTrue(rows, f"No {model.__name__} rows")
            for row in rows:
                with self.subTest(model=model.__name__, pk=row.pk):
                    row.full_clean()


class IdempotenceTests(TestCase):
    """Running the command twice creates no duplicates."""

    def test_second_run_creates_and_replaces_nothing(self):
        seed()
        first_run = snapshot()
        seed()
        self.assertEqual(snapshot(), first_run)

    def test_second_run_keeps_artifacts_on_the_seeded_repos(self):
        # Deleting and recreating would leave the same counts, but its cascade would
        # wipe every ingested artifact and summary on a deployed database.
        seed()
        repo = Repo.objects.first()
        artifact = Artifact.objects.create(
            repo=repo,
            artifact_id=f"{repo.full_name}:pr:1",
            type=Artifact.Type.PR,
            url=f"https://github.com/{repo.full_name}/pull/1",
            timestamp=timezone.now(),
        )
        seed()
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_second_run_keeps_requirement_text_edited_in_admin(self):
        # Requirements are editable in admin, and re-seeding shouldn't undo that.
        seed()
        requirement = Requirement.objects.first()
        requirement.text = "Edited in admin"
        requirement.save()
        seed()
        requirement.refresh_from_db()
        self.assertEqual(requirement.text, "Edited in admin")

    def test_leaves_other_courses_alone(self):
        other = Course.objects.create(name="Some Other Course")
        seed()
        seed()
        other.refresh_from_db()
        self.assertEqual(other.name, "Some Other Course")
        self.assertEqual(Course.objects.count(), 2)
        self.assertFalse(other.projects.exists())
        self.assertFalse(other.enrollments.exists())


class PasswordTests(TestCase):
    """Passwords come from the environment."""

    def test_instructor_and_ta_use_the_password_from_the_environment(self):
        seed("first-test-only-password")
        for role in (Role.INSTRUCTOR, Role.TA):
            staff = users_with_role(role)
            self.assertTrue(staff)
            for user in staff:
                with self.subTest(role=role, user=user.username):
                    self.assertTrue(user.check_password("first-test-only-password"))

    def test_second_run_applies_a_changed_password(self):
        # Otherwise rotating the variable would silently do nothing.
        seed("first-test-only-password")
        seed("second-test-only-password")
        for user in users_with_role(Role.INSTRUCTOR):
            with self.subTest(user=user.username):
                self.assertTrue(user.check_password("second-test-only-password"))
                self.assertFalse(user.check_password("first-test-only-password"))

    def test_students_cannot_log_in(self):
        # Students have no pages in the alpha, so they don't need a password at all.
        seed()
        students = users_with_role(Role.STUDENT)
        self.assertTrue(students)
        for student in students:
            with self.subTest(student=student.github_login):
                self.assertFalse(student.has_usable_password())

    def test_seeded_instructor_and_ta_can_log_in_and_open_the_course(self):
        seed()
        course = Course.objects.get()
        url = reverse("dashboard:course_overview", args=[course.id])
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                user = users_with_role(role).first()
                self.assertTrue(self.client.login(username=user.username, password=PASSWORD))
                self.assertEqual(self.client.get(url).status_code, 200)
                self.client.logout()

    def test_does_not_print_the_password(self):
        self.assertNotIn(PASSWORD, seed())

    def test_missing_password_stops_with_an_error_naming_the_variable(self):
        without_password = {k: v for k, v in os.environ.items() if k != PASSWORD_VARIABLE}
        with mock.patch.dict(os.environ, without_password, clear=True):
            # Matching the name matters: an unknown command is a CommandError too.
            with self.assertRaisesRegex(CommandError, PASSWORD_VARIABLE):
                call_command("seed", stdout=StringIO())

    def test_blank_or_placeholder_password_stops_with_an_error(self):
        # The placeholder is committed, so an account using it would have a public password.
        for value in ("", "   ", ENV_EXAMPLE_PLACEHOLDER):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {PASSWORD_VARIABLE: value}):
                    with self.assertRaisesRegex(CommandError, PASSWORD_VARIABLE):
                        call_command("seed", stdout=StringIO())

    def test_refusing_a_password_writes_nothing(self):
        before = snapshot()
        with mock.patch.dict(os.environ, {PASSWORD_VARIABLE: ""}):
            with self.assertRaisesRegex(CommandError, PASSWORD_VARIABLE):
                call_command("seed", stdout=StringIO())
        self.assertEqual(snapshot(), before)


class EnvExampleTests(SimpleTestCase):
    def test_documents_the_password_variable_with_a_placeholder(self):
        lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{PASSWORD_VARIABLE}={ENV_EXAMPLE_PLACEHOLDER}", lines)


class NoCommittedSecretsTests(SimpleTestCase):
    """Scans the command's source, so nothing slips through in a default or a comment."""

    def setUp(self):
        self.assertTrue(SEED_COMMAND_PATH.is_file(), f"Missing {SEED_COMMAND_PATH}")
        self.text = SEED_COMMAND_PATH.read_text(encoding="utf-8")

    def test_contains_no_tokens(self):
        self.assertEqual(find_secrets(self.text), [])

    def test_uses_only_reserved_email_addresses(self):
        self.assertEqual(find_non_reserved_emails(self.text), [])

    def test_reads_the_password_from_the_environment_variable(self):
        self.assertIn(PASSWORD_VARIABLE, self.text)


class NoRealPersonalDataTests(TestCase):
    def test_seeded_emails_are_blank_or_reserved(self):
        # A login identifies an account anyone can look up. An email address is private,
        # and matching a commit to a student never needs one.
        seed()
        users = User.objects.all()
        self.assertTrue(users)
        for user in users:
            with self.subTest(user=user.username):
                self.assertEqual(find_non_reserved_emails(user.email), [])
