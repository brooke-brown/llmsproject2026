"""Tests for T-15 (#103): the FlagDispute model.

The model and dispute_for are looked up per test rather than imported at module level, so
each criterion passes or fails on its own instead of the whole module failing on a single
ImportError.
"""

import inspect
from datetime import date
from io import StringIO

from django.apps import apps
from django.contrib import admin
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag
from summaries.models import Summary

from .sprint_helpers import sprint_for
from .test_course_overview import enroll, make_user, team_url
from .test_team_setup import (
    TeamSetupFixture,
    membership_remove_url,
    team_delete_url,
)

WEEK = date(2026, 10, 26)  # A Monday.
RULE = "low-requirement-share"
Role = Enrollment.Role


def dispute_model():
    return apps.get_model("dashboard", "FlagDispute")


def dispute_for(flag):
    from dashboard.services import dispute_for

    return dispute_for(flag)


def make_dispute(team, student, **fields):
    values = {
        "rule": RULE,
        "sprint": sprint_for(team.project),
        "flag_reason": "Ada Sample did 10% of the team's requirement-tied work.",
        "severity": "flag",
        "explanation": "I ran our user interviews that week, which aren't in the repo.",
        **fields,
    }
    return dispute_model().objects.create(team=team, student=student, **values)


def make_flag(team, user, **fields):
    values = {
        "rule": RULE,
        "reason": "Ada Sample did 10% of the team's requirement-tied work.",
        "severity": Flag.Severity.FLAG,
        "sprint": sprint_for(team.project),
        **fields,
    }
    return Flag.objects.create(team=team, user=user, **values)


class DisputeFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.instructor = make_user("sample-instructor")
        cls.next_sprint = sprint_for(
            cls.project, "Sprint 2", start=date(2026, 11, 2), end=date(2026, 11, 8)
        )


