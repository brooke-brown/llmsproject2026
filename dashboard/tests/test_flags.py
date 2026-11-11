"""Tests for T-11 (#56): the Flag model and compute_flags.

The model and compute_flags are looked up per test rather than imported at module
level, so each criterion passes or fails on its own instead of the whole module
failing on a single ImportError.
"""

import inspect
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Course, Project, Team, TeamMembership

from .sprint_helpers import flaggable_sprint, sprint_for



def flag_model():
    return apps.get_model("dashboard", "Flag")


def compute_flags(team):
    from dashboard.flags import compute_flags

    return compute_flags(team)


def make_user(login="sample-student-a"):
    return get_user_model().objects.create_user(
        username=login, password="not-a-real-password", github_login=login
    )


def make_team(name="Sample Team A"):
    course = Course.objects.create(name="CS 3240, Fall 2026")
    project = Project.objects.create(course=course, name="Project 1")
    return Team.objects.create(project=project, name=name)


def make_flaggable_team(name="Sample Team A"):
    """A team with a student and a finished sprint they have a complete summary for, so
    compute_flags runs its rules for that sprint."""
    team = make_team(name)
    TeamMembership.objects.create(user=make_user(f"sample-{name.split()[-1].lower()}"), team=team)
    flaggable_sprint(team)
    return team


def make_flag(team, save=True, **fields):
    values = {
        "rule": "sample-rule",
        "reason": "sample-student-a has 2 of the team's 30 points in Sprint 1.",
        "severity": "flag",
        **fields,
    }
    if "sprint" not in values:
        values["sprint"] = sprint_for(team.project)
    flag = flag_model()(team=team, **values)
    if save:
        flag.save()
    return flag


def rule_raising(*flag_fields):
    """A stand-in rule that raises one unsaved flag per dict of fields."""

    def rule(team, sprint):
        return [make_flag(team, save=False, sprint=sprint, **fields) for fields in flag_fields]

    return rule


class FlagModelTests(TestCase):
    def test_flag_stores_its_fields(self):
        team, user = make_team(), make_user()

        flag = make_flag(team, user=user, rule="low-share-week", severity="warning")
        flag.refresh_from_db()

        self.assertEqual(flag.team, team)
        self.assertEqual(flag.user, user)
        self.assertEqual(flag.rule, "low-share-week")
        self.assertEqual(flag.reason, "sample-student-a has 2 of the team's 30 points in Sprint 1.")
        self.assertEqual(flag.severity, "warning")
        self.assertEqual(flag.sprint, sprint_for(team.project))
        self.assertIsNotNone(flag.created_at)

    def test_team_reaches_its_flags_as_flags(self):
        team = make_team()
        flag = make_flag(team)

        self.assertEqual(list(team.flags.all()), [flag])

    def test_user_is_optional(self):
        flag = make_flag(make_team(), user=None)
        flag.refresh_from_db()

        self.assertIsNone(flag.user)

    def test_severity_is_warning_or_flag(self):
        self.assertEqual(
            sorted(flag_model().Severity.values), ["flag", "warning"]
        )

    def test_unknown_severity_is_rejected_by_the_database(self):
        team = make_team()

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_flag(team, severity="critical")

    def test_sprint_is_required(self):
        team = make_team()

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_flag(team, sprint=None)

    def test_deleting_a_sprint_deletes_its_flags(self):
        team = make_team()
        make_flag(team)

        sprint_for(team.project).delete()

        self.assertFalse(flag_model().objects.exists())

    def test_rule_must_be_a_slug(self):
        flag = make_flag(make_team(), save=False, rule="Low share!")

        with self.assertRaises(ValidationError) as caught:
            flag.full_clean()

        self.assertIn("rule", caught.exception.message_dict)

    def test_deleting_a_team_deletes_its_flags(self):
        team = make_team()
        make_flag(team)

        team.delete()

        self.assertFalse(flag_model().objects.exists())


