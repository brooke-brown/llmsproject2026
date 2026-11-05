"""Tests for `manage.py demo_complete_week`, which shows a flag for an unfinished week."""

from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.flags import compute_flags
from dashboard.services import current_week_start
from summaries.models import Claim, Summary, ValidationRun


def run_command(*args):
    out = StringIO()
    call_command("demo_complete_week", *args, stdout=out)
    return out.getvalue()


class DemoCompleteWeekTests(TestCase):
    def setUp(self):
        self.week = current_week_start()
        course = Course.objects.create(name="CS 3240, Fall 2026")
        self.project = Project.objects.create(course=course, name="Project 1")
        self.team = Team.objects.create(project=self.project, name="Team Demo")
        self.worker = self.add_student(self.team, "sample-worker")
        self.idler = self.add_student(self.team, "sample-idler")
        self.worker_summary = self.add_summary(self.team, self.worker, self.week)
        self.idler_summary = self.add_summary(self.team, self.idler, self.week)
        Claim.objects.create(
            summary=self.worker_summary,
            text="Implemented a required feature.",
            category="feature",
            requirement_ids=[1],
            significance="major",
            status=Claim.Status.VERIFIED,
        )
        Claim.objects.create(
            summary=self.idler_summary,
            text="Added project documentation.",
            category="feature",
            requirement_ids=[],
            significance="minor",
            status=Claim.Status.VERIFIED,
        )

    def add_student(self, team, login):
        user = get_user_model().objects.create_user(
            username=login, password="not-a-real-password", github_login=login
        )
        TeamMembership.objects.create(team=team, user=user)
        return user

    def add_summary(self, team, user, week):
        summary = Summary.objects.create(team=team, user=user, week_start=week)
        ValidationRun.objects.create(
            summary=summary, claims_total=1, claims_first_pass=1, claims_dropped=0, retries=0
        )
        return summary

    def test_the_unfinished_week_raises_no_flag_before_the_command(self):
        self.assertEqual(compute_flags(self.team), [])

    def test_flags_the_student_with_no_requirement_tied_work(self):
        output = run_command("--team", "Team Demo")

        flag = self.team.flags.get()
        self.assertEqual(flag.user, self.idler)
        self.assertEqual(flag.severity, "flag")
        self.assertEqual(flag.week_start, self.week)
        self.assertIn(flag.reason, output)

    def test_the_week_no_longer_counts_as_partial(self):
        run_command("--team", "Team Demo")

        self.assertFalse(Summary.objects.get(pk=self.worker_summary.pk).is_partial)
        self.assertFalse(Summary.objects.get(pk=self.idler_summary.pk).is_partial)

    def test_generating_again_keeps_the_week_counting_toward_flags(self):
        run_command("--team", "Team Demo")

        # Generating logs a new run, but a summary's latest run is the one that counts.
        ValidationRun.objects.create(
            summary=self.idler_summary,
            claims_total=1,
            claims_first_pass=1,
            claims_dropped=0,
            retries=0,
        )

        self.assertEqual([flag.user for flag in compute_flags(self.team)], [self.idler])

    def test_team_page_shows_the_flag(self):
        run_command("--team", "Team Demo")
        instructor = get_user_model().objects.create_user(
            username="sample-instructor",
            password="not-a-real-password",
            github_login="sample-instructor",
        )
        Enrollment.objects.create(
            user=instructor, course=self.project.course, role=Enrollment.Role.INSTRUCTOR
        )
        self.client.force_login(instructor)

        response = self.client.get(reverse("dashboard:team", args=[self.team.id]))

        self.assertContains(response, escape(self.team.flags.get().reason))

    def test_leaves_other_teams_and_weeks_alone(self):
        other_team = Team.objects.create(project=self.project, name="Team Other")
        other_summary = self.add_summary(other_team, self.worker, self.week)
        earlier_summary = self.add_summary(self.team, self.worker, date(2026, 1, 5))
        other_time = other_summary.validation_runs.get().created_at
        earlier_time = earlier_summary.validation_runs.get().created_at

        run_command("--team", "Team Demo")

        self.assertEqual(other_summary.validation_runs.get().created_at, other_time)
        self.assertEqual(earlier_summary.validation_runs.get().created_at, earlier_time)

    def test_week_option_picks_another_week(self):
        week = date(2026, 1, 5)
        summary = self.add_summary(self.team, self.worker, week)
        untouched = self.worker_summary.validation_runs.get().created_at

        run_command("--team", "Team Demo", "--week", week.isoformat())

        run_time = summary.validation_runs.get().created_at
        self.assertEqual(run_time, summary.week_ended_at)
        self.assertEqual(self.worker_summary.validation_runs.get().created_at, untouched)

    def test_says_so_when_no_flag_comes_out(self):
        self.idler_summary.claims.update(requirement_ids=[1], significance="major")

        output = run_command("--team", "Team Demo")

        self.assertEqual(self.team.flags.count(), 0)
        self.assertIn("No flags", output)

    def test_refuses_an_unknown_team(self):
        with self.assertRaisesMessage(CommandError, "No team"):
            run_command("--team", "Team Missing")

    def test_refuses_a_team_name_used_twice(self):
        Team.objects.create(project=self.project, name="Team Demo")

        with self.assertRaisesMessage(CommandError, "More than one team"):
            run_command("--team", "Team Demo")

    def test_refuses_a_week_with_no_summaries(self):
        with self.assertRaisesMessage(CommandError, "no summaries"):
            run_command("--team", "Team Demo", "--week", "2026-01-05")
