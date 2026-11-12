"""Tests for T-21 (#125), part 2: the alpha's weekly summaries are deleted, and a summary is
always for a sprint.

The migration tests start from the schema just before T-21's migrations, make data with the
models as they were, and run every migration from there.
"""

from datetime import date

from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from summaries.models import Summary
from summaries.tests.sprint_helpers import SprintTestCase

# The last migrations before T-21's: Summary still has week_start, and Requirement its project.
# Ingest's is listed so the old models include every Artifact column.
BEFORE_T21 = [
    ("core", "0005_user_must_change_password"),
    ("ingest", "0002_artifact_review_state"),
    ("summaries", "0004_summary_week_or_sprint"),
]


class T21MigrationTestCase(TransactionTestCase):
    """Migrates back to before T-21 and gives `self.old` the models as they were then."""

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE_T21)
        self.addCleanup(self.migrate_to_latest)
        self.old = executor.loader.project_state(BEFORE_T21).apps

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate(latest)
        return MigrationExecutor(connection).loader.project_state(latest).apps

    def old_model(self, app, name):
        return self.old.get_model(app, name)


class WeeklySummaryMigrationTests(T21MigrationTestCase):
    def setUp(self):
        super().setUp()
        course = self.old_model("core", "Course").objects.create(name="CS 4501")
        project = self.old_model("core", "Project").objects.create(course=course, name="Project 1")
        self.team = self.old_model("core", "Team").objects.create(project=project, name="Team A")
        repo = self.old_model("core", "Repo").objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        self.student = self.old_model("core", "User").objects.create(
            username="ada", github_login="ada-dev"
        )
        self.sprint = self.old_model("core", "Sprint").objects.create(
            project=project, name="Sprint 1", start_date=date(2026, 10, 23),
            end_date=date(2026, 10, 29),
        )
        self.artifact = self.old_model("ingest", "Artifact").objects.create(
            repo=repo, artifact_id="acme/app:commit:aa1", type="commit",
            url="https://github.com/acme/app/commit/aa1", timestamp="2026-10-27T12:00:00Z",
            title="Add login", author_login="ada-dev",
        )
        self.weekly = self.summary(week_start=date(2026, 10, 26))
        self.by_sprint = self.summary(sprint=self.sprint)

    def summary(self, **period):
        summary = self.old_model("summaries", "Summary").objects.create(
            team=self.team, user=self.student, **period
        )
        claim = self.old_model("summaries", "Claim").objects.create(
            summary=summary, text="Added the login form.", category="feature",
            artifact_ids=[self.artifact.artifact_id], significance="minor", status="verified",
        )
        claim.artifacts.add(self.artifact)
        self.old_model("summaries", "ValidationRun").objects.create(
            summary=summary, claims_total=1, claims_first_pass=1, claims_dropped=0, retries=0
        )
        return summary

    def migrate(self):
        new = self.migrate_to_latest()
        return (
            new.get_model("summaries", "Summary"),
            new.get_model("summaries", "Claim"),
            new.get_model("summaries", "ValidationRun"),
        )

    def test_deletes_every_weekly_summary(self):
        Summary_, _, _ = self.migrate()
        self.assertFalse(Summary_.objects.filter(pk=self.weekly.pk).exists())

    def test_deletes_the_weekly_summaries_claims_and_validation_runs(self):
        _, Claim_, ValidationRun_ = self.migrate()
        self.assertFalse(Claim_.objects.filter(summary_id=self.weekly.pk).exists())
        self.assertFalse(ValidationRun_.objects.filter(summary_id=self.weekly.pk).exists())

    def test_keeps_sprint_summaries_with_their_claims_and_runs(self):
        Summary_, Claim_, ValidationRun_ = self.migrate()
        kept = Summary_.objects.get(pk=self.by_sprint.pk)
        self.assertEqual(kept.sprint_id, self.sprint.pk)
        self.assertEqual(Claim_.objects.filter(summary_id=kept.pk).count(), 1)
        self.assertEqual(ValidationRun_.objects.filter(summary_id=kept.pk).count(), 1)

    def test_keeps_the_artifacts_weekly_claims_cited(self):
        new = self.migrate_to_latest()
        Artifact_ = new.get_model("ingest", "Artifact")
        self.assertTrue(Artifact_.objects.filter(pk=self.artifact.pk).exists())


class SummaryIsAlwaysForASprintTests(SprintTestCase):
    def test_week_start_is_gone(self):
        with self.assertRaises(FieldDoesNotExist):
            Summary._meta.get_field("week_start")

    def test_sprint_is_required(self):
        self.assertFalse(Summary._meta.get_field("sprint").null)

    def test_the_database_refuses_a_summary_without_a_sprint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Summary.objects.create(team=self.team, user=self.student)

    def test_only_the_one_per_sprint_constraint_is_left(self):
        names = {constraint.name for constraint in Summary._meta.constraints}
        self.assertEqual(names, {"summaries_summary_unique_per_sprint"})

    def test_it_reads_as_its_sprint(self):
        summary = Summary.objects.create(team=self.team, user=self.student, sprint=self.sprint)
        self.assertIn("Sprint 1", str(summary))
        self.assertNotIn("week", str(summary).lower())

    def test_admin_lists_summaries_by_sprint(self):
        summary_admin = admin.site._registry[Summary]
        for option in (summary_admin.list_display, summary_admin.list_filter, summary_admin.ordering):
            self.assertNotIn("week_start", " ".join(option))
        self.assertIn("sprint", summary_admin.list_display)
