"""Tests for T-2 (#13): the Artifact model.

The model is looked up per test rather than imported at module level, so each
criterion passes or fails on its own instead of the whole module failing on a
single ImportError.
"""

from datetime import datetime, timezone
from io import StringIO
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from core.models import Course, Project, Repo, Team

TIMESTAMP = datetime(2026, 10, 28, 14, 30, tzinfo=timezone.utc)


def artifact_model():
    return apps.get_model("ingest", "Artifact")


def add_repo(team, full_name):
    return Repo.objects.create(
        team=team, url=f"https://github.com/{full_name}", full_name=full_name
    )


def make_repo(full_name="acme/app"):
    course = Course.objects.create(name="CS 3240, Fall 2026")
    project = Project.objects.create(course=course, name="Project 1")
    team = Team.objects.create(project=project, name="Team A")
    return add_repo(team, full_name)


def artifact_fields(repo, **overrides):
    """Valid field values for a PR, which any test can override."""
    fields = {
        "repo": repo,
        "artifact_id": "acme/app:pr:12",
        "type": "pr",
        "url": "https://github.com/acme/app/pull/12",
        "timestamp": TIMESTAMP,
        "title": "Add login page",
        "body": "Closes #3",
        "author_login": "octocat",
    }
    return {**fields, **overrides}


class SetupTests(SimpleTestCase):
    def test_ingest_is_an_installed_app(self):
        self.assertIn("ingest", settings.INSTALLED_APPS)


class MigrationTests(TestCase):
    def test_artifact_table_exists_after_migrating(self):
        # The test runner builds this database from scratch by running migrate.
        self.assertIn(artifact_model()._meta.db_table, connection.introspection.table_names())

    def test_model_has_no_unmigrated_changes(self):
        artifact_model()
        call_command("makemigrations", "ingest", check=True, dry_run=True, stdout=StringIO())


class ArtifactTypeTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def test_type_choices_are_exactly_commit_pr_review_issue(self):
        field = self.Artifact._meta.get_field("type")
        self.assertEqual({value for value, _ in field.flatchoices}, {"commit", "pr", "review", "issue"})

    def test_validation_accepts_each_type(self):
        for type_ in ("commit", "pr", "review", "issue"):
            with self.subTest(type=type_):
                artifact = self.Artifact(
                    **artifact_fields(self.repo, type=type_, artifact_id=f"acme/app:{type_}:12")
                )
                artifact.full_clean()

    def test_validation_rejects_other_types(self):
        for type_ in ("pull_request", "PR", ""):
            with self.subTest(type=type_):
                artifact = self.Artifact(**artifact_fields(self.repo, type=type_))
                with self.assertRaises(ValidationError) as caught:
                    artifact.full_clean()
                self.assertIn("type", caught.exception.message_dict)

    def test_database_rejects_other_types(self):
        # Choices alone are only enforced by full_clean(), which .create() skips.
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Artifact.objects.create(**artifact_fields(self.repo, type="wiki"))


class ArtifactRepoTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()

    def test_artifact_requires_a_repo(self):
        fields = artifact_fields(None)
        del fields["repo"]
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Artifact.objects.create(**fields)

    def test_repo_lists_its_artifacts(self):
        repo = make_repo()
        artifact = self.Artifact.objects.create(**artifact_fields(repo))
        self.assertEqual(list(repo.artifacts.all()), [artifact])


class ArtifactStoredFieldsTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def test_stores_each_field(self):
        created = self.Artifact.objects.create(**artifact_fields(self.repo))
        artifact = self.Artifact.objects.get(pk=created.pk)

        self.assertEqual(artifact.repo, self.repo)
        self.assertEqual(artifact.artifact_id, "acme/app:pr:12")
        self.assertEqual(artifact.type, "pr")
        self.assertEqual(artifact.url, "https://github.com/acme/app/pull/12")
        self.assertEqual(artifact.timestamp, TIMESTAMP)
        self.assertEqual(artifact.title, "Add login page")
        self.assertEqual(artifact.body, "Closes #3")
        self.assertEqual(artifact.author_login, "octocat")

    def test_timestamp_keeps_the_exact_instant_across_time_zones(self):
        # 11pm Sunday in New York is already Monday in UTC. The reporting week
        # depends on this instant surviving the round trip.
        moment = datetime(2026, 11, 1, 23, 0, tzinfo=ZoneInfo("America/New_York"))
        created = self.Artifact.objects.create(**artifact_fields(self.repo, timestamp=moment))
        self.assertEqual(self.Artifact.objects.get(pk=created.pk).timestamp, moment)

    def test_database_requires_a_timestamp(self):
        fields = artifact_fields(self.repo)
        del fields["timestamp"]
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Artifact.objects.create(**fields)

    def test_validation_requires_a_url(self):
        artifact = self.Artifact(**artifact_fields(self.repo, url=""))
        with self.assertRaises(ValidationError) as caught:
            artifact.full_clean()
        self.assertIn("url", caught.exception.message_dict)

    def test_validation_rejects_a_malformed_url(self):
        artifact = self.Artifact(**artifact_fields(self.repo, url="not a url"))
        with self.assertRaises(ValidationError) as caught:
            artifact.full_clean()
        self.assertIn("url", caught.exception.message_dict)

    def test_title_and_body_may_be_blank(self):
        # A review has neither a title nor, when it's a bare approval, a body.
        artifact = self.Artifact(
            **artifact_fields(
                self.repo,
                type="review",
                artifact_id="acme/app:review:99",
                title="",
                body="",
            )
        )
        artifact.full_clean()

    def test_a_long_commit_message_fits_in_the_title(self):
        artifact = self.Artifact(
            **artifact_fields(
                self.repo,
                type="commit",
                artifact_id="acme/app:commit:abc123",
                title="x" * 5000,
            )
        )
        artifact.full_clean()

    def test_the_longest_real_github_values_fit(self):
        # GitHub allows 39-character owners and 100-character repo names.
        full_name = f"{'o' * 39}/{'r' * 100}"
        sha = "a" * 40
        artifact = self.Artifact(
            **artifact_fields(
                add_repo(self.repo.team, full_name),
                type="commit",
                artifact_id=f"{full_name}:commit:{sha}",
                url=f"https://github.com/{full_name}/commit/{sha}",
            )
        )
        artifact.full_clean()


class ArtifactIdFormatTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def test_validation_accepts_well_formed_ids(self):
        cases = [
            ("acme/app:pr:12", "pr"),
            ("acme/app:issue:7", "issue"),
            ("acme/app:review:2043511", "review"),
            ("acme/app:commit:0a1b2c3d4e5f60718293a4b5c6d7e8f901234567", "commit"),
        ]
        for artifact_id, type_ in cases:
            with self.subTest(artifact_id=artifact_id):
                self.Artifact(
                    **artifact_fields(self.repo, artifact_id=artifact_id, type=type_)
                ).full_clean()

    def test_validation_accepts_punctuation_in_owner_and_repo_names(self):
        repo = add_repo(self.repo.team, "acme-co/my.app_v2")
        self.Artifact(**artifact_fields(repo, artifact_id="acme-co/my.app_v2:pr:12")).full_clean()

    def test_validation_rejects_malformed_ids(self):
        cases = [
            "",
            "12",
            "acme/app",
            "acme/app:12",
            "acme:pr:12",
            "app:pr:12",
            "/app:pr:12",
            "acme/:pr:12",
            "acme/app/extra:pr:12",
            "acme/app:pull_request:12",
            "acme/app:PR:12",
            "acme/app:pr:",
            "acme/app:pr:12:extra",
            "acme/my app:pr:12",
            "acme/app:pr:12\n",
        ]
        for artifact_id in cases:
            with self.subTest(artifact_id=artifact_id):
                artifact = self.Artifact(**artifact_fields(self.repo, artifact_id=artifact_id))
                with self.assertRaises(ValidationError) as caught:
                    artifact.full_clean()
                self.assertIn("artifact_id", caught.exception.message_dict)

    def test_database_rejects_a_malformed_id(self):
        # Claims cite these IDs, and .create() and bulk_create() skip full_clean().
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Artifact.objects.create(**artifact_fields(self.repo, artifact_id="12"))

    def test_validation_rejects_an_id_whose_type_differs_from_the_type_field(self):
        artifact = self.Artifact(
            **artifact_fields(self.repo, artifact_id="acme/app:issue:12", type="pr")
        )
        with self.assertRaises(ValidationError):
            artifact.full_clean()


class ArtifactIdMatchesRepoTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo("acme/app")

    def test_validation_rejects_an_id_from_a_different_repo(self):
        # Compared exactly, so every ID for a repo is spelled the same way and a
        # claim's citation can always find its artifact.
        for artifact_id in ("acme/docs:pr:12", "other/app:pr:12", "acme/app2:pr:12", "ACME/app:pr:12"):
            with self.subTest(artifact_id=artifact_id):
                artifact = self.Artifact(**artifact_fields(self.repo, artifact_id=artifact_id))
                with self.assertRaises(ValidationError) as caught:
                    artifact.full_clean()
                self.assertIn("artifact_id", caught.exception.message_dict)

    def test_validation_accepts_an_id_from_its_own_repo(self):
        self.Artifact(**artifact_fields(self.repo, artifact_id="acme/app:pr:12")).full_clean()

    def test_validation_reports_a_missing_repo_instead_of_crashing(self):
        # full_clean() still runs clean() when field validation has failed.
        fields = artifact_fields(None)
        del fields["repo"]
        with self.assertRaises(ValidationError) as caught:
            self.Artifact(**fields).full_clean()
        self.assertIn("repo", caught.exception.message_dict)


class ArtifactIdUniquenessTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()
        self.Artifact.objects.create(**artifact_fields(self.repo))

    def test_database_rejects_a_duplicate_id(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.Artifact.objects.create(**artifact_fields(self.repo, title="Another copy"))

    def test_validation_rejects_a_duplicate_id(self):
        with self.assertRaises(ValidationError):
            self.Artifact(**artifact_fields(self.repo, title="Another copy")).full_clean()

    def test_a_later_project_reusing_the_github_repo_can_ingest_the_same_artifact(self):
        # A team that keeps its repo gets a new Repo row in the new project. Each
        # row needs its own copy, so past projects' reports are never touched.
        later_project = Project.objects.create(course=self.repo.team.project.course, name="Project 2")
        later_team = Team.objects.create(project=later_project, name="Team A")
        later_repo = add_repo(later_team, self.repo.full_name)

        artifact = self.Artifact(**artifact_fields(later_repo))
        artifact.full_clean()
        artifact.save()

        self.assertEqual(self.Artifact.objects.filter(artifact_id="acme/app:pr:12").count(), 2)
        self.assertEqual(self.Artifact.objects.get(repo__team=later_team).pk, artifact.pk)

    def test_the_same_number_is_allowed_in_another_repo(self):
        other_repo = add_repo(self.repo.team, "acme/docs")
        self.Artifact.objects.create(
            **artifact_fields(
                other_repo,
                artifact_id="acme/docs:pr:12",
                url="https://github.com/acme/docs/pull/12",
            )
        )
        self.assertEqual(self.Artifact.objects.count(), 2)

    def test_the_same_number_is_allowed_for_another_type(self):
        self.Artifact.objects.create(
            **artifact_fields(self.repo, artifact_id="acme/app:review:12", type="review")
        )
        self.assertEqual(self.Artifact.objects.count(), 2)


class ArtifactGitAuthorTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def commit(self, **overrides):
        return artifact_fields(
            self.repo,
            type="commit",
            artifact_id="acme/app:commit:abc123",
            url="https://github.com/acme/app/commit/abc123",
            **overrides,
        )

    def test_stores_the_raw_git_author_name_and_email(self):
        created = self.Artifact.objects.create(
            **self.commit(git_author_name="Mona Lisa", git_author_email="mona@example.com")
        )
        artifact = self.Artifact.objects.get(pk=created.pk)
        self.assertEqual(artifact.git_author_name, "Mona Lisa")
        self.assertEqual(artifact.git_author_email, "mona@example.com")

    def test_author_login_may_be_empty(self):
        # A commit whose git email isn't linked to a GitHub account has no login.
        artifact = self.Artifact(
            **self.commit(
                author_login="",
                git_author_name="Mona Lisa",
                git_author_email="mona@laptop.local",
            )
        )
        artifact.full_clean()
        artifact.save()
        self.assertEqual(self.Artifact.objects.get(pk=artifact.pk).author_login, "")

    def test_author_login_defaults_to_empty(self):
        fields = self.commit()
        del fields["author_login"]
        self.assertEqual(self.Artifact.objects.create(**fields).author_login, "")

    def test_git_author_email_is_stored_raw_even_if_not_a_valid_address(self):
        # Git doesn't validate this value, and a bad one must not block ingest.
        artifact = self.Artifact(**self.commit(git_author_email="root@(none)"))
        artifact.full_clean()

    def test_git_author_fields_may_be_blank(self):
        # PRs, reviews and issues have no git author.
        artifact = self.Artifact(**artifact_fields(self.repo))
        artifact.full_clean()
        self.assertEqual(artifact.git_author_name, "")
        self.assertEqual(artifact.git_author_email, "")


class ArtifactDiffTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def commit(self, **overrides):
        return artifact_fields(
            self.repo,
            type="commit",
            artifact_id="acme/app:commit:abc123",
            url="https://github.com/acme/app/commit/abc123",
            **overrides,
        )

    def test_stores_a_diff_and_whether_it_was_truncated(self):
        created = self.Artifact.objects.create(
            **self.commit(diff="--- a/app.py\n+++ b/app.py\n", diff_truncated=True)
        )
        artifact = self.Artifact.objects.get(pk=created.pk)
        self.assertEqual(artifact.diff, "--- a/app.py\n+++ b/app.py\n")
        self.assertIs(artifact.diff_truncated, True)

    def test_diff_fields_default_to_empty_and_not_truncated(self):
        # PRs, reviews and issues have no diff of their own.
        artifact = self.Artifact(**artifact_fields(self.repo))
        artifact.full_clean()
        self.assertEqual(artifact.diff, "")
        self.assertIs(artifact.diff_truncated, False)

    def test_a_large_diff_fits(self):
        # Ingest sets the truncation limit, so the model mustn't impose its own.
        self.Artifact(**self.commit(diff="x" * 200_000)).full_clean()


class ArtifactCoAuthorTests(TestCase):
    def setUp(self):
        self.Artifact = artifact_model()
        self.repo = make_repo()

    def test_stores_co_author_logins(self):
        created = self.Artifact.objects.create(
            **artifact_fields(self.repo, co_author_logins=["hubot", "mona"])
        )
        self.assertEqual(
            self.Artifact.objects.get(pk=created.pk).co_author_logins, ["hubot", "mona"]
        )

    def test_stores_co_authors_that_could_not_be_matched(self):
        unmatched = [{"name": "Sam Doe", "email": "sam@example.com"}]
        created = self.Artifact.objects.create(
            **artifact_fields(self.repo, unmatched_co_authors=unmatched)
        )
        self.assertEqual(self.Artifact.objects.get(pk=created.pk).unmatched_co_authors, unmatched)

    def test_co_authors_default_to_empty_lists_and_pass_validation(self):
        artifact = self.Artifact(**artifact_fields(self.repo))
        artifact.full_clean()
        self.assertEqual(artifact.co_author_logins, [])
        self.assertEqual(artifact.unmatched_co_authors, [])

    def test_artifacts_do_not_share_a_default_list(self):
        first = self.Artifact(**artifact_fields(self.repo))
        second = self.Artifact(**artifact_fields(self.repo))
        first.co_author_logins.append("hubot")
        first.unmatched_co_authors.append({"name": "Sam Doe", "email": "sam@example.com"})
        self.assertEqual(second.co_author_logins, [])
        self.assertEqual(second.unmatched_co_authors, [])