class ComputeFlagsTests(TestCase):
    def test_signature_matches_the_shared_contract(self):
        from dashboard.flags import compute_flags as contract

        self.assertEqual(list(inspect.signature(contract).parameters), ["team"])

    def test_returns_no_flags_for_a_team_with_no_activity(self):
        team = make_team()

        self.assertEqual(compute_flags(team), [])
        self.assertFalse(team.flags.exists())

    def test_saves_and_returns_the_flags_rules_raise(self):
        team, user = make_flaggable_team(), make_user()

        with patch("dashboard.flags.RULES", [rule_raising({"user": user})]):
            flags = compute_flags(team)

        self.assertEqual(len(flags), 1)
        self.assertIsNotNone(flags[0].pk)
        self.assertEqual(list(team.flags.all()), flags)

    def test_rules_are_run_for_the_given_team_and_each_flaggable_sprint(self):
        team = make_flaggable_team()
        seen = []

        def rule(team, sprint):
            seen.append((team, sprint))
            return []

        with patch("dashboard.flags.RULES", [rule]):
            compute_flags(team)

        self.assertEqual(seen, [(team, sprint_for(team.project))])

    def test_rules_dont_run_without_a_flaggable_sprint(self):
        team = make_team()
        sprint_for(team.project)
        seen = []

        with patch("dashboard.flags.RULES", [lambda team, sprint: seen.append(sprint) or []]):
            compute_flags(team)

        self.assertEqual(seen, [])

    def test_recomputing_replaces_earlier_flags(self):
        team = make_flaggable_team()

        with patch("dashboard.flags.RULES", [rule_raising({})]):
            compute_flags(team)
            flags = compute_flags(team)

        self.assertEqual(list(team.flags.all()), flags)
        self.assertEqual(team.flags.count(), 1)

    def test_recomputing_clears_flags_no_rule_raises_any_more(self):
        team = make_team()
        make_flag(team)

        compute_flags(team)

        self.assertFalse(team.flags.exists())

    def test_leaves_other_teams_flags_alone(self):
        team, other_team = make_team(), make_team("Sample Team B")
        other_flag = make_flag(other_team)

        compute_flags(team)

        self.assertEqual(list(other_team.flags.all()), [other_flag])

    def test_a_failing_rule_keeps_the_earlier_flags(self):
        team = make_flaggable_team()
        earlier = make_flag(team)

        def broken_rule(team, sprint):
            raise RuntimeError("rule failed")

        with patch("dashboard.flags.RULES", [broken_rule]), self.assertRaises(RuntimeError):
            compute_flags(team)

        self.assertEqual(list(team.flags.all()), [earlier])


class FlagAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser(
            username="sample-admin",
            password="not-a-real-password",
            email="admin@example.com",
            github_login="sample-admin",
        )

    def flag_admin(self):
        return admin.site._registry[flag_model()]

    def admin_request(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser
        return request

    def test_flag_is_registered_in_admin(self):
        self.assertIn(flag_model(), admin.site._registry)

    def test_admin_cannot_add_or_change_flags(self):
        flag_admin, request = self.flag_admin(), self.admin_request()

        self.assertFalse(flag_admin.has_add_permission(request))
        self.assertFalse(flag_admin.has_change_permission(request))

    def test_admin_can_still_delete_so_team_deletes_cascade(self):
        # Django checks each related model's admin before cascading a delete, so
        # refusing delete here would stop a team or user being deleted in admin.
        self.assertTrue(self.flag_admin().has_delete_permission(self.admin_request()))

    def test_flag_list_page_loads(self):
        make_flag(make_team())
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:dashboard_flag_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sample-rule")


class MigrationTests(TestCase):
    def test_dashboard_has_migrations(self):
        self.assertIn("dashboard", MigrationLoader(connection).migrated_apps)

    def test_migrations_create_flag_and_match_the_models(self):
        migrated = MigrationLoader(connection).project_state().apps
        self.assertTrue(migrated.get_model("dashboard", "Flag"))
        call_command("makemigrations", "dashboard", check=True, dry_run=True, stdout=StringIO())
