"""Tests for T-21 (#125): the dashboard generates by sprint only."""

from django.urls import reverse

from dashboard import services

from .test_generate_summary import generate_url, page_messages, retry_url
from .test_sprint_generation import SprintGenerationFixture


class WeeklyGenerationIsGoneTests(SprintGenerationFixture):
    def test_a_post_that_names_only_a_week_is_refused_and_nothing_runs(self):
        response = self.client.post(
            generate_url(self.team.id), {"week_start": "2026-10-26"}, follow=True
        )
        self.assertIn("Pick a sprint to generate.", page_messages(response))
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_a_post_with_nothing_is_refused_the_same_way(self):
        response = self.client.post(generate_url(self.team.id), {}, follow=True)
        self.assertIn("Pick a sprint to generate.", page_messages(response))

    def test_the_weekly_helpers_are_removed(self):
        for name in (
            "MONDAY",
            "week_end",
            "current_week_start",
            "last_completed_week_start",
            "parse_week_start",
            "generate_team_summaries",
            "_ingest_team_week",
            "_write_summaries",
        ):
            self.assertFalse(hasattr(services, name), name)
        self.assertTrue(callable(services.generate_team_sprint_summaries))

    def remember_weekly_retry(self):
        session = self.client.session
        session[services.RETRY_SESSION_KEY] = {
            str(self.team.id): {"week_start": "2026-10-26", "student_ids": [self.grace.id]}
        }
        session.save()

    def test_a_retry_remembered_from_a_weekly_run_has_nothing_to_retry(self):
        self.remember_weekly_retry()
        response = self.client.post(retry_url(self.team.id), follow=True)
        self.assertIn("has no failed summaries to retry", page_messages(response))
        self.generate.assert_not_called()

    def test_retrying_it_clears_it_from_the_session(self):
        self.remember_weekly_retry()
        self.client.post(retry_url(self.team.id))
        self.assertNotIn(
            str(self.team.id), self.client.session.get(services.RETRY_SESSION_KEY, {})
        )

    def test_the_overview_never_offers_to_retry_it(self):
        # It can't run, so no Retry button, and no "Incomplete" tag hiding the sprint's status.
        self.remember_weekly_retry()
        page = self.client.get(
            reverse("dashboard:course_overview", args=[self.course.id])
        ).content.decode()
        self.assertNotIn(reverse("dashboard:retry_failed_summaries", args=[self.team.id]), page)
        self.assertNotIn("Incomplete", page)
