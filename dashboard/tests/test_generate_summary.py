"""Tests for US-22 (#83): generate a team's weekly summaries with one click.

`ingest` and `generate_summary` are mocked, so no test makes a network or LLM call. The
end-to-end tests run the real `generate_summary` and mock only the LLM client call. The
reporting clock is fixed at Monday Nov 2, 2026, noon in New York, which makes the week of
Oct 26 the most recent completed week.

URLs are reversed per test, and dashboard.services is reached through the module, so each
criterion passes or fails on its own.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Sprint, Team, TeamMembership
from dashboard import services
from dashboard.models import Flag
from ingest import services as ingest_services
from ingest.models import Artifact
from summaries import schemas
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from .sprint_helpers import sprint_for
from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_show_flags import article_containing

Role = Enrollment.Role
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=NEW_YORK)
LAST_WEEK = date(2026, 10, 26)
LAST_WEEK_END = date(2026, 11, 1)
EARLIER_WEEK = date(2026, 10, 12)
EARLIER_WEEK_END = date(2026, 10, 18)
GENERATE_LABEL = "Generate summaries"
RETRY_LABEL = "Retry failed summaries"


def generate_url(team_id):
    return reverse("dashboard:generate_team_summaries", args=[team_id])


def retry_url(team_id):
    return reverse("dashboard:retry_failed_summaries", args=[team_id])


def team_article(content, team_name):
    """The team's <article>. Messages above it can also name the team, so match its header."""
    return article_containing(content, f"<strong>{team_name}</strong>")


def page_messages(response):
    """The text of the messages on a page, unescaped."""
    return " ".join(str(message) for message in response.context["messages"])


class TeamGenerationFixture(TestCase):
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

        cls.empty_team = Team.objects.create(project=cls.project, name="Team Beta")
        # The overview only offers the Generate summaries control once a sprint has started.
        cls.sprint = Sprint.objects.create(
            project=cls.project, name="Sprint 1", start_date=date(2026, 10, 19), end_date=date(2026, 11, 15)
        )

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

        self.ingest = self.patch("ingest.services.ingest", return_value=[])
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

    def fail_for(self, *students, error=None):
        """generate_summary fails for these students and works for everyone else."""
        error = error or LLMError("The LLM request timed out.")

        def generate(user, team, week_start):
            if user in students:
                raise error
            return self.save_summary(user, team, week_start)

        self.generate.side_effect = generate

    def post(self, week_start=LAST_WEEK, user=None, team=None, **kwargs):
        self.client.force_login(user or self.instructor)
        data = {} if week_start is None else {"week_start": str(week_start)}
        return self.client.post(generate_url((team or self.team).id), data, **kwargs)

    def post_retry(self, user=None, team=None, **kwargs):
        self.client.force_login(user or self.instructor)
        return self.client.post(retry_url((team or self.team).id), **kwargs)

    def get_overview(self):
        self.client.force_login(self.instructor)
        return self.client.get(overview_url(self.course.id)).content.decode()

    def reset_calls(self):
        self.ingest.reset_mock()
        self.generate.reset_mock()

    def assert_nothing_ran(self):
        self.ingest.assert_not_called()
        self.generate.assert_not_called()


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


class GenerateButtonTests(TeamGenerationFixture):
    def test_each_team_on_the_overview_has_one_generate_form(self):
        article = team_article(self.get_overview(), "Team Alpha")

        self.assertEqual(article.count(f'action="{generate_url(self.team.id)}"'), 1)
        self.assertIn('method="post"', article)
        self.assertIn("csrfmiddlewaretoken", article)
        self.assertEqual(article.count(GENERATE_LABEL), 1)

    def test_the_team_page_has_no_generate_button(self):
        self.client.force_login(self.instructor)

        content = self.client.get(team_url(self.team.id)).content.decode()

        self.assertNotIn("Generate summar", content)
        self.assertNotIn('name="week_start"', content)

    def test_the_per_student_route_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse("dashboard:generate_summary", args=[self.team.id, self.ada.id])


class GenerateRequestTests(TeamGenerationFixture):
    def test_get_returns_405(self):
        self.client.force_login(self.instructor)

        response = self.client.get(generate_url(self.team.id))

        self.assertEqual(response.status_code, 405)
        self.assert_nothing_ran()

    def test_post_without_a_csrf_token_is_rejected(self):
        self.client = self.client_class(enforce_csrf_checks=True)

        response = self.post()

        self.assertEqual(response.status_code, 403)
        self.assert_nothing_ran()

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = generate_url(self.team.id)

        response = self.client.post(url, {"week_start": str(LAST_WEEK)})

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )
        self.assert_nothing_ran()


