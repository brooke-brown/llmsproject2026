"""Tests for US-28: see that summaries are generating after I leave and come back.

`ingest` and `generate_summary` are mocked, so no test makes a network or LLM call. The
clock is fixed at Monday Nov 2, 2026, noon in New York.

The model and URLs are reached per test rather than at module level, so each criterion
passes or fails on its own.
"""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import IntegrityError, OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership
from dashboard import models as dashboard_models
from ingest import services as ingest_services

from .test_course_overview import enroll, make_user, overview_url
from .test_generate_summary import (
    LAST_WEEK,
    NOW,
    TeamGenerationFixture,
    generate_url,
    page_messages,
    retry_url,
    team_article,
)

Role = Enrollment.Role


def status_url(course_id):
    return reverse("dashboard:course_generating_status", args=[course_id])


def runs():
    return dashboard_models.GenerationRun.objects


class GenerationRunFixture(TeamGenerationFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.instructor)

    def click_generate(self, team=None, client=None):
        return (client or self.client).post(
            generate_url((team or self.team).id),
            {"week_start": LAST_WEEK.isoformat()},
            follow=True,
        )

    def already_running(self, team=None, minutes_left=10):
        return runs().create(
            team=team or self.team, expires_at=NOW + timedelta(minutes=minutes_left)
        )

    def give_a_failed_student(self):
        """Run once with a failure, so the session has someone to retry."""
        self.fail_for(self.ada)
        self.click_generate()
        self.generate.side_effect = self.save_summary
        self.generate.reset_mock()
        self.ingest.reset_mock()


class OneRunAtATimeTests(GenerationRunFixture):
    def test_a_run_is_recorded_while_it_happens(self):
        seen = []
        self.ingest.side_effect = lambda *args: seen.append(runs().filter(team=self.team).count()) or []

        self.click_generate()

        self.assertIn(1, seen)

    def test_a_second_generate_is_refused_and_nothing_runs(self):
        self.already_running()

        response = self.click_generate()

        self.assertIn("already being generated", page_messages(response))
        self.assertIn("Team Alpha", page_messages(response))
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_a_retry_is_refused_too(self):
        self.give_a_failed_student()
        self.already_running()

        response = self.client.post(retry_url(self.team.id), follow=True)

        self.assertIn("already being generated", page_messages(response))
        self.generate.assert_not_called()

    def test_a_refusal_doesnt_queue_for_the_database(self):
        """Waiting for a run's lock would let the second run start once the first has ended."""
        self.already_running()

        with mock.patch.object(runs(), "create") as create:
            self.click_generate()

        create.assert_not_called()

    def test_a_refusal_doesnt_write_to_the_session(self):
        """A write would wait for a run's lock, and could overwrite the run's saved result."""
        self.already_running()
        before = Session.objects.get().session_data

        with mock.patch.object(Session, "save") as save:
            self.client.post(generate_url(self.team.id), {"week_start": LAST_WEEK.isoformat()})

        save.assert_not_called()
        self.assertEqual(Session.objects.get().session_data, before)

    def test_a_refused_click_leaves_the_running_record_alone(self):
        run = self.already_running()

        self.click_generate()

        self.assertTrue(runs().filter(pk=run.pk).exists())

    def test_two_starting_at_the_same_moment_refuses_one(self):
        """Both can pass any check, and the database's constraint stops the second."""
        with mock.patch.object(runs(), "create", side_effect=IntegrityError):
            response = self.click_generate()

        self.assertIn("already being generated", page_messages(response))
        self.ingest.assert_not_called()

    def test_another_team_can_generate_at_the_same_time(self):
        self.already_running(self.empty_team)

        self.click_generate()

        self.ingest.assert_called()

    def test_a_busy_database_at_the_start_shows_a_message(self):
        with mock.patch.object(runs(), "create", side_effect=OperationalError("database is locked")):
            response = self.click_generate()

        self.assertIn("try again", page_messages(response))
        self.ingest.assert_not_called()


class WaitingForTheDatabaseTests(TestCase):
    def test_writes_wait_for_a_runs_transaction_instead_of_failing(self):
        """Showing a message writes to the session, which a run's GitHub pull would block.

        Two threads can't share a test's transaction, so this checks the settings. The
        behaviour itself was checked by hand against a running server.
        """
        options = settings.DATABASES["default"]["OPTIONS"]

        self.assertGreaterEqual(options["timeout"], 60)
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")


