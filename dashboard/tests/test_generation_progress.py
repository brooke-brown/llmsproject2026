"""Tests for US-45 (#135): see how far summary generation has got.

A run's progress is read from its GenerationRun row, which the run updates as each student
finishes. `ingest` and `generate_summary` are mocked, as in the other generation tests.
"""

import json
from datetime import timedelta

from django.db.migrations.loader import MigrationLoader
from django.test import Client, SimpleTestCase
from django.urls import reverse

from dashboard.models import GenerationRun
from core.models import Enrollment

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_generate_summary import LAST_WEEK, NOW, generate_url, retry_url, team_article
from .test_generation_run import GenerationRunFixture, status_url


def run_for(team, done=1, total=4, failures=None, minutes_left=10):
    return GenerationRun.objects.create(
        team=team,
        expires_at=NOW + timedelta(minutes=minutes_left),
        student_count=total,
        done_count=done,
        failures=failures or [],
    )


class RecordingProgressTests(GenerationRunFixture):
    def snapshots(self):
        """What the run row said when each student's summary was about to be written."""
        seen = []

        def generate(user, team, week_start):
            run = GenerationRun.objects.get(team=team)
            seen.append((run.done_count, run.student_count, [f["name"] for f in run.failures]))
            if user == self.grace:
                from summaries.llm import LLMError

                raise LLMError("The LLM request timed out.")
            return self.save_summary(user, team, week_start)

        self.generate.side_effect = generate
        return seen

    def test_the_run_starts_with_the_number_of_students_and_counts_each_one_done(self):
        seen = []

        def generate(user, team, week_start):
            run = GenerationRun.objects.get(team=team)
            seen.append((run.done_count, run.student_count))
            return self.save_summary(user, team, week_start)

        self.generate.side_effect = generate
        self.click_generate()
        self.assertEqual(seen, [(0, 2), (1, 2)])

    def test_a_failed_student_counts_as_done_and_is_recorded_by_name_and_reason(self):
        seen = self.snapshots()
        self.click_generate()
        # Grace (first, by last name) fails; by Ada's turn she is done and named.
        self.assertEqual(seen[0], (0, 2, []))
        self.assertEqual(seen[1][:2], (1, 2))
        self.assertEqual(seen[1][2], ["Grace Example"])

    def test_the_other_students_are_unaffected_by_a_failure(self):
        self.snapshots()
        self.click_generate()
        self.assertEqual(self.generate.call_count, 2)

    def test_the_run_row_is_gone_when_the_run_ends(self):
        self.click_generate()
        self.assertFalse(GenerationRun.objects.filter(team=self.team).exists())

    def test_a_retry_tracks_its_own_students(self):
        self.give_a_failed_student()
        seen = []

        def generate(user, team, week_start):
            run = GenerationRun.objects.get(team=team)
            seen.append((run.done_count, run.student_count))
            return self.save_summary(user, team, week_start)

        self.generate.side_effect = generate
        self.client.post(retry_url(self.team.id))
        self.assertEqual(seen, [(0, 1)])


class OverviewProgressTests(GenerationRunFixture):
    def card(self, team="Team Alpha"):
        return team_article(self.client.get(overview_url(self.course.id)).content.decode(), team)

    def test_a_generating_team_shows_how_many_students_are_done_with_a_progress_bar(self):
        run_for(self.team, done=1, total=4)
        card = self.card()
        self.assertIn("1 of 4 students done", card)
        self.assertIn('<progress value="1" max="4"', card)

    def test_other_teams_show_no_progress(self):
        run_for(self.team)
        self.assertNotIn("<progress", self.card("Team Beta"))

    def test_the_button_stays_disabled_while_running(self):
        run_for(self.team)
        card = self.card()
        self.assertIn("Generating…", card)
        self.assertIn("disabled", card)

    def test_a_failed_student_appears_by_name_with_a_plain_reason(self):
        run_for(self.team, failures=[{"name": "Grace Example", "reason": "The LLM request timed out."}])
        card = self.card()
        self.assertIn("Grace Example", card)
        self.assertIn("The LLM request timed out.", card)

    def test_a_run_with_no_student_done_yet_shows_an_unfilled_bar_and_says_it_is_starting(self):
        # _start_run records the student count straight away, so "Starting" must not depend on it.
        run_for(self.team, done=0, total=4)
        card = self.card()
        self.assertIn("Starting", card)
        self.assertIn("<progress>", card.replace(' aria-label="Progress"', ""))
        self.assertNotIn("0 of 4 students done", card)

    def test_starting_goes_once_a_student_is_done(self):
        run_for(self.team, done=1, total=4)
        self.assertNotIn("Starting", self.card())

    def test_starting_shows_during_a_real_run_while_github_activity_is_pulled(self):
        seen = []
        self.ingest.side_effect = lambda *args: seen.append(self.card()) or []
        self.click_generate()
        self.assertTrue(seen)
        self.assertIn("Starting", seen[0])

    def test_an_expired_run_shows_nothing(self):
        run_for(self.team, minutes_left=-5)
        self.assertNotIn("<progress", self.card())

    def test_when_the_run_ends_progress_goes_and_the_button_returns(self):
        run = run_for(self.team)
        run.delete()
        card = self.card()
        self.assertNotIn("<progress", card)
        self.assertIn("Generate summaries", card)
        self.assertNotIn('aria-busy="true"', card)