class GenerateAccessTests(TeamGenerationFixture):
    def test_instructor_can_generate(self):
        response = self.post(user=self.instructor)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.generate.call_count, 2)

    def test_ta_can_generate(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.post(user=ta)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.generate.call_count, 2)

    def assert_forbidden(self, user):
        response = self.post(user=user)

        self.assertEqual(response.status_code, 403)
        self.assert_nothing_ran()

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
            generate_url(self.team.id + 1000), {"week_start": str(LAST_WEEK)}
        )

        self.assertEqual(response.status_code, 404)
        self.assert_nothing_ran()


class GenerateRunTests(TeamGenerationFixture):
    def test_ingests_each_of_the_teams_repos_once_for_the_week(self):
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
            team=self.empty_team,
            url="https://github.com/sample-org/elsewhere",
            full_name="sample-org/elsewhere",
        )

        self.post()

        ingested = [call.args[0].full_name for call in self.ingest.call_args_list]
        self.assertNotIn("sample-org/elsewhere", ingested)

    def test_generates_a_summary_once_for_each_student_on_the_team(self):
        self.post()

        self.assertCountEqual(
            self.generate.call_args_list,
            [
                mock.call(self.ada, self.team, LAST_WEEK),
                mock.call(self.grace, self.team, LAST_WEEK),
            ],
        )

    def test_doesnt_generate_for_students_on_another_team(self):
        outsider = make_user("sample-outsider")
        TeamMembership.objects.create(user=outsider, team=self.empty_team)

        self.post()

        self.assertNotIn(outsider, [call.args[0] for call in self.generate.call_args_list])

    def test_ingests_every_repo_before_generating_any_summary(self):
        order = []
        self.ingest.side_effect = lambda *args: order.append("ingest") or []
        self.generate.side_effect = lambda *args: order.append("generate") or self.save_summary(
            *args
        )

        self.post()

        self.assertEqual(order, ["ingest", "ingest", "generate", "generate"])

    def test_an_earlier_completed_week_can_be_picked(self):
        self.post(week_start=EARLIER_WEEK)

        self.ingest.assert_any_call(self.app, EARLIER_WEEK, EARLIER_WEEK_END)
        self.generate.assert_any_call(self.ada, self.team, EARLIER_WEEK)
        self.generate.assert_any_call(self.grace, self.team, EARLIER_WEEK)

    def test_success_returns_to_the_overview_with_the_team_week_and_student_count(self):
        response = self.post(follow=True)

        self.assertRedirects(response, overview_url(self.course.id))
        message = page_messages(response)
        self.assertIn("Team Alpha", message)
        self.assertIn("Oct 26", message)
        self.assertIn("2 students", message)


class GenerateValidationTests(TeamGenerationFixture):
    def assert_rejected(self, week_start, message):
        response = self.post(week_start=week_start, follow=True)

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertIn(message, page_messages(response))
        self.assert_nothing_ran()
        self.assertFalse(Summary.objects.exists())

    def test_week_start_that_isnt_a_monday_is_rejected(self):
        self.assert_rejected(date(2026, 10, 27), "The week must start on a Monday.")

    def test_future_week_is_rejected(self):
        self.assert_rejected(date(2026, 11, 9), "That week hasn't started yet.")

    def test_missing_week_is_rejected(self):
        self.assert_rejected(None, "Pick a week to generate.")

    def test_unreadable_week_is_rejected(self):
        self.assert_rejected("next tuesday", "Pick a week to generate.")

    def test_team_with_no_students_shows_a_message_and_nothing_runs(self):
        response = self.post(team=self.empty_team, follow=True)

        self.assertRedirects(response, overview_url(self.course.id))
        message = page_messages(response)
        self.assertIn("Team Beta", message)
        self.assertIn("no students", message.lower())
        self.assert_nothing_ran()


