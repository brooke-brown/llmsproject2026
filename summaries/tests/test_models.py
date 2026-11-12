"""Tests for T-7 (#34): the Summary, Claim and ValidationRun models.

Models are looked up per test rather than imported at module level, so each
criterion passes or fails on its own instead of the whole module failing on a
single ImportError.
"""

from datetime import date, datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from core.models import Course, Project, Repo, Sprint, Team
from ingest.models import Artifact

SUMMARY_MODELS = ["Summary", "Claim", "ValidationRun"]

SPRINT_START = date(2026, 10, 26)
NEXT_SPRINT_START = date(2026, 11, 2)
MID_WEEK = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def summaries_model(name):
    return apps.get_model("summaries", name)


def make_user(login="sample-student-a"):
    return get_user_model().objects.create_user(
        username=login, password="not-a-real-password", github_login=login
    )


def make_team(name="Sample Team A"):
    course = Course.objects.create(name="CS 3240, Fall 2026")
    project = Project.objects.create(course=course, name="Project 1")
    return Team.objects.create(project=project, name=name)


def make_artifact(team, artifact_id="sample-org/app:pr:12", type_="pr"):
    repo, _ = Repo.objects.get_or_create(
        team=team, full_name="sample-org/app", defaults={"url": "https://github.com/sample-org/app"}
    )
    return Artifact.objects.create(
        repo=repo,
        artifact_id=artifact_id,
        type=type_,
        url=f"https://github.com/{artifact_id}",
        timestamp=MID_WEEK,
        author_login="sample-student-a",
    )


def make_sprint(project, start=SPRINT_START):
    """The project's seven-day sprint starting on `start`, made on first use."""
    sprint, _ = Sprint.objects.get_or_create(
        project=project,
        start_date=start,
        defaults={"name": f"Sprint from {start}", "end_date": start + timedelta(days=6)},
    )
    return sprint


def make_summary(team=None, user=None, start=SPRINT_START):
    team = team or make_team()
    return summaries_model("Summary").objects.create(
        team=team, user=user or make_user(), sprint=make_sprint(team.project, start)
    )


def claim_fields(summary, **overrides):
    """Valid field values for a verified claim, which any test can override."""
    fields = {
        "summary": summary,
        "text": "Added the login form and wired it to the session endpoint.",
        "category": "feature",
        "artifact_ids": ["sample-org/app:pr:12"],
        "requirement_ids": [3],
        "significance": "moderate",
        "status": "verified",
    }
    return {**fields, **overrides}


def make_claim(summary, **overrides):
    return summaries_model("Claim").objects.create(**claim_fields(summary, **overrides))


def run_fields(summary, **overrides):
    fields = {
        "summary": summary,
        "claims_total": 10,
        "claims_first_pass": 8,
        "claims_dropped": 1,
        "retries": 2,
    }
    return {**fields, **overrides}


def make_run(summary, **overrides):
    return summaries_model("ValidationRun").objects.create(**run_fields(summary, **overrides))