class RunEndsTests(GenerationRunFixture):
    def assert_cleared(self):
        self.assertFalse(runs().filter(team=self.team).exists())

    def test_a_finished_run_clears(self):
        self.click_generate()

        self.ingest.assert_called()
        self.assert_cleared()

    def test_the_team_can_generate_again_afterwards(self):
        self.click_generate()
        self.ingest.reset_mock()

        self.click_generate()

        self.ingest.assert_called()

    def test_a_failed_pull_clears(self):
        self.ingest.side_effect = ingest_services.IngestError("GitHub returned an error.")

        self.click_generate()

        self.assert_cleared()

    def test_a_failed_summary_clears(self):
        self.fail_for(self.ada)

        self.click_generate()

        self.assert_cleared()

    def test_an_unexpected_error_clears(self):
        with mock.patch("dashboard.services.flags.compute_flags", side_effect=ValueError):
            self.client.raise_request_exception = False
            self.click_generate()

        self.assert_cleared()

    def test_a_finished_retry_clears(self):
        self.give_a_failed_student()

        self.client.post(retry_url(self.team.id))

        self.generate.assert_called()
        self.assert_cleared()

    def test_a_busy_database_at_the_end_doesnt_lose_the_result(self):
        """The record then clears by expiring, so this mustn't turn a good run into an error."""
        with mock.patch.object(
            dashboard_models.GenerationRun, "delete", side_effect=OperationalError("database is locked")
        ):
            response = self.click_generate()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Generated summaries", page_messages(response))


class StuckRunTests(GenerationRunFixture):
    def test_an_expired_run_doesnt_block_the_team(self):
        self.already_running(minutes_left=-1)

        self.click_generate()

        self.ingest.assert_called()
        self.assertFalse(runs().filter(team=self.team).exists())

    def test_the_limit_is_ten_minutes_plus_six_per_student(self):
        limits = []
        self.ingest.side_effect = lambda *args: limits.append(
            runs().get(team=self.team).expires_at - NOW
        ) or []

        self.click_generate()

        self.assertEqual(set(limits), {timedelta(minutes=22)})

    def test_the_refusal_says_when_a_stuck_run_clears(self):
        run = self.already_running(minutes_left=45)
        self.assertEqual(run.expires_at, NOW + timedelta(minutes=45))

        response = self.click_generate()

        self.assertIn("12:45", page_messages(response))

    def test_a_stuck_run_can_be_cleared_in_the_admin(self):
        admin = get_user_model().objects.create_superuser(
            username="sample-admin", password="not-a-real-password", github_login="sample-admin"
        )
        run = self.already_running()
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin:dashboard_generationrun_delete", args=[run.pk]), {"post": "yes"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(runs().filter(pk=run.pk).exists())