class IngestFailureTests(TeamGenerationFixture):
    """US-8 (#24): staff can tell why a refresh failed, and a failure changes nothing."""

    def fail_ingest_on_last_repo(self, error):
        """Stores an artifact for each repo before the last one, then fails on the last."""
        last_repo = list(services.team_repos(self.team))[-1]

        def ingest(repo, week_start, week_end):
            if repo == last_repo:
                raise error
            Artifact.objects.create(
                repo=repo,
                artifact_id=f"{repo.full_name}:pr:1",
                type=Artifact.Type.PR,
                url=f"https://github.com/{repo.full_name}/pull/1",
                timestamp=NOW,
            )
            return []

        self.ingest.side_effect = ingest

    def test_each_failure_shows_its_own_message_and_names_the_repo(self):
        for failure in (
            ingest_services.BadTokenError,
            ingest_services.RepoNotFoundError,
            ingest_services.RateLimitError,
            ingest_services.IngestError,
        ):
            with self.subTest(failure.__name__):
                reason = f"The reason is {failure.__name__}."
                self.ingest.side_effect = failure(reason)

                response = self.post(follow=True)

                self.assertRedirects(response, overview_url(self.course.id))
                message = page_messages(response)
                self.assertIn(reason, message)
                # Repos run in name order, so api is the one that fails.
                self.assertIn("sample-org/api", message)
                self.generate.assert_not_called()

    def test_an_unexpected_failure_keeps_its_details_off_the_page(self):
        self.ingest.side_effect = RuntimeError("sample-secret-detail")

        response = self.post(follow=True)

        self.assertIn("Couldn't pull GitHub activity", page_messages(response))
        self.assertNotContains(response, "sample-secret-detail")
        self.generate.assert_not_called()

    def test_a_failure_on_a_later_repo_leaves_earlier_repos_unchanged(self):
        for failure in (ingest_services.RateLimitError("Rate limited."), RuntimeError("Oops.")):
            with self.subTest(type(failure).__name__):
                self.fail_ingest_on_last_repo(failure)
                self.ingest.reset_mock()

                self.post(follow=True)

                self.assertEqual(self.ingest.call_count, 2)
                self.assertEqual(Artifact.objects.count(), 0)
                self.generate.assert_not_called()

    def test_an_ingest_failure_offers_no_retry_of_failed_summaries(self):
        self.ingest.side_effect = RuntimeError("Oops.")

        self.post()

        self.assertNotIn(RETRY_LABEL, self.get_overview())


class PartialFailureTests(TeamGenerationFixture):
    def test_other_students_are_still_processed_after_one_fails(self):
        # Students are processed in name order, so either could come first.
        for failing, other in ((self.ada, self.grace), (self.grace, self.ada)):
            with self.subTest(failing=failing.github_login):
                self.reset_calls()
                self.fail_for(failing)

                self.post()

                self.assertEqual(self.generate.call_count, 2)
                self.assertTrue(
                    Summary.objects.filter(team=self.team, user=other, week_start=LAST_WEEK).exists()
                )
                Summary.objects.all().delete()

    def test_message_says_how_many_succeeded_and_which_students_failed(self):
        self.fail_for(self.ada)

        response = self.post(follow=True)

        self.assertRedirects(response, overview_url(self.course.id))
        message = page_messages(response)
        self.assertIn("Team Alpha", message)
        self.assertIn("Oct 26", message)
        self.assertIn("1 of 2", message)
        self.assertIn("Ada Sample", message)
        self.assertNotIn("Grace Example", message)

    def test_a_student_without_a_name_is_named_by_github_login(self):
        nameless = make_user("sample-nameless")
        TeamMembership.objects.create(user=nameless, team=self.team)
        self.fail_for(nameless)

        response = self.post(follow=True)

        self.assertIn("sample-nameless", page_messages(response))

    def test_a_known_safe_llm_error_is_shown(self):
        self.fail_for(self.ada, error=LLMError("The LLM request timed out."))

        response = self.post(follow=True)

        self.assertIn("The LLM request timed out.", page_messages(response))

    def test_an_unexpected_error_keeps_its_details_off_the_page(self):
        self.fail_for(self.ada, error=RuntimeError("sample-student-code"))

        response = self.post(follow=True)

        self.assertIn("Ada Sample", page_messages(response))
        self.assertNotContains(response, "sample-student-code")
        self.assertEqual(self.generate.call_count, 2)

    def test_a_failed_student_keeps_their_earlier_summary_for_the_week(self):
        earlier = Summary.objects.create(team=self.team, user=self.ada, week_start=LAST_WEEK)
        self.fail_for(self.ada)

        self.post()

        self.assertEqual(Summary.objects.get(user=self.ada, week_start=LAST_WEEK), earlier)

    def test_when_everyone_fails_the_message_says_none_succeeded(self):
        self.fail_for(self.ada, self.grace)

        response = self.post(follow=True)

        message = page_messages(response)
        self.assertIn("0 of 2", message)
        self.assertIn("Ada Sample", message)
        self.assertIn("Grace Example", message)