class SummaryTests(TestCase):
    def test_summary_belongs_to_a_team_a_user_and_a_sprint(self):
        team, user = make_team(), make_user()

        summary = make_summary(team=team, user=user)
        summary.refresh_from_db()

        self.assertEqual(summary.team, team)
        self.assertEqual(summary.user, user)
        self.assertEqual(summary.sprint.start_date, SPRINT_START)

    def test_summaries_are_reachable_from_the_team_and_the_user(self):
        team, user = make_team(), make_user()
        summary = make_summary(team=team, user=user)

        self.assertEqual(list(team.summaries.all()), [summary])
        self.assertEqual(list(user.summaries.all()), [summary])

    def test_summary_records_when_it_was_created(self):
        self.assertIsNotNone(make_summary().created_at)

    def test_a_student_has_one_summary_per_team_and_sprint(self):
        team, user = make_team(), make_user()
        make_summary(team=team, user=user)

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_summary(team=team, user=user)

    def test_a_student_can_have_a_summary_for_another_sprint(self):
        team, user = make_team(), make_user()
        make_summary(team=team, user=user)

        make_summary(team=team, user=user, start=NEXT_SPRINT_START)

        self.assertEqual(team.summaries.count(), 2)

    def test_another_student_can_have_a_summary_for_the_same_sprint(self):
        team = make_team()
        make_summary(team=team, user=make_user("sample-student-a"))

        make_summary(team=team, user=make_user("sample-student-b"))

        self.assertEqual(team.summaries.count(), 2)

    def test_unverified_count_is_the_number_of_claims_that_could_not_be_verified(self):
        summary = make_summary()
        make_claim(summary)
        make_claim(summary, status="couldnt_verify")
        make_claim(summary, status="couldnt_verify")

        self.assertEqual(summary.unverified_count, 2)

    def test_unverified_count_is_zero_when_every_claim_is_verified(self):
        summary = make_summary()
        make_claim(summary)

        self.assertEqual(summary.unverified_count, 0)

    def test_unverified_count_is_zero_for_a_summary_with_no_claims(self):
        self.assertEqual(make_summary().unverified_count, 0)


class ClaimTests(TestCase):
    def test_claim_stores_the_schema_fields(self):
        summary = make_summary()

        claim = make_claim(summary)
        claim.refresh_from_db()

        self.assertEqual(claim.summary, summary)
        self.assertEqual(claim.text, "Added the login form and wired it to the session endpoint.")
        self.assertEqual(claim.category, "feature")
        self.assertEqual(claim.artifact_ids, ["sample-org/app:pr:12"])
        self.assertEqual(claim.requirement_ids, [3])
        self.assertEqual(claim.significance, "moderate")

    def test_claims_are_reachable_from_their_summary(self):
        summary = make_summary()
        claim = make_claim(summary)

        self.assertEqual(list(summary.claims.all()), [claim])

    def test_deleting_a_summary_deletes_its_claims(self):
        summary = make_summary()
        make_claim(summary)

        summary.delete()

        self.assertEqual(summaries_model("Claim").objects.count(), 0)

    def test_every_category_from_the_schema_is_accepted(self):
        summary = make_summary()
        for category in ["feature", "bug fix", "tests", "refactor", "review"]:
            with self.subTest(category=category):
                make_claim(summary, category=category)

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_claim(make_summary(), category="documentation")

    def test_every_significance_from_the_schema_is_accepted(self):
        summary = make_summary()
        for significance in ["minor", "moderate", "major"]:
            with self.subTest(significance=significance):
                make_claim(summary, significance=significance)

    def test_unknown_significance_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_claim(make_summary(), significance="critical")

    def test_status_is_verified_or_couldnt_verify(self):
        summary = make_summary()
        for status in ["verified", "couldnt_verify"]:
            with self.subTest(status=status):
                make_claim(summary, status=status)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_claim(make_summary(), status="pending")

    def test_status_defaults_to_couldnt_verify(self):
        # Nothing is verified until the validator says so.
        fields = claim_fields(make_summary())
        del fields["status"]

        claim = summaries_model("Claim").objects.create(**fields)

        self.assertEqual(claim.status, "couldnt_verify")

    def test_requirement_ids_default_to_an_empty_list(self):
        fields = claim_fields(make_summary())
        del fields["requirement_ids"]

        claim = summaries_model("Claim").objects.create(**fields)

        self.assertEqual(claim.requirement_ids, [])

    def test_claim_links_to_the_artifacts_it_cites(self):
        team = make_team()
        pr = make_artifact(team, "sample-org/app:pr:12")
        commit = make_artifact(team, "sample-org/app:commit:abc123", type_="commit")
        claim = make_claim(
            make_summary(team=team),
            artifact_ids=["sample-org/app:pr:12", "sample-org/app:commit:abc123"],
        )

        claim.artifacts.add(pr, commit)

        self.assertCountEqual(claim.artifacts.all(), [pr, commit])

    def test_an_artifact_knows_which_claims_cite_it(self):
        team = make_team()
        artifact = make_artifact(team)
        claim = make_claim(make_summary(team=team))

        claim.artifacts.add(artifact)

        self.assertEqual(list(artifact.claims.all()), [claim])

    def test_a_claim_can_cite_an_artifact_that_does_not_exist(self):
        # A made-up ID can't be linked to a row, but the claim keeps what was cited.
        claim = make_claim(
            make_summary(), status="couldnt_verify", artifact_ids=["sample-org/app:pr:999"]
        )

        self.assertEqual(claim.artifact_ids, ["sample-org/app:pr:999"])
        self.assertEqual(claim.artifacts.count(), 0)

    def test_a_claim_that_could_not_be_verified_keeps_its_validator_errors(self):
        errors = [
            {
                "artifact_id": "sample-org/app:pr:999",
                "check": "artifact_exists",
                "message": "No such artifact in this team's repos.",
            }
        ]
        claim = make_claim(make_summary(), status="couldnt_verify", validator_errors=errors)

        claim.refresh_from_db()

        self.assertEqual(claim.validator_errors, errors)

    def test_validator_errors_default_to_an_empty_list(self):
        self.assertEqual(make_claim(make_summary()).validator_errors, [])


