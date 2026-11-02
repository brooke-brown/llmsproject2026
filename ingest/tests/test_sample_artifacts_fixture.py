"""Tests for T-3 (#14): the sample artifact fixture.

The fixture is loaded per test rather than through the `fixtures` class attribute,
so each criterion passes or fails on its own instead of the whole class failing
once in setUpClass.

Each special case is valid in every way but one, so that a validator check can be
tested on its own: an artifact outside the week would otherwise be creditable to
the target student, for example.
"""

import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from core.models import Enrollment, Repo, Requirement, Team, TeamMembership
from ingest.models import Artifact

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_artifacts.json"

# What the fixture is written around. Tests built on it can use these same values.
TARGET_TEAM = "Sample Team A"
TARGET_STUDENT = "sample-student-a"
TARGET_WEEK_START = date(2026, 10, 26)  # A Monday.
TARGET_WEEK_END = date(2026, 11, 1)  # The following Sunday.
REPORTING_TIME_ZONE = ZoneInfo("America/New_York")

# Everything in the fixture is invented, which is how it uses only data from people
# who have given permission: no real person's login, repo or email address appears.
SYNTHETIC_NAME_PREFIX = "sample-"

SECRET_PATTERNS = [
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",  # GitHub tokens.
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bsk-[A-Za-z0-9_-]{20,}",  # LLM API keys.
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS access keys.
    r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}",
    r"://[^/\s\"@]+@",  # Credentials embedded in a URL.
]
EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def find_secrets(text):
    return [match for pattern in SECRET_PATTERNS for match in re.findall(pattern, text)]


def find_non_reserved_emails(text):
    """Email addresses outside example.com, which is reserved and so can't be anyone's."""
    return [
        email
        for email in re.findall(EMAIL_PATTERN, text)
        if not re.search(r"@([A-Za-z0-9-]+\.)*example\.com$", email, re.IGNORECASE)
    ]


def in_target_week(artifact):
    local_date = artifact.timestamp.astimezone(REPORTING_TIME_ZONE).date()
    return TARGET_WEEK_START <= local_date <= TARGET_WEEK_END


def load_fixture():
    call_command("loaddata", "sample_artifacts", verbosity=0)


class LoadingTests(TestCase):
    def test_loads_on_a_freshly_migrated_database_with_no_other_setup(self):
        # The test runner builds this database from scratch by running migrate.
        self.assertFalse(Artifact.objects.exists())
        load_fixture()
        self.assertTrue(Artifact.objects.exists())

    def test_file_is_in_the_agreed_location(self):
        self.assertTrue(FIXTURE_PATH.is_file(), f"Missing {FIXTURE_PATH}")


class ValidityTests(TestCase):
    """loaddata skips full_clean(), so nothing else catches an invalid row."""

    def setUp(self):
        load_fixture()

    def test_every_artifact_passes_model_validation(self):
        artifacts = Artifact.objects.all()
        self.assertTrue(artifacts)
        for artifact in artifacts:
            with self.subTest(artifact_id=artifact.artifact_id):
                artifact.full_clean()

    def test_every_repo_passes_model_validation(self):
        repos = Repo.objects.all()
        self.assertTrue(repos)
        for repo in repos:
            with self.subTest(full_name=repo.full_name):
                repo.full_clean()

    def test_every_artifact_url_points_into_its_own_repo(self):
        # Claims render these as links, and cite the artifact the link belongs to.
        artifacts = Artifact.objects.select_related("repo")
        self.assertTrue(artifacts)
        for artifact in artifacts:
            with self.subTest(artifact_id=artifact.artifact_id):
                self.assertTrue(
                    artifact.url.startswith(f"https://github.com/{artifact.repo.full_name}/")
                )


