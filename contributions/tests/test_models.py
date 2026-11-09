"""Tests for T-16 (#104): the contributions app and its models.

The models are looked up per test rather than imported at module level, so each criterion
passes or fails on its own instead of the whole module failing on a single ImportError.

The issue says a contribution belongs to sprints by date but doesn't name how to ask. These
tests expect a read-only `Contribution.sprints`, which US-33 will use for its sprint labels.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.urls import reverse

from core.models import Course, Project, Sprint, Team, TeamMembership, User
from ingest.models import Artifact

# 10pm on Sep 3 in New York, but already Sep 4 in UTC, so using UTC's date gets caught.
NOW = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)
TODAY = date(2026, 9, 3)
TOMORROW = date(2026, 9, 4)

CATEGORIES = {"meeting", "design", "user research", "documentation", "planning", "other"}


def contribution_model():
    return apps.get_model("contributions", "Contribution")


def corroboration_model():
    return apps.get_model("contributions", "Corroboration")


def day(number):
    """A date in September 2026, which keeps the sprint tables below readable."""
    return date(2026, 9, number)


def make_student(login):
    return User.objects.create_user(
        username=login, password="not-a-real-password", github_login=login
    )


def freeze(moment=NOW):
    return mock.patch("django.utils.timezone.now", return_value=moment)


class ContributionTestCase(TestCase):
    """Made-up students: A and B share a team, C is on another team in the same project,
    D is on a team in a different project and E isn't on any team."""

    def setUp(self):
        frozen = freeze()
        frozen.start()
        self.addCleanup(frozen.stop)

        self.course = Course.objects.create(name="Course 1")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.other_project = Project.objects.create(course=self.course, name="Project 2")
        self.team = Team.objects.create(project=self.project, name="Team A")
        self.other_team = Team.objects.create(project=self.project, name="Team B")
        self.other_project_team = Team.objects.create(project=self.other_project, name="Team C")

        self.student = make_student("student-a")
        self.teammate = make_student("student-b")
        self.other_team_student = make_student("student-c")
        self.other_project_student = make_student("student-d")
        self.teamless_student = make_student("student-e")

        TeamMembership.objects.create(user=self.student, team=self.team)
        TeamMembership.objects.create(user=self.teammate, team=self.team)
        TeamMembership.objects.create(user=self.other_team_student, team=self.other_team)
        TeamMembership.objects.create(
            user=self.other_project_student, team=self.other_project_team
        )

    def build(self, **overrides):
        """A valid, unsaved contribution by student A."""
        values = {
            "team": self.team,
            "student": self.student,
            "date": day(2),
            "category": "meeting",
            "description": "Ran the sprint planning meeting",
        }
        return contribution_model()(**{**values, **overrides})

    def create(self, **overrides):
        contribution = self.build(**overrides)
        contribution.save()
        return contribution

    def assert_field_refused(self, instance, field):
        with self.assertRaises(ValidationError) as caught:
            instance.full_clean()
        self.assertIn(field, caught.exception.message_dict)


class AppSetupTests(TestCase):
    def test_app_is_installed(self):
        self.assertTrue(apps.is_installed("contributions"))

    def test_migrations_are_in_the_app(self):
        loader = MigrationLoader(connection)
        self.assertTrue([name for name in loader.disk_migrations if name[0] == "contributions"])

    def test_migrations_match_the_models(self):
        try:
            call_command("makemigrations", "contributions", "--check", "--dry-run", verbosity=0)
        except SystemExit:
            self.fail("The contributions models have changes with no migration.")


class ContributionFieldTests(ContributionTestCase):
    def test_stores_every_field(self):
        contribution = self.create(
            date=day(1),
            category="design",
            description="Sketched the checkout flow",
            hours=Decimal("1.50"),
        )
        contribution.refresh_from_db()

        self.assertEqual(contribution.team, self.team)
        self.assertEqual(contribution.student, self.student)
        self.assertEqual(contribution.date, day(1))
        self.assertEqual(contribution.category, "design")
        self.assertEqual(contribution.description, "Sketched the checkout flow")
        self.assertEqual(contribution.hours, Decimal("1.50"))

    def test_team_and_student_are_foreign_keys(self):
        fields = contribution_model()._meta
        self.assertIsInstance(fields.get_field("team"), models.ForeignKey)
        self.assertIs(fields.get_field("team").related_model, Team)
        self.assertIsInstance(fields.get_field("student"), models.ForeignKey)
        self.assertIs(fields.get_field("student").related_model, User)

    def test_date_is_a_date_field(self):
        self.assertIsInstance(contribution_model()._meta.get_field("date"), models.DateField)
        self.assertNotIsInstance(
            contribution_model()._meta.get_field("date"), models.DateTimeField
        )

    def test_category_choices_are_the_six_agreed_ones(self):
        choices = contribution_model()._meta.get_field("category").choices
        self.assertEqual({value for value, _ in choices}, CATEGORIES)

    def test_every_category_is_accepted(self):
        for category in CATEGORIES:
            with self.subTest(category=category):
                self.build(category=category).full_clean()

    def test_unknown_category_is_refused(self):
        self.assert_field_refused(self.build(category="bug fix"), "category")

    def test_description_is_required(self):
        self.assert_field_refused(self.build(description=""), "description")

    def test_hours_are_optional(self):
        contribution = self.build()
        contribution.full_clean()
        contribution.save()
        contribution.refresh_from_db()
        self.assertIsNone(contribution.hours)

    def test_hours_are_a_decimal_field(self):
        self.assertIsInstance(contribution_model()._meta.get_field("hours"), models.DecimalField)

    def test_hours_in_range_are_accepted(self):
        for hours in ("0.25", "1", "7.5", "24"):
            with self.subTest(hours=hours):
                self.build(hours=Decimal(hours)).full_clean()

    def test_hours_out_of_range_are_refused(self):
        for hours in ("-1", "0", "0.24", "24.01", "25"):
            with self.subTest(hours=hours):
                self.assert_field_refused(self.build(hours=Decimal(hours)), "hours")

    def test_created_at_is_set_once_and_updated_at_on_every_save(self):
        later = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        contribution = self.create()
        self.assertEqual(contribution.created_at, NOW)
        self.assertEqual(contribution.updated_at, NOW)

        with freeze(later):
            contribution.description = "Ran the retrospective"
            contribution.save()
        contribution.refresh_from_db()

        self.assertEqual(contribution.created_at, NOW)
        self.assertEqual(contribution.updated_at, later)