class ComingBackTests(GenerationRunFixture):
    def card(self, team=None):
        content = self.client.get(overview_url(self.course.id)).content.decode()
        return team_article(content, (team or self.team).name)

    def test_a_generating_team_shows_a_disabled_spinner_on_a_fresh_page(self):
        self.already_running()

        card = self.card()

        self.assertIn('aria-busy="true"', card)
        self.assertIn("disabled", card)
        self.assertIn("Generating…", card)

    def test_another_team_stays_normal(self):
        self.already_running()

        card = self.card(self.empty_team)

        self.assertNotIn("aria-busy", card)
        self.assertIn("Generate summaries", card)

    def test_an_idle_team_stays_normal(self):
        self.assertNotIn("aria-busy", self.card())

    def test_an_expired_run_shows_as_idle(self):
        self.already_running(minutes_left=-1)

        self.assertNotIn("aria-busy", self.card())

    def test_the_retry_button_is_disabled_too(self):
        self.give_a_failed_student()
        self.already_running()

        card = self.card()

        tag = card[card.index("Retry failed summaries") - 80 : card.index("Retry failed summaries")]
        self.assertIn("disabled", tag)

    def test_a_ta_sees_it_too(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.already_running()
        self.client.force_login(ta)

        self.assertIn('aria-busy="true"', self.card())

    def test_the_page_watches_for_the_run_to_finish(self):
        self.already_running()

        content = self.client.get(overview_url(self.course.id)).content.decode()

        self.assertIn(f'data-generating-status="{status_url(self.course.id)}"', content)
        self.assertIn(f'data-team-ids="{self.team.id}"', content)

    def test_a_page_with_nothing_generating_doesnt_watch(self):
        content = self.client.get(overview_url(self.course.id)).content.decode()

        self.assertNotIn("data-generating-status=", content)

    def test_the_result_message_survives_leaving_before_it_finished(self):
        """A browser that has left never receives the response or any cookie set on it."""
        session_cookie = self.client.cookies["sessionid"].value
        self.client.post(generate_url(self.team.id), {"week_start": LAST_WEEK.isoformat()})

        came_back = Client()
        came_back.cookies["sessionid"] = session_cookie
        response = came_back.get(overview_url(self.course.id))

        self.assertIn("Generated summaries", page_messages(response))

    def test_two_runs_at_once_both_report_their_results(self):
        """The run that ends last has an old copy of the session, which mustn't win."""
        repo_owner = make_user("sample-lin", "Lin", "Beta")
        enroll(repo_owner, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=repo_owner, team=self.empty_team)
        Repo.objects.create(
            team=self.empty_team, url="https://github.com/acme/beta", full_name="acme/beta"
        )
        started = []

        def the_other_team_runs_meanwhile(repo, *args):
            if not started:
                started.append(repo)
                self.client.post(
                    generate_url(self.empty_team.id), {"week_start": LAST_WEEK.isoformat()}
                )
            return []

        self.ingest.side_effect = the_other_team_runs_meanwhile

        self.client.post(generate_url(self.team.id), {"week_start": LAST_WEEK.isoformat()})
        response = self.client.get(overview_url(self.course.id))

        self.assertIn("Team Alpha", page_messages(response))
        self.assertIn("Team Beta", page_messages(response))

    def test_a_failed_run_reports_as_an_error_after_coming_back(self):
        self.fail_for(self.ada)

        self.client.post(generate_url(self.team.id), {"week_start": LAST_WEEK.isoformat()})
        response = self.client.get(overview_url(self.course.id))

        levels = [message.level_tag for message in response.context["messages"]]
        self.assertEqual(levels, ["error"])

    def test_the_retry_button_appears_after_a_failed_run(self):
        self.fail_for(self.ada)

        self.click_generate()

        self.assertContains(
            self.client.get(overview_url(self.course.id)), retry_url(self.team.id)
        )

    def test_the_message_only_shows_once(self):
        self.click_generate()

        response = self.client.get(overview_url(self.course.id))

        self.assertEqual(page_messages(response), "")


class StatusViewTests(GenerationRunFixture):
    def team_ids(self):
        response = self.client.get(status_url(self.course.id))
        self.assertEqual(response.status_code, 200)
        return response.json()["team_ids"]

    def test_it_lists_the_generating_teams(self):
        self.already_running()

        self.assertEqual(self.team_ids(), [self.team.id])

    def test_it_is_empty_when_nothing_is_generating(self):
        self.assertEqual(self.team_ids(), [])

    def test_it_leaves_out_expired_runs(self):
        self.already_running(minutes_left=-1)

        self.assertEqual(self.team_ids(), [])

    def test_it_leaves_out_other_courses(self):
        other = Course.objects.create(name="CS 3240, Fall 2026")
        project = Project.objects.create(course=other, name="Other Project")
        self.already_running(Team.objects.create(project=project, name="Other Team"))

        self.assertEqual(self.team_ids(), [])

    def test_a_ta_can_see_it(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.client.force_login(ta)

        self.assertEqual(self.team_ids(), [])

    def test_others_get_a_403(self):
        student = make_user("sample-student")
        enroll(student, self.course, Role.STUDENT)
        for user in [student, make_user("sample-unenrolled")]:
            with self.subTest(user=user.username):
                self.client.force_login(user)

                self.assertEqual(self.client.get(status_url(self.course.id)).status_code, 403)

    def test_a_missing_course_is_a_404(self):
        self.assertEqual(self.client.get(status_url(999999)).status_code, 404)

    def test_anonymous_users_are_sent_to_log_in(self):
        self.client.logout()

        response = self.client.get(status_url(self.course.id))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_it_refuses_post(self):
        self.assertEqual(self.client.post(status_url(self.course.id)).status_code, 405)
