"""Tests for US-18 (#55): generate a student's weekly summary.

`ingest` and `generate_summary` are mocked, so no test makes a network or LLM call.
`ingest.services.ingest` isn't on main yet (#18), so it is patched with create=True.
The reporting clock is fixed at Monday Nov 2, 2026, noon in New York, which makes
the week of Oct 26 the most recent completed week.

URLs are reversed per test, and dashboard.services is reached through the module,
so each criterion passes or fails on its own.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership
from dashboard import services
from dashboard.models import Flag
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, team_url
from .test_show_flags import article_containing

Role = Enrollment.Role
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=NEW_YORK)
LAST_WEEK = date(2026, 10, 26)
LAST_WEEK_END = date(2026, 11, 1)


def generate_url(team_id, user_id):
    return reverse("dashboard:generate_summary", args=[team_id, user_id])


def summary_url(summary_id):
    return reverse("dashboard:summary", args=[summary_id])


class GenerateSummaryFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.app = Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/app", full_name="sample-org/app"
        )
        cls.api = Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/api", full_name="sample-org/api"
        )

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

        cls.other_team = Team.objects.create(project=cls.project, name="Team Beta")

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

        self.ingest = self.patch("ingest.services.ingest", create=True, return_value=[])
        self.generate = self.patch("summaries.services.generate_summary")
        self.generate.side_effect = self.save_summary

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def save_summary(user, team, week_start):
        summary, _ = Summary.objects.get_or_create(team=team, user=user, week_start=week_start)
        return summary

    def post(self, week_start=LAST_WEEK, user=None, student=None, team=None, **kwargs):
        self.client.force_login(user or self.instructor)
        data = {} if week_start is None else {"week_start": str(week_start)}
        url = generate_url((team or self.team).id, (student or self.ada).id)
        return self.client.post(url, data, **kwargs)


class DefaultWeekTests(TestCase):
    def week_start_at(self, *args):
        return services.last_completed_week_start(datetime(*args, tzinfo=NEW_YORK))

    def test_on_a_monday_is_the_week_that_ended_the_day_before(self):
        self.assertEqual(self.week_start_at(2026, 11, 2, 0, 0), LAST_WEEK)

    def test_midweek_is_the_previous_monday_to_sunday(self):
        self.assertEqual(self.week_start_at(2026, 11, 5, 15, 0), LAST_WEEK)

    def test_on_a_sunday_the_current_week_hasnt_ended(self):
        self.assertEqual(self.week_start_at(2026, 11, 1, 23, 59), date(2026, 10, 19))

    def test_uses_new_york_time_not_utc(self):
        # 2am UTC on Monday is still 9pm Sunday in New York.
        now = datetime(2026, 11, 2, 2, 0, tzinfo=dt_timezone.utc)

        self.assertEqual(services.last_completed_week_start(now), date(2026, 10, 19))


class GenerateButtonTests(GenerateSummaryFixture):
    def get_team_page(self):
        self.client.force_login(self.instructor)
        return self.client.get(team_url(self.team.id))

    def test_each_student_has_a_generate_form_that_posts_to_their_url(self):
        content = self.get_team_page().content.decode()

        for student in [self.ada, self.grace]:
            article = article_containing(content, student.github_login)
            self.assertIn(f'action="{generate_url(self.team.id, student.id)}"', article)
            self.assertIn('method="post"', article)
            self.assertIn("csrfmiddlewaretoken", article)
            self.assertIn("Generate summary", article)

    def test_week_picker_defaults_to_the_most_recent_completed_week(self):
        content = self.get_team_page().content.decode()

        article = article_containing(content, self.ada.github_login)
        self.assertIn('name="week_start"', article)
        self.assertIn(f'value="{LAST_WEEK.isoformat()}"', article)


class GenerateRequestTests(GenerateSummaryFixture):
    def test_get_returns_405(self):
        self.client.force_login(self.instructor)

        response = self.client.get(generate_url(self.team.id, self.ada.id))

        self.assertEqual(response.status_code, 405)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_post_without_a_csrf_token_is_rejected(self):
        self.client = self.client_class(enforce_csrf_checks=True)

        response = self.post()

        self.assertEqual(response.status_code, 403)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = generate_url(self.team.id, self.ada.id)

        response = self.client.post(url, {"week_start": str(LAST_WEEK)})

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )
        self.generate.assert_not_called()


class GenerateAccessTests(GenerateSummaryFixture):
    def test_instructor_can_generate(self):
        response = self.post(user=self.instructor)

        self.assertEqual(response.status_code, 302)
        self.generate.assert_called_once()

    def test_ta_can_generate(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.post(user=ta)

        self.assertEqual(response.status_code, 302)
        self.generate.assert_called_once()

    def assert_forbidden(self, user):
        response = self.post(user=user)

        self.assertEqual(response.status_code, 403)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_student_on_the_team_gets_403(self):
        self.assert_forbidden(self.ada)

    def test_user_not_enrolled_gets_403(self):
        self.assert_forbidden(make_user("sample-outsider"))

    def test_instructor_of_another_course_gets_403(self):
        other_instructor = make_user("sample-other-instructor")
        enroll(other_instructor, Course.objects.create(name="CS 2100"), Role.INSTRUCTOR)

        self.assert_forbidden(other_instructor)

    def test_missing_team_returns_404(self):
        self.client.force_login(self.instructor)

        response = self.client.post(
            generate_url(self.team.id + 1000, self.ada.id), {"week_start": str(LAST_WEEK)}
        )

        self.assertEqual(response.status_code, 404)
        self.generate.assert_not_called()

    def test_student_not_on_the_team_returns_404(self):
        outsider = make_user("sample-outsider")
        TeamMembership.objects.create(user=outsider, team=self.other_team)

        response = self.post(student=outsider)

        self.assertEqual(response.status_code, 404)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_student_on_another_team_returns_404(self):
        response = self.post(team=self.other_team)

        self.assertEqual(response.status_code, 404)
        self.generate.assert_not_called()


class GenerateRunTests(GenerateSummaryFixture):
    def test_ingests_each_of_the_teams_repos_for_the_week(self):
        self.post()

        self.assertCountEqual(
            self.ingest.call_args_list,
            [
                mock.call(self.app, LAST_WEEK, LAST_WEEK_END),
                mock.call(self.api, LAST_WEEK, LAST_WEEK_END),
            ],
        )

    def test_doesnt_ingest_another_teams_repos(self):
        Repo.objects.create(
            team=self.other_team,
            url="https://github.com/sample-org/elsewhere",
            full_name="sample-org/elsewhere",
        )

        self.post()

        ingested = [call.args[0].full_name for call in self.ingest.call_args_list]
        self.assertNotIn("sample-org/elsewhere", ingested)

    def test_generates_the_students_summary_for_the_week(self):
        self.post()

        self.generate.assert_called_once_with(self.ada, self.team, LAST_WEEK)

    def test_ingests_before_generating(self):
        order = []
        self.ingest.side_effect = lambda *args: order.append("ingest") or []
        self.generate.side_effect = lambda *args: order.append("generate") or self.save_summary(
            *args
        )

        self.post()

        self.assertEqual(order, ["ingest", "ingest", "generate"])

    def test_redirects_to_the_new_summary_page(self):
        response = self.post()

        summary = Summary.objects.get(team=self.team, user=self.ada, week_start=LAST_WEEK)
        self.assertRedirects(response, summary_url(summary.id), fetch_redirect_response=False)

    def test_an_earlier_completed_week_can_be_picked(self):
        self.post(week_start=date(2026, 10, 12))

        self.ingest.assert_any_call(self.app, date(2026, 10, 12), date(2026, 10, 18))
        self.generate.assert_called_once_with(self.ada, self.team, date(2026, 10, 12))


class GenerateWeekValidationTests(GenerateSummaryFixture):
    def assert_rejected(self, week_start, message):
        response = self.post(week_start=week_start, follow=True)

        self.assertRedirects(response, team_url(self.team.id))
        self.assertContains(response, escape(message))
        self.ingest.assert_not_called()
        self.generate.assert_not_called()
        self.assertFalse(Summary.objects.exists())

    def test_week_start_that_isnt_a_monday_is_rejected(self):
        self.assert_rejected(date(2026, 10, 27), "The week must start on a Monday.")

    def test_current_week_hasnt_ended_and_is_rejected(self):
        self.assert_rejected(date(2026, 11, 2), "That week hasn't ended yet.")

    def test_future_week_is_rejected(self):
        self.assert_rejected(date(2026, 11, 9), "That week hasn't ended yet.")

    def test_missing_week_is_rejected(self):
        self.assert_rejected(None, "Pick a week to generate.")

    def test_unreadable_week_is_rejected(self):
        self.assert_rejected("next tuesday", "Pick a week to generate.")


class GenerateFailureTests(GenerateSummaryFixture):
    def test_ingest_failure_returns_to_team_page_naming_the_step(self):
        self.ingest.side_effect = RuntimeError("GitHub is down")

        response = self.post(follow=True)

        self.assertRedirects(response, team_url(self.team.id))
        self.assertContains(response, "Couldn&#x27;t pull GitHub activity")
        self.assertContains(response, "No summary was generated.")
        self.generate.assert_not_called()

    def test_summary_failure_returns_to_team_page_naming_the_step(self):
        self.generate.side_effect = LLMError("The LLM request timed out.")

        response = self.post(follow=True)

        self.assertRedirects(response, team_url(self.team.id))
        self.assertContains(response, "Couldn&#x27;t write the summary")
        self.assertContains(response, "The LLM request timed out.")
        self.assertContains(response, "No summary was generated.")

    def test_summary_failure_shows_no_summary(self):
        self.generate.side_effect = LLMError("The LLM request timed out.")

        response = self.post(follow=True)

        self.assertContains(response, "No summaries yet.", count=2)

    def test_success_shows_no_error(self):
        response = self.post(follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "No summary was generated.")


class RegenerateTests(GenerateSummaryFixture):
    def test_regenerating_shows_the_new_claims_on_the_summary_page(self):
        summary = Summary.objects.create(team=self.team, user=self.ada, week_start=LAST_WEEK)
        Claim.objects.create(
            summary=summary,
            text="Old claim text",
            category="feature",
            artifact_ids=[],
            requirement_ids=[],
            significance="minor",
            status=Claim.Status.VERIFIED,
        )

        def replace_claims(user, team, week_start):
            summary.claims.all().delete()
            Claim.objects.create(
                summary=summary,
                text="New claim text",
                category="tests",
                artifact_ids=[],
                requirement_ids=[],
                significance="moderate",
                status=Claim.Status.VERIFIED,
            )
            return summary

        self.generate.side_effect = replace_claims

        response = self.post(follow=True)

        self.assertRedirects(response, summary_url(summary.id))
        self.assertContains(response, "New claim text")
        self.assertNotContains(response, "Old claim text")
        self.assertEqual(Summary.objects.filter(user=self.ada, week_start=LAST_WEEK).count(), 1)


class GenerateFlagTests(GenerateSummaryFixture):
    def make_stale_flag(self):
        return Flag.objects.create(
            team=self.team,
            user=self.ada,
            rule="low-requirement-share",
            reason="Stale flag",
            severity=Flag.Severity.FLAG,
            week_start=date(2026, 10, 19),
        )

    def test_flags_are_recomputed_after_generating(self):
        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            self.post()

        compute_flags.assert_called_once_with(self.team)

    def test_recomputing_replaces_the_teams_earlier_flags(self):
        stale = self.make_stale_flag()

        self.post()

        # Neither student has any claims, so the rules raise no flags.
        self.assertFalse(Flag.objects.filter(pk=stale.pk).exists())

    def test_flags_are_left_alone_when_generating_fails(self):
        stale = self.make_stale_flag()
        self.generate.side_effect = LLMError("The LLM request timed out.")

        self.post()

        self.assertTrue(Flag.objects.filter(pk=stale.pk).exists())