class ContributionSprintTests(ContributionTestCase):
    def make_sprint(self, name, start, end, project=None):
        return Sprint.objects.create(
            project=project or self.project, name=name, start_date=start, end_date=end
        )

    def test_has_no_field_linking_it_to_a_sprint(self):
        linked = [
            field.name
            for field in contribution_model()._meta.get_fields()
            if field.related_model is Sprint
        ]
        self.assertEqual(linked, [])

    def test_belongs_to_the_sprint_containing_its_date(self):
        sprint = self.make_sprint("Sprint 1", day(1), day(7))
        self.make_sprint("Sprint 2", day(8), day(14))
        self.assertCountEqual(self.create(date=day(3)).sprints, [sprint])

    def test_both_ends_of_a_sprint_are_included(self):
        sprint = self.make_sprint("Sprint 1", day(2), day(3))
        for number in (2, 3):
            with self.subTest(day=number):
                self.assertCountEqual(self.create(date=day(number)).sprints, [sprint])

    def test_a_day_shared_by_two_sprints_puts_it_in_both(self):
        first = self.make_sprint("Sprint 1", day(1), day(3))
        second = self.make_sprint("Sprint 2", day(3), day(9))
        self.assertCountEqual(self.create(date=day(3)).sprints, [first, second])

    def test_a_date_between_sprints_belongs_to_none(self):
        self.make_sprint("Sprint 1", day(1), day(1))
        self.make_sprint("Sprint 2", day(3), day(9))
        self.assertCountEqual(self.create(date=day(2)).sprints, [])

    def test_can_be_logged_before_any_sprint_is_set_up(self):
        contribution = self.build()
        contribution.full_clean()
        contribution.save()
        self.assertCountEqual(contribution.sprints, [])

    def test_a_sprint_set_up_later_picks_it_up(self):
        contribution = self.create(date=day(3))
        sprint = self.make_sprint("Sprint 1", day(1), day(7))
        self.assertCountEqual(contribution.sprints, [sprint])

    def test_another_projects_sprint_on_the_same_dates_is_ignored(self):
        sprint = self.make_sprint("Sprint 1", day(1), day(7))
        self.make_sprint("Sprint 1", day(1), day(7), project=self.other_project)
        self.assertCountEqual(self.create(date=day(3)).sprints, [sprint])


class ContributionCleanTests(ContributionTestCase):
    def test_valid_contribution_passes(self):
        self.build().clean()

    def test_student_not_on_the_team_is_refused(self):
        for student in (
            self.other_team_student,
            self.other_project_student,
            self.teamless_student,
        ):
            with self.subTest(student=student.github_login):
                with self.assertRaises(ValidationError):
                    self.build(student=student).clean()

    def test_date_in_the_future_is_refused(self):
        with self.assertRaises(ValidationError):
            self.build(date=date(2027, 1, 1)).clean()

    def test_tomorrow_in_new_york_is_refused(self):
        with self.assertRaises(ValidationError):
            self.build(date=TOMORROW).clean()

    def test_today_in_new_york_is_accepted(self):
        self.build(date=TODAY).clean()

    def test_past_date_is_accepted(self):
        self.build(date=date(2026, 1, 15)).clean()

    def test_missing_values_are_reported_rather_than_crashing(self):
        """A form calls clean even after a required field failed, so it can't assume them."""
        cases = {
            "nothing set": {"team": None, "student": None, "date": None},
            "no team": {"team": None},
            "no student": {"student": None},
            "no date": {"date": None},
        }
        for label, missing in cases.items():
            with self.subTest(label):
                values = {
                    "team": self.team,
                    "student": self.student,
                    "date": day(2),
                    "category": "meeting",
                    "description": "Ran the sprint planning meeting",
                }
                for field in missing:
                    del values[field]
                with self.assertRaises(ValidationError):
                    contribution_model()(**values).full_clean()