class TargetScenarioTests(TestCase):
    """Sets up the team, student and week that the case tests below refer to."""

    def setUp(self):
        load_fixture()
        self.team = Team.objects.get(name=TARGET_TEAM)
        self.student = get_user_model().objects.get(github_login=TARGET_STUDENT)
        self.team_artifacts = Artifact.objects.filter(repo__team=self.team)

    def matching(self, artifacts, predicate):
        return [artifact for artifact in artifacts if predicate(artifact)]

    def test_target_student_is_a_student_on_the_target_team(self):
        self.assertTrue(TeamMembership.objects.filter(user=self.student, team=self.team).exists())
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.student, course=self.team.project.course, role="student"
            ).exists()
        )

    def test_target_project_has_requirements(self):
        # The validator checks each claim's requirement_ids against these.
        self.assertGreaterEqual(Requirement.objects.filter(project=self.team.project).count(), 2)

    def test_includes_each_artifact_type_creditable_to_the_target_student(self):
        for type_ in ("commit", "pr", "review", "issue"):
            with self.subTest(type=type_):
                self.assertTrue(
                    self.matching(
                        self.team_artifacts.filter(type=type_, author_login=TARGET_STUDENT),
                        in_target_week,
                    )
                )

    def test_includes_a_commit_co_authored_by_the_target_student(self):
        # Someone else is the author, so only the co-author credit can match.
        found = self.matching(
            self.team_artifacts.filter(type="commit").exclude(author_login=TARGET_STUDENT),
            lambda artifact: in_target_week(artifact)
            and TARGET_STUDENT in artifact.co_author_logins,
        )
        self.assertTrue(found)

    def test_includes_an_unattributed_commit(self):
        # No GitHub login, but git still recorded who made it.
        found = self.matching(
            self.team_artifacts.filter(type="commit", author_login=""),
            lambda artifact: in_target_week(artifact)
            and artifact.git_author_name
            and artifact.git_author_email,
        )
        self.assertTrue(found)

    def test_includes_an_artifact_outside_the_target_week(self):
        found = self.matching(
            self.team_artifacts.filter(author_login=TARGET_STUDENT),
            lambda artifact: not in_target_week(artifact),
        )
        self.assertTrue(found)

    def test_includes_an_artifact_by_a_different_student_on_the_team(self):
        teammates = (
            TeamMembership.objects.filter(
                team=self.team,
                user__enrollments__course=self.team.project.course,
                user__enrollments__role="student",
            )
            .exclude(user=self.student)
            .values_list("user__github_login", flat=True)
        )
        found = self.matching(
            self.team_artifacts.filter(author_login__in=list(teammates)),
            lambda artifact: in_target_week(artifact)
            and TARGET_STUDENT not in artifact.co_author_logins,
        )
        self.assertTrue(found)

    def test_includes_an_artifact_from_another_teams_repo(self):
        found = self.matching(
            Artifact.objects.exclude(repo__team=self.team).filter(author_login=TARGET_STUDENT),
            in_target_week,
        )
        self.assertTrue(found)


class SyntheticDataTests(TestCase):
    """Every person and repo in the fixture must be invented."""

    def setUp(self):
        load_fixture()

    def assert_all_synthetic(self, names):
        names = list(names)
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(name.startswith(SYNTHETIC_NAME_PREFIX))

    def test_user_logins_are_synthetic(self):
        self.assert_all_synthetic(get_user_model().objects.values_list("github_login", flat=True))

    def test_repo_names_are_synthetic(self):
        self.assert_all_synthetic(Repo.objects.values_list("full_name", flat=True))

    def test_artifact_logins_are_synthetic(self):
        logins = []
        for artifact in Artifact.objects.all():
            logins.extend(artifact.co_author_logins)
            if artifact.author_login:
                logins.append(artifact.author_login)
        self.assert_all_synthetic(logins)

    def test_no_user_has_a_usable_password(self):
        users = get_user_model().objects.all()
        self.assertTrue(users)
        for user in users:
            with self.subTest(user=user.github_login):
                self.assertFalse(user.has_usable_password())


class RawFileTests(SimpleTestCase):
    """Scans the text itself, so nothing slips through in a body, diff or trailer."""

    def setUp(self):
        self.text = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_contains_no_tokens(self):
        self.assertEqual(find_secrets(self.text), [])

    def test_uses_only_reserved_email_addresses(self):
        self.assertEqual(find_non_reserved_emails(self.text), [])


class ScannerTests(SimpleTestCase):
    """The raw file tests would pass vacuously if these helpers matched nothing."""

    def test_target_week_runs_monday_to_sunday(self):
        self.assertEqual(TARGET_WEEK_START.weekday(), 0)
        self.assertEqual(TARGET_WEEK_END.weekday(), 6)
        self.assertEqual((TARGET_WEEK_END - TARGET_WEEK_START).days, 6)

    def test_finds_each_kind_of_secret(self):
        # Built up from parts so that this file doesn't trip secret scanners itself.
        filler = "a1" * 18
        cases = [
            "ghp_" + filler,
            "ghs_" + filler,
            "github_pat_" + filler,
            "sk-" + filler,
            "AKIA" + "A1B2C3D4E5F6G7H8",
            "Bearer " + filler,
            "https://someone:" + filler + "@github.com/acme/app",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(find_secrets(text))

    def test_ignores_ordinary_text_and_urls(self):
        text = '{"url": "https://github.com/sample-org/app/pull/12", "title": "Add task-list"}'
        self.assertEqual(find_secrets(text), [])

    def test_flags_real_looking_email_addresses(self):
        for email in ("someone@virginia.edu", "someone@gmail.com", "someone@example.com.evil.org"):
            with self.subTest(email=email):
                self.assertEqual(find_non_reserved_emails(email), [email])

    def test_allows_reserved_email_addresses(self):
        for email in ("someone@example.com", "someone@laptop.example.com"):
            with self.subTest(email=email):
                self.assertEqual(find_non_reserved_emails(email), [])