class FlagDisputeModelTests(DisputeFixture):
    def test_a_dispute_stores_its_fields(self):
        dispute = make_dispute(self.team, self.ada, severity="warning")
        dispute.refresh_from_db()

        self.assertEqual(dispute.team, self.team)
        self.assertEqual(dispute.student, self.ada)
        self.assertEqual(dispute.rule, RULE)
        self.assertEqual(dispute.sprint, sprint_for(self.project))
        self.assertEqual(dispute.severity, "warning")
        self.assertIn("user interviews", dispute.explanation)
        self.assertIn("10%", dispute.flag_reason)
        self.assertIsNotNone(dispute.created_at)
        self.assertIsNotNone(dispute.updated_at)

    def test_a_new_dispute_is_open_and_unresolved(self):
        dispute = make_dispute(self.team, self.ada)

        self.assertEqual(dispute.status, "open")
        self.assertEqual(dispute.resolution_note, "")
        self.assertIsNone(dispute.resolved_by)
        self.assertIsNone(dispute.resolved_at)

    def test_status_choices_are_the_three_outcomes(self):
        choices = {value for value, _ in dispute_model().Status.choices}

        self.assertEqual(choices, {"open", "flag_kept", "flag_withdrawn"})

    def test_a_resolved_dispute_stores_who_resolved_it(self):
        dispute = make_dispute(self.team, self.ada)
        dispute.status = "flag_withdrawn"
        dispute.resolution_note = "The interview notes are in the team's drive."
        dispute.resolved_by = self.instructor
        dispute.save()
        dispute.refresh_from_db()

        self.assertEqual(dispute.status, "flag_withdrawn")
        self.assertEqual(dispute.resolved_by, self.instructor)

    def test_updated_at_changes_when_the_dispute_is_edited(self):
        dispute = make_dispute(self.team, self.ada)
        created = dispute.updated_at

        dispute.explanation = "Edited explanation."
        dispute.save()

        self.assertGreater(dispute.updated_at, created)

    def test_the_database_refuses_an_unknown_status(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_dispute(self.team, self.ada, status="ignored")

    def test_the_database_refuses_an_unknown_severity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_dispute(self.team, self.ada, severity="minor")

    def test_a_student_can_dispute_a_rule_and_sprint_once(self):
        make_dispute(self.team, self.ada)

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_dispute(self.team, self.ada, explanation="A second one.")

    def test_a_student_can_dispute_another_sprint_or_rule(self):
        make_dispute(self.team, self.ada)
        make_dispute(self.team, self.ada, sprint=self.next_sprint)
        make_dispute(self.team, self.ada, rule="another-rule")

        self.assertEqual(dispute_model().objects.count(), 3)

    def test_teammates_can_dispute_the_same_rule_and_sprint(self):
        teammate = make_user("sample-teammate")
        make_dispute(self.team, self.ada)
        make_dispute(self.team, teammate)

        self.assertEqual(dispute_model().objects.count(), 2)

    def test_a_dispute_has_no_foreign_key_to_flag(self):
        related = {
            field.related_model
            for field in dispute_model()._meta.get_fields()
            if field.is_relation
        }

        self.assertNotIn(Flag, related)

    def test_its_str_names_the_student_rule_and_sprint_but_not_the_explanation(self):
        text = str(make_dispute(self.team, self.ada))

        self.assertIn("sample-ada", text)
        self.assertIn(RULE, text)
        self.assertIn("Sprint 1", text)
        self.assertNotIn("user interviews", text)

    def test_deleting_the_team_deletes_its_disputes(self):
        dispute = make_dispute(self.team, self.ada)

        self.team.delete()

        self.assertFalse(dispute_model().objects.filter(pk=dispute.pk).exists())

    def test_deleting_the_student_deletes_their_disputes(self):
        dispute = make_dispute(self.team, self.ada)

        self.ada.delete()

        self.assertFalse(dispute_model().objects.filter(pk=dispute.pk).exists())

    def test_deleting_the_resolver_keeps_the_dispute(self):
        dispute = make_dispute(self.team, self.ada, resolved_by=self.instructor)

        self.instructor.delete()
        dispute.refresh_from_db()

        self.assertIsNone(dispute.resolved_by)


class DisputeForTests(DisputeFixture):
    def test_it_finds_the_flags_dispute(self):
        dispute = make_dispute(self.team, self.ada)

        self.assertEqual(dispute_for(make_flag(self.team, self.ada)), dispute)

    def test_it_returns_none_for_an_undisputed_flag(self):
        self.assertIsNone(dispute_for(make_flag(self.team, self.ada)))

    def test_it_matches_on_team_student_rule_and_sprint(self):
        teammate = make_user("sample-teammate")
        other_team = Team.objects.create(project=self.project, name="Team Beta")
        make_dispute(self.team, self.ada)

        for flag in [
            make_flag(self.team, teammate),
            make_flag(other_team, self.ada),
            make_flag(self.team, self.ada, rule="another-rule"),
            make_flag(self.team, self.ada, sprint=self.next_sprint),
        ]:
            with self.subTest(flag=str(flag)):
                self.assertIsNone(dispute_for(flag))

    def test_it_ignores_the_flags_wording_and_severity(self):
        dispute = make_dispute(self.team, self.ada, severity="flag")
        flag = make_flag(
            self.team, self.ada, severity=Flag.Severity.WARNING, reason="Reworded reason."
        )

        self.assertEqual(dispute_for(flag), dispute)

    def test_a_team_wide_flag_has_no_dispute(self):
        make_dispute(self.team, self.ada)

        self.assertIsNone(dispute_for(make_flag(self.team, None)))


class RecomputeTests(DisputeFixture):
    def test_recomputing_flags_keeps_the_dispute_and_its_snapshot(self):
        from dashboard.flags import compute_flags

        make_flag(self.team, self.ada)
        dispute = make_dispute(self.team, self.ada)

        compute_flags(self.team)  # No summaries, so no flags are raised.
        dispute.refresh_from_db()

        self.assertFalse(Flag.objects.filter(team=self.team).exists())
        self.assertEqual(dispute.status, "open")
        self.assertIn("10%", dispute.flag_reason)

    def test_compute_flags_keeps_its_signature(self):
        from dashboard.flags import compute_flags

        self.assertEqual(str(inspect.signature(compute_flags)), "(team: core.models.Team) -> list[dashboard.models.Flag]")


class AdminAndMigrationTests(TestCase):
    def test_flag_dispute_is_registered_in_admin(self):
        self.assertIn(dispute_model(), admin.site._registry)

    def test_there_are_no_missing_migrations(self):
        out = StringIO()

        call_command("makemigrations", "dashboard", "--check", "--dry-run", stdout=out)


class DeleteConfirmationTests(TeamSetupFixture):
    """Deleting a team, project or student warns about disputes that go with it."""

    def dispute(self, student=None):
        return make_dispute(self.team, student or self.grace)

    def test_removing_a_student_deletes_their_disputes_on_the_team(self):
        dispute = self.dispute()
        elsewhere = make_dispute(self.later_team, self.grace)

        self.client.post(membership_remove_url(self.membership.id))

        self.assertFalse(dispute_model().objects.filter(pk=dispute.pk).exists())
        self.assertTrue(dispute_model().objects.filter(pk=elsewhere.pk).exists())

    def test_removing_a_student_keeps_teammates_disputes(self):
        teammate = make_user("sample-teammate")
        enroll(teammate, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=teammate, team=self.team)
        dispute = self.dispute(teammate)
        self.dispute()

        self.client.post(membership_remove_url(self.membership.id))

        self.assertTrue(dispute_model().objects.filter(pk=dispute.pk).exists())

    def test_the_remove_student_confirmation_counts_their_disputes(self):
        self.dispute()

        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, membership_remove_url(self.membership.id))
        self.assertIn("1 dispute", tag)

    def test_the_remove_student_confirmation_omits_disputes_when_there_are_none(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, membership_remove_url(self.membership.id))
        self.assertNotIn("dispute", tag)

    def test_the_team_delete_confirmation_counts_its_disputes(self):
        Summary.objects.create(team=self.team, user=self.grace, week_start=WEEK, artifact_count=1)
        self.dispute()
        make_dispute(self.team, self.grace, rule="another-rule")

        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, team_delete_url(self.team.id))
        self.assertIn("1 summary", tag)
        self.assertIn("2 disputes", tag)

    def test_the_team_delete_confirmation_omits_disputes_when_there_are_none(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, team_delete_url(self.team.id))
        self.assertNotIn("dispute", tag)

    def test_the_project_delete_confirmation_counts_its_disputes(self):
        from django.urls import reverse

        self.dispute()
        url = reverse("dashboard:project_delete", args=[self.project.id])

        content = self.client.get(
            reverse("dashboard:project_setup", args=[self.project.id])
        ).content.decode()

        tag = self.form_tag(content, url)
        self.assertIn("1 dispute", tag)

    def test_the_project_delete_confirmation_omits_disputes_when_there_are_none(self):
        from django.urls import reverse

        url = reverse("dashboard:project_delete", args=[self.project.id])

        content = self.client.get(
            reverse("dashboard:project_setup", args=[self.project.id])
        ).content.decode()

        tag = self.form_tag(content, url)
        self.assertNotIn("dispute", tag)

    def test_the_team_delete_note_mentions_disputes(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        self.assertIn("disputes", content[content.index("Delete team") :])