class RefreshTests(GenerationRunFixture):
    REFRESH = '<meta http-equiv="refresh" content="5">'

    def status(self):
        return json.loads(self.client.get(status_url(self.course.id)).content)

    def test_the_overview_uses_only_its_poller_and_never_a_meta_refresh(self):
        run_for(self.team)
        html = self.client.get(overview_url(self.course.id)).content.decode()
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn('data-generating-status="', html)

    def test_the_overview_has_no_poller_when_no_run_is_active(self):
        html = self.client.get(overview_url(self.course.id)).content.decode()
        self.assertNotIn('data-generating-status="', html)
        run_for(self.team, minutes_left=-1)
        html = self.client.get(overview_url(self.course.id)).content.decode()
        self.assertNotIn('data-generating-status="', html)

    def test_a_running_card_has_a_slot_the_poller_fills_with_its_progress(self):
        run_for(self.team)
        card = team_article(self.client.get(overview_url(self.course.id)).content.decode(), "Team Alpha")
        self.assertIn(f'data-progress-for="{self.team.id}"', card)

    def test_the_status_gives_each_running_teams_progress(self):
        run_for(self.team, done=2, total=3, failures=[{"name": "Grace Example", "reason": "Timed out."}])
        status = self.status()
        self.assertEqual(status["team_ids"], [self.team.id])
        progress = status["progress"][str(self.team.id)]
        self.assertIn("2 of 3 students done", progress)
        self.assertIn('<progress value="2" max="3"', progress)
        self.assertIn("Grace Example", progress)

    def test_the_status_gives_no_progress_when_nothing_runs(self):
        run_for(self.team, minutes_left=-1)
        self.assertEqual(self.status(), {"team_ids": [], "progress": {}})

    def test_the_team_page_shows_progress_and_refreshes_while_its_team_runs(self):
        run_for(self.team, done=2, total=3)
        html = self.client.get(team_url(self.team.id)).content.decode()
        self.assertIn("2 of 3 students done", html)
        self.assertIn('<progress value="2" max="3"', html)
        self.assertIn(self.REFRESH, html)

    def test_the_team_page_is_quiet_when_another_team_is_running(self):
        run_for(self.team)
        html = self.client.get(team_url(self.empty_team.id)).content.decode()
        self.assertNotIn("<progress", html)
        self.assertNotIn('http-equiv="refresh"', html)


class AccessTests(GenerationRunFixture):
    def test_tas_can_see_progress_and_students_cannot(self):
        run_for(self.team)
        ta = make_user("sample-ta")
        enroll(ta, self.course, Enrollment.Role.TA)
        client = Client()
        client.force_login(ta)
        self.assertIn("1 of 4 students done", client.get(overview_url(self.course.id)).content.decode())
        student = Client()
        student.force_login(self.ada)
        self.assertEqual(student.get(overview_url(self.course.id)).status_code, 403)
        self.assertEqual(student.get(team_url(self.team.id)).status_code, 403)


class SprintRunProgressTests(GenerationRunFixture):
    def test_a_sprint_run_counts_each_student_done_and_names_a_failure(self):
        from unittest import mock

        from summaries.llm import LLMError
        from summaries.models import Summary

        seen = []

        def generate(user, team, sprint):
            run = GenerationRun.objects.get(team=team)
            seen.append((run.done_count, run.student_count, [f["name"] for f in run.failures]))
            if user == self.grace:
                raise LLMError("The LLM request timed out.")
            return Summary.objects.get_or_create(team=team, user=user, sprint=sprint)[0]

        with mock.patch("summaries.services.generate_sprint_summary", side_effect=generate):
            self.client.post(generate_url(self.team.id), {"sprint": str(self.sprint.id)})
        # Grace goes first (by last name) and fails; by Ada's turn she is done and named.
        self.assertEqual(seen, [(0, 2, []), (1, 2, ["Grace Example"])])
        self.assertFalse(GenerationRun.objects.filter(team=self.team).exists())


class MigrationOrderTests(SimpleTestCase):
    def test_the_progress_migration_follows_flags_per_sprint(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        self.assertEqual(loader.detect_conflicts(), {})
        migration = loader.disk_migrations[("dashboard", "0005_generationrun_progress")]
        self.assertEqual(migration.dependencies, [("dashboard", "0004_flags_per_sprint")])