class RegenerateTests(TeamGenerationFixture):
    def test_generating_again_runs_ingest_and_every_student_again(self):
        self.post()
        self.reset_calls()

        self.post()

        self.assertEqual(self.ingest.call_count, 2)
        self.assertEqual(self.generate.call_count, 2)

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
            if user != self.ada:
                return self.save_summary(user, team, week_start)
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

        self.post()

        response = self.client.get(reverse("dashboard:summary", args=[summary.id]))
        self.assertContains(response, "New claim text")
        self.assertNotContains(response, "Old claim text")
        self.assertEqual(Summary.objects.filter(user=self.ada, week_start=LAST_WEEK).count(), 1)


class RetryButtonTests(TeamGenerationFixture):
    def test_no_button_before_any_failure(self):
        self.assertNotIn(RETRY_LABEL, self.get_overview())

    def test_no_button_after_a_full_success(self):
        self.post()

        self.assertNotIn(RETRY_LABEL, self.get_overview())

    def test_partial_failure_shows_the_button_on_that_team_only(self):
        self.fail_for(self.ada)
        self.post()

        content = self.get_overview()

        article = team_article(content, "Team Alpha")
        self.assertIn(RETRY_LABEL, article)
        self.assertIn(f'action="{retry_url(self.team.id)}"', article)
        self.assertIn('method="post"', article)
        self.assertNotIn(RETRY_LABEL, team_article(content, "Team Beta"))

    def test_button_stays_for_students_who_still_fail(self):
        self.fail_for(self.ada, self.grace)
        self.post()
        self.fail_for(self.ada)

        self.post_retry()

        self.assertIn(RETRY_LABEL, self.get_overview())
        self.reset_calls()
        self.post_retry()
        self.generate.assert_called_once_with(self.ada, self.team, LAST_WEEK)

    def test_button_goes_once_no_failed_summaries_remain(self):
        self.fail_for(self.ada)
        self.post()
        self.fail_for()

        self.post_retry()

        self.assertNotIn(RETRY_LABEL, self.get_overview())

    def test_button_goes_after_the_whole_team_generates_successfully(self):
        self.fail_for(self.ada)
        self.post()
        self.fail_for()

        self.post()

        self.assertNotIn(RETRY_LABEL, self.get_overview())


class RetryRunTests(TeamGenerationFixture):
    def fail_then_reset(self, *students, week_start=LAST_WEEK):
        self.fail_for(*students)
        self.post(week_start=week_start)
        self.fail_for()
        self.reset_calls()

    def test_retries_only_the_students_who_failed(self):
        self.fail_then_reset(self.ada)

        self.post_retry()

        self.generate.assert_called_once_with(self.ada, self.team, LAST_WEEK)

    def test_uses_the_week_that_failed_not_the_default(self):
        self.fail_then_reset(self.ada, week_start=EARLIER_WEEK)

        self.post_retry()

        self.generate.assert_called_once_with(self.ada, self.team, EARLIER_WEEK)

    def test_doesnt_run_ingest_again(self):
        self.fail_then_reset(self.ada)

        self.post_retry()

        self.ingest.assert_not_called()

    def test_doesnt_touch_summaries_that_succeeded(self):
        self.fail_then_reset(self.ada)
        grace_summary = Summary.objects.get(user=self.grace, week_start=LAST_WEEK)
        updated_at = list(Summary.objects.filter(pk=grace_summary.pk).values())

        self.post_retry()

        self.assertEqual(list(Summary.objects.filter(pk=grace_summary.pk).values()), updated_at)

    def test_saves_the_retried_students_summary(self):
        self.fail_then_reset(self.ada)

        self.post_retry()

        self.assertTrue(Summary.objects.filter(user=self.ada, week_start=LAST_WEEK).exists())

    def test_skips_anyone_no_longer_on_the_team(self):
        self.fail_then_reset(self.ada, self.grace)
        TeamMembership.objects.get(user=self.grace, team=self.team).delete()

        self.post_retry()

        self.generate.assert_called_once_with(self.ada, self.team, LAST_WEEK)

    def test_returns_to_the_overview_with_a_message_naming_the_team_and_week(self):
        self.fail_then_reset(self.ada)

        response = self.post_retry(follow=True)

        self.assertRedirects(response, overview_url(self.course.id))
        message = page_messages(response)
        self.assertIn("Team Alpha", message)
        self.assertIn("Oct 26", message)

    def test_a_failed_retry_says_which_students_still_failed(self):
        self.fail_then_reset(self.ada)
        self.fail_for(self.ada)

        response = self.post_retry(follow=True)

        self.assertIn("Ada Sample", page_messages(response))

    def test_an_unexpected_retry_error_keeps_its_details_off_the_page(self):
        self.fail_then_reset(self.ada)
        self.fail_for(self.ada, error=RuntimeError("sample-student-code"))

        response = self.post_retry(follow=True)

        self.assertNotContains(response, "sample-student-code")

    def test_a_new_partial_failure_replaces_the_students_to_retry(self):
        self.fail_then_reset(self.ada, week_start=EARLIER_WEEK)
        self.fail_for(self.grace)
        self.post()
        self.fail_for()
        self.reset_calls()

        self.post_retry()

        self.generate.assert_called_once_with(self.grace, self.team, LAST_WEEK)

    def test_with_nothing_to_retry_nothing_runs(self):
        response = self.post_retry()

        self.assertRedirects(
            response, overview_url(self.course.id), fetch_redirect_response=False
        )
        self.assert_nothing_ran()

    def test_retrying_one_team_doesnt_run_another_teams_failures(self):
        self.fail_then_reset(self.ada)

        self.post_retry(team=self.empty_team)

        self.generate.assert_not_called()


