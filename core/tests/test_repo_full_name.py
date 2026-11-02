"""Tests for Repo.full_name, the `owner/repo` name that artifact IDs start with.

Added under T-2 (#13), so Artifact can check its ID against its repo without
parsing URLs.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Course, Project, Repo, Team


def make_team(name="Team A", project_name="Project 1"):
    course = Course.objects.create(name="CS 3240, Fall 2026")
    project = Project.objects.create(course=course, name=project_name)
    return Team.objects.create(project=project, name=name)


class RepoFullNameFormatTests(TestCase):
    def setUp(self):
        self.team = make_team()

    def repo(self, full_name):
        return Repo(team=self.team, url="https://github.com/acme/app", full_name=full_name)

    def test_stores_the_full_name(self):
        created = Repo.objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        self.assertEqual(Repo.objects.get(pk=created.pk).full_name, "acme/app")

    def test_validation_accepts_well_formed_names(self):
        # GitHub allows 39-character owners and 100-character repo names.
        for full_name in ("acme/app", "acme-co/my.app_v2", f"{'o' * 39}/{'r' * 100}"):
            with self.subTest(full_name=full_name):
                self.repo(full_name).full_clean()

    def test_validation_rejects_malformed_names(self):
        cases = [
            "",
            "acme",
            "acme/",
            "/app",
            "acme/app/extra",
            "acme/my app",
            "acme/app:pr",
            "https://github.com/acme/app",
            "acme/app\n",
        ]
        for full_name in cases:
            with self.subTest(full_name=full_name):
                with self.assertRaises(ValidationError) as caught:
                    self.repo(full_name).full_clean()
                self.assertIn("full_name", caught.exception.message_dict)

    def test_database_rejects_a_missing_name(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Repo.objects.create(team=self.team, url="https://github.com/acme/app")

    def test_database_rejects_a_malformed_name(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Repo.objects.create(
                team=self.team, url="https://github.com/acme/app", full_name="acme"
            )


class RepoFullNameUniquenessTests(TestCase):
    def setUp(self):
        self.team = make_team()
        Repo.objects.create(team=self.team, url="https://github.com/acme/app", full_name="acme/app")

    def test_database_rejects_the_same_repo_twice_on_a_team(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Repo.objects.create(
                team=self.team, url="https://github.com/acme/app.git", full_name="acme/app"
            )

    def test_database_rejects_a_name_differing_only_by_case(self):
        # GitHub treats these as the same repo.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Repo.objects.create(
                team=self.team, url="https://github.com/Acme/App", full_name="Acme/App"
            )

    def test_validation_rejects_the_same_repo_twice_on_a_team(self):
        duplicate = Repo(team=self.team, url="https://github.com/acme/app", full_name="ACME/app")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_a_team_can_have_several_repos(self):
        Repo.objects.create(
            team=self.team, url="https://github.com/acme/docs", full_name="acme/docs"
        )
        self.assertEqual(Repo.objects.filter(team=self.team).count(), 2)

    def test_another_team_can_use_the_same_repo(self):
        # A team that keeps its repo gets a new Repo row in the later project.
        later_team = make_team(name="Team A", project_name="Project 2")
        Repo.objects.create(team=later_team, url="https://github.com/acme/app", full_name="acme/app")
        self.assertEqual(Repo.objects.filter(full_name="acme/app").count(), 2)