class ValidationRunTests(TestCase):
    def test_run_stores_its_counts_and_belongs_to_a_summary(self):
        summary = make_summary()

        run = make_run(summary)
        run.refresh_from_db()

        self.assertEqual(run.summary, summary)
        self.assertEqual(run.claims_total, 10)
        self.assertEqual(run.claims_first_pass, 8)
        self.assertEqual(run.claims_dropped, 1)
        self.assertEqual(run.retries, 2)

    def test_run_records_when_it_happened(self):
        self.assertIsNotNone(make_run(make_summary()).created_at)

    def test_a_summary_can_have_several_runs(self):
        summary = make_summary()
        first, second = make_run(summary), make_run(summary, retries=0)

        self.assertCountEqual(summary.validation_runs.all(), [first, second])

    def test_deleting_a_summary_deletes_its_runs(self):
        summary = make_summary()
        make_run(summary)

        summary.delete()

        self.assertEqual(summaries_model("ValidationRun").objects.count(), 0)

    def test_first_pass_rate_is_the_share_of_claims_valid_on_the_first_attempt(self):
        run = make_run(make_summary(), claims_total=10, claims_first_pass=8)

        self.assertEqual(run.first_pass_rate, 0.8)

    def test_drop_rate_is_the_share_of_claims_that_never_verified(self):
        run = make_run(make_summary(), claims_total=10, claims_dropped=1)

        self.assertEqual(run.drop_rate, 0.1)

    def test_rates_are_zero_for_a_run_with_no_claims(self):
        run = make_run(make_summary(), claims_total=0, claims_first_pass=0, claims_dropped=0)

        self.assertEqual(run.first_pass_rate, 0)
        self.assertEqual(run.drop_rate, 0)

    def test_counts_cannot_be_negative(self):
        for field in ["claims_total", "claims_first_pass", "claims_dropped", "retries"]:
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    make_run(make_summary(), **{field: -1})


class MigrationTests(TestCase):
    def test_summaries_has_migrations(self):
        self.assertIn("summaries", MigrationLoader(connection).migrated_apps)

    def test_all_summaries_tables_exist_after_migrating(self):
        # The test runner builds this database from scratch by running migrate.
        tables = connection.introspection.table_names()
        for name in SUMMARY_MODELS:
            with self.subTest(model=name):
                self.assertIn(summaries_model(name)._meta.db_table, tables)

    def test_models_have_no_unmigrated_changes(self):
        for name in SUMMARY_MODELS:
            summaries_model(name)
        call_command("makemigrations", "summaries", check=True, dry_run=True, stdout=StringIO())