class RetryRequestTests(TeamGenerationFixture):
    def test_get_returns_405(self):
        self.client.force_login(self.instructor)

        response = self.client.get(retry_url(self.team.id))

        self.assertEqual(response.status_code, 405)
        self.assert_nothing_ran()

    def test_post_without_a_csrf_token_is_rejected(self):
        self.client = self.client_class(enforce_csrf_checks=True)

        response = self.post_retry()

        self.assertEqual(response.status_code, 403)
        self.assert_nothing_ran()

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = retry_url(self.team.id)

        response = self.client.post(url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )
        self.assert_nothing_ran()

    def test_ta_can_retry(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.fail_for(self.ada)
        self.post(user=ta)
        self.fail_for()
        self.reset_calls()

        response = self.post_retry(user=ta)

        self.assertEqual(response.status_code, 302)
        self.generate.assert_called_once_with(self.ada, self.team, LAST_WEEK)

    def assert_forbidden(self, user):
        response = self.post_retry(user=user)

        self.assertEqual(response.status_code, 403)
        self.assert_nothing_ran()

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

        response = self.client.post(retry_url(self.team.id + 1000))

        self.assertEqual(response.status_code, 404)
        self.assert_nothing_ran()


class GenerateFlagTests(TeamGenerationFixture):
    def make_stale_flag(self):
        return Flag.objects.create(
            team=self.team,
            user=self.ada,
            rule="low-requirement-share",
            reason="Stale flag",
            severity=Flag.Severity.FLAG,
            sprint=sprint_for(self.team.project),
        )

    def test_flags_are_recomputed_once_after_every_student_succeeds(self):
        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            self.post()

        compute_flags.assert_called_once_with(self.team)

    def test_recomputing_replaces_the_teams_earlier_flags(self):
        stale = self.make_stale_flag()

        self.post()

        # Neither student has any claims, so the rules raise no flags.
        self.assertFalse(Flag.objects.filter(pk=stale.pk).exists())

    def test_flags_are_left_alone_after_a_partial_failure_and_the_message_says_so(self):
        stale = self.make_stale_flag()
        self.fail_for(self.ada)

        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            response = self.post(follow=True)

        compute_flags.assert_not_called()
        self.assertTrue(Flag.objects.filter(pk=stale.pk).exists())
        self.assertIn("Flags weren't updated", page_messages(response))

    def test_flags_are_left_alone_after_an_ingest_failure(self):
        stale = self.make_stale_flag()
        self.ingest.side_effect = RuntimeError("Oops.")

        self.post()

        self.assertTrue(Flag.objects.filter(pk=stale.pk).exists())

    def test_flags_are_recomputed_after_a_retry_clears_every_failure(self):
        self.fail_for(self.ada)
        self.post()
        self.fail_for()

        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            self.post_retry()

        compute_flags.assert_called_once_with(self.team)

    def test_flags_are_left_alone_after_a_retry_that_still_fails(self):
        self.fail_for(self.ada)
        self.post()

        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            response = self.post_retry(follow=True)

        compute_flags.assert_not_called()
        self.assertIn("Flags weren't updated", page_messages(response))

    def test_flags_are_recomputed_when_the_only_failed_student_has_left_the_team(self):
        self.fail_for(self.ada)
        self.post()
        TeamMembership.objects.get(user=self.ada, team=self.team).delete()

        with mock.patch("dashboard.flags.compute_flags") as compute_flags:
            self.post_retry()

        compute_flags.assert_called_once_with(self.team)
        self.assertNotIn(RETRY_LABEL, self.get_overview())


class OneClickEndToEndTests(TestCase):
    """The real generate_summary, validator and flag rules. Only GitHub and the LLM are mocked."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        Requirement.objects.create(project=cls.project, number=1, text="Sample requirement")
        cls.sprint = sprint_for(cls.project)
        Requirement.objects.create(
            project=cls.project, sprint=cls.sprint, number=1, text="Sample requirement"
        )
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.repo = Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/app", full_name="sample-org/app"
        )
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)
        Artifact.objects.create(
            repo=cls.repo,
            artifact_id="sample-org/app:pr:1",
            type=Artifact.Type.PR,
            url="https://github.com/sample-org/app/pull/1",
            timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=NEW_YORK),
            author_login="sample-ada",
        )

    def setUp(self):
        for patcher in (
            mock.patch("django.utils.timezone.now", return_value=NOW),
            mock.patch("ingest.services.ingest", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        llm = mock.patch(
            "summaries.services.generate_claims",
            return_value=[
                schemas.Claim(
                    text="Opened a PR for the sample requirement.",
                    category="feature",
                    artifact_ids=["sample-org/app:pr:1"],
                    requirement_ids=[1],
                    significance="major",
                )
            ],
        )
        self.generate_claims = llm.start()
        self.addCleanup(llm.stop)

    def click_generate(self):
        self.client.force_login(self.instructor)
        return self.client.post(
            generate_url(self.team.id), {"week_start": str(LAST_WEEK)}, follow=True
        )

    def test_a_student_with_no_activity_gets_a_summary_with_no_llm_call(self):
        self.click_generate()

        self.generate_claims.assert_called_once()
        summary = Summary.objects.get(team=self.team, user=self.grace, week_start=LAST_WEEK)
        self.assertFalse(summary.claims.exists())

    def test_one_click_flags_a_team_with_no_earlier_summaries(self):
        # Flags follow sprints (US-41), so this generates the sprint that just ended.
        self.client.force_login(self.instructor)
        response = self.client.post(
            generate_url(self.team.id), {"sprint": str(self.sprint.id)}, follow=True
        )

        flag = Flag.objects.get(team=self.team)
        self.assertEqual(flag.user, self.grace)
        self.assertEqual(flag.severity, Flag.Severity.FLAG)
        article = team_article(response.content.decode(), "Team Alpha")
        self.assertIn("<mark>", article)
        self.assertIn("Flag", article)
        self.assertNotIn(flag.reason.split(" did ")[1], article)


class GeneratingIndicatorTests(TeamGenerationFixture):
    """The markup the page's script relies on. The script's behaviour needs a real browser."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.instructor)

    def page(self):
        return self.client.get(overview_url(self.course.id)).content.decode()

    def form_tag(self, content, action):
        start = content.rindex("<form", 0, content.index(f'action="{action}"'))
        return content[start : content.index(">", start)]

    def test_every_generate_form_is_marked_for_the_script(self):
        content = self.page()

        for team in [self.team, self.empty_team]:
            with self.subTest(team=team.name):
                self.assertIn(
                    'class="generate-form"', self.form_tag(content, generate_url(team.id))
                )

    def test_a_retry_form_is_marked_too_since_it_also_takes_a_while(self):
        self.fail_for(self.ada)
        self.client.post(generate_url(self.team.id), {"week_start": LAST_WEEK.isoformat()})

        tag = self.form_tag(self.page(), retry_url(self.team.id))

        self.assertIn('class="generate-form"', tag)

    def test_the_page_includes_the_script_that_shows_the_spinner(self):
        content = self.page()

        self.assertIn("<script>", content)
        self.assertIn('".generate-form"', content)
        self.assertIn('"aria-busy"', content)
        self.assertIn("pageshow", content)