class CorroborationModelTests(ContributionTestCase):
    def test_stores_its_contribution_and_teammate(self):
        contribution = self.create()
        corroboration = corroboration_model().objects.create(
            contribution=contribution, teammate=self.teammate
        )
        corroboration.refresh_from_db()
        self.assertEqual(corroboration.contribution, contribution)
        self.assertEqual(corroboration.teammate, self.teammate)

    def test_contribution_and_teammate_are_foreign_keys(self):
        fields = corroboration_model()._meta
        self.assertIsInstance(fields.get_field("contribution"), models.ForeignKey)
        self.assertIs(fields.get_field("contribution").related_model, contribution_model())
        self.assertIsInstance(fields.get_field("teammate"), models.ForeignKey)
        self.assertIs(fields.get_field("teammate").related_model, User)

    def test_reached_from_a_contribution_as_corroborations(self):
        contribution = self.create()
        corroboration = corroboration_model().objects.create(
            contribution=contribution, teammate=self.teammate
        )
        self.assertCountEqual(contribution.corroborations.all(), [corroboration])

    def test_created_at_is_set_automatically(self):
        corroboration = corroboration_model().objects.create(
            contribution=self.create(), teammate=self.teammate
        )
        self.assertEqual(corroboration.created_at, NOW)

    def test_database_refuses_a_second_corroboration_by_the_same_teammate(self):
        contribution = self.create()
        corroboration_model().objects.create(contribution=contribution, teammate=self.teammate)
        with self.assertRaises(IntegrityError), transaction.atomic():
            corroboration_model().objects.create(
                contribution=contribution, teammate=self.teammate
            )

    def test_two_teammates_can_corroborate_the_same_contribution(self):
        third = make_student("student-f")
        TeamMembership.objects.create(user=third, team=self.team)
        contribution = self.create()
        corroboration_model().objects.create(contribution=contribution, teammate=self.teammate)
        corroboration_model().objects.create(contribution=contribution, teammate=third)
        self.assertEqual(contribution.corroborations.count(), 2)

    def test_a_teammate_can_corroborate_two_contributions(self):
        first = self.create(description="Ran the sprint planning meeting")
        second = self.create(description="Wrote the onboarding guide")
        corroboration_model().objects.create(contribution=first, teammate=self.teammate)
        corroboration_model().objects.create(contribution=second, teammate=self.teammate)
        self.assertEqual(corroboration_model().objects.filter(teammate=self.teammate).count(), 2)


class CorroborationCleanTests(ContributionTestCase):
    def test_teammate_on_the_team_passes(self):
        corroboration_model()(contribution=self.create(), teammate=self.teammate).clean()

    def test_student_cannot_corroborate_their_own_contribution(self):
        with self.assertRaises(ValidationError):
            corroboration_model()(contribution=self.create(), teammate=self.student).clean()

    def test_someone_not_on_the_team_is_refused(self):
        contribution = self.create()
        for student in (
            self.other_team_student,
            self.other_project_student,
            self.teamless_student,
        ):
            with self.subTest(student=student.github_login):
                with self.assertRaises(ValidationError):
                    corroboration_model()(contribution=contribution, teammate=student).clean()

    def test_missing_values_are_reported_rather_than_crashing(self):
        """A form calls clean even after a required field failed, so it can't assume them."""
        contribution = self.create()
        for label, values in {
            "nothing set": {},
            "no teammate": {"contribution": contribution},
            "no contribution": {"teammate": self.teammate},
        }.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    corroboration_model()(**values).full_clean()


class SeparateFromArtifactsTests(TestCase):
    def test_a_contribution_is_not_an_artifact(self):
        contribution = contribution_model()
        self.assertFalse(issubclass(contribution, Artifact))
        names = [field.name for field in contribution._meta.get_fields()]
        self.assertNotIn("artifact_id", names)
        self.assertEqual(
            [f.name for f in contribution._meta.get_fields() if f.related_model is Artifact], []
        )


class AdminTests(ContributionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(
            User.objects.create_superuser(
                username="admin-user",
                email="admin@example.com",
                password="not-a-real-password",
                github_login="admin-user",
            )
        )

    def test_both_models_are_registered(self):
        self.assertTrue(admin.site.is_registered(contribution_model()))
        self.assertTrue(admin.site.is_registered(corroboration_model()))

    def test_both_lists_open(self):
        corroboration_model().objects.create(contribution=self.create(), teammate=self.teammate)
        for name in ("contribution", "corroboration"):
            with self.subTest(name):
                response = self.client.get(reverse(f"admin:contributions_{name}_changelist"))
                self.assertEqual(response.status_code, 200)

    def test_submitting_a_blank_form_shows_errors(self):
        for name in ("contribution", "corroboration"):
            with self.subTest(name):
                response = self.client.post(reverse(f"admin:contributions_{name}_add"), {})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
