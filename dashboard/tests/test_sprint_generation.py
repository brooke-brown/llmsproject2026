"""Tests for US-40 (#122): generate a team's summaries for a sprint.

`ingest` and `generate_sprint_summary` are mocked, so nothing makes a network or LLM call.
The clock is fixed at Monday Nov 2, 2026, noon in New York. The weekly flow keeps working
beside this one until T-21 removes it, so its tests are in test_generate_summary.py.
"""

import re
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Sprint, Team, TeamMembership
from dashboard import services
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_generate_summary import generate_url, page_messages, retry_url, team_article

Role = Enrollment.Role
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=NY)


def at(month, day, hour=12):
    return datetime(2026, month, day, hour, 0, tzinfo=NY)


class SprintGenerationFixture(TestCase):
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
        for student in (cls.ada, cls.grace):
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.team)

        cls.ended = Sprint.objects.create(
            project=cls.project, name="Sprint 1", start_date=date(2026, 10, 5), end_date=date(2026, 10, 18)
        )
        cls.running = Sprint.objects.create(
            project=cls.project, name="Sprint 2", start_date=date(2026, 10, 19), end_date=date(2026, 11, 8)
        )
        cls.future = Sprint.objects.create(
            project=cls.project, name="Sprint 3", start_date=date(2026, 11, 8), end_date=date(2026, 11, 14)
        )
        cls.other_project = Project.objects.create(course=cls.course, name="Project Two")
        cls.foreign = Sprint.objects.create(
            project=cls.other_project, name="Sprint 1", start_date=date(2026, 10, 5), end_date=date(2026, 10, 18)
        )

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.ingest = self.patch("ingest.services.ingest", return_value=[])
        self.generate = self.patch("summaries.services.generate_sprint_summary")
        self.generate.side_effect = self.save_summary
        self.weekly = self.patch("summaries.services.generate_summary")
        self.compute_flags = self.patch("dashboard.flags.compute_flags")
        self.client.force_login(self.instructor)

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def save_summary(user, team, sprint):
        summary, _ = Summary.objects.get_or_create(team=team, user=user, sprint=sprint)
        return summary

    def fail_for(self, *students):
        def generate(user, team, sprint):
            if user in students:
                raise LLMError("The LLM request timed out.")
            return self.save_summary(user, team, sprint)

        self.generate.side_effect = generate

    def post(self, sprint=None, team=None, **kwargs):
        data = {} if sprint is None else {"sprint": str(sprint.id if hasattr(sprint, "id") else sprint)}
        return self.client.post(generate_url((team or self.team).id), data, **kwargs)

    def overview(self):
        return self.client.get(overview_url(self.course.id)).content.decode()


class SprintChoiceTests(SprintGenerationFixture):
    def test_only_started_sprints_are_offered_labelled_with_dates(self):
        article = team_article(self.overview(), "Team Alpha")
        self.assertIn("Sprint 2 (Oct 19 – Nov 8)", article)
        self.assertIn("Sprint 1 (Oct 5 – Oct 18)", article)
        self.assertNotIn("Sprint 3", article)  # Starts Nov 8, so not started.

    def test_another_projects_sprints_are_not_offered(self):
        Sprint.objects.create(
            project=self.other_project, name="Elsewhere", start_date=date(2026, 10, 1), end_date=date(2026, 10, 4)
        )
        # Project Two's own sprint menu lists it (US-46), but this team's form doesn't.
        self.assertNotIn("Elsewhere", team_article(self.overview(), "Team Alpha"))

    def test_it_defaults_to_the_current_sprint(self):
        article = team_article(self.overview(), "Team Alpha")
        selected = re.search(r'<option value="(\d+)" selected', article).group(1)
        self.assertEqual(int(selected), self.running.id)

    def test_one_form_posts_a_sprint_not_a_week(self):
        article = team_article(self.overview(), "Team Alpha")
        self.assertEqual(article.count('name="sprint"'), 1)
        self.assertNotIn('name="week_start"', article)
        self.assertEqual(article.count("Generate summaries"), 1)

    def test_a_project_with_no_started_sprint_shows_a_message_instead_of_the_control(self):
        Sprint.objects.filter(project=self.project).update(start_date=date(2026, 12, 1), end_date=date(2026, 12, 7))
        article = team_article(self.overview(), "Team Alpha")
        self.assertIn("No sprint has started yet", article)
        self.assertNotIn('name="sprint"', article)
        self.assertNotIn("Generate summaries", article)


class DefaultSprintTests(SprintGenerationFixture):
    def default(self, now):
        return services.default_sprint(self.project, now)

    def test_the_sprint_covering_today(self):
        self.assertEqual(self.default(at(11, 2)), self.running)

    def test_on_a_shared_day_it_is_the_sprint_that_just_started(self):
        self.future.delete()
        shared = Sprint.objects.create(
            project=self.project, name="Sprint 4", start_date=date(2026, 11, 8), end_date=date(2026, 11, 14)
        )
        self.assertEqual(self.default(at(11, 8)), shared)  # Sprint 2 ends and Sprint 4 starts today.

    def test_between_sprints_it_is_the_most_recently_ended(self):
        self.running.start_date = date(2026, 10, 20)
        self.running.end_date = date(2026, 10, 25)
        self.running.save()
        self.assertEqual(self.default(at(11, 2)), self.running)

    def test_with_no_started_sprint_there_is_none(self):
        self.assertIsNone(self.default(at(9, 1)))

    def test_started_sprints_are_in_date_order_and_skip_future_ones(self):
        self.assertEqual(services.started_sprints(self.project, at(11, 2)), [self.ended, self.running])
        self.assertEqual(services.started_sprints(self.project, at(11, 8)), [self.ended, self.running, self.future])


class GenerateBySprintTests(SprintGenerationFixture):
    def test_it_ingests_each_repo_through_today_for_a_running_sprint(self):
        self.post(self.running)
        self.assertEqual(self.ingest.call_count, 2)
        for call in self.ingest.call_args_list:
            self.assertEqual(call.args[1:], (date(2026, 10, 19), date(2026, 11, 2)))
        self.assertEqual({call.args[0] for call in self.ingest.call_args_list}, {self.app, self.api})

    def test_it_ingests_through_the_last_day_for_an_ended_sprint(self):
        self.post(self.ended)
        for call in self.ingest.call_args_list:
            self.assertEqual(call.args[1:], (date(2026, 10, 5), date(2026, 10, 18)))

    def test_it_then_writes_one_summary_per_student_for_the_sprint(self):
        self.post(self.ended)
        self.assertEqual(
            sorted(call.args[0].github_login for call in self.generate.call_args_list),
            ["sample-ada", "sample-grace"],
        )
        for call in self.generate.call_args_list:
            self.assertEqual(call.args[1:], (self.team, self.ended))
        self.weekly.assert_not_called()
        self.assertEqual(Summary.objects.filter(sprint=self.ended).count(), 2)

    def test_the_overview_says_what_was_generated_with_the_sprint_name_and_dates(self):
        response = self.post(self.ended, follow=True)
        message = page_messages(response)
        self.assertIn("Generated summaries for 2 students on Team Alpha for Sprint 1 (Oct 5 – Oct 18)", message)
        self.assertNotIn("in progress", message)

    def test_a_running_sprint_is_labelled_in_progress_with_the_date_it_covers_through(self):
        message = page_messages(self.post(self.running, follow=True))
        self.assertIn("still in progress", message)
        self.assertIn("Nov 2", message)

    def test_flags_are_recomputed_once_every_student_succeeds(self):
        # US-41: compute_flags decides which sprints can raise flags.
        self.post(self.ended)
        self.compute_flags.assert_called_once_with(self.team)

    def test_flags_are_recomputed_for_a_running_sprint_too(self):
        # A running sprint raises none itself, but an earlier sprint's flags stay current.
        self.post(self.running)
        self.compute_flags.assert_called_once_with(self.team)

    def test_a_sprint_that_has_not_started_is_rejected(self):
        message = page_messages(self.post(self.future, follow=True))
        self.assertIn("hasn't started yet", message)
        self.ingest.assert_not_called()
        self.generate.assert_not_called()

    def test_a_sprint_from_another_project_is_rejected(self):
        message = page_messages(self.post(self.foreign, follow=True))
        self.assertIn("isn't in this team's project", message)
        self.ingest.assert_not_called()

    def test_a_missing_or_nonsense_sprint_is_rejected(self):
        for value in ("", "abc", "999999"):
            message = page_messages(self.post(value, follow=True))
            self.assertTrue("Pick a sprint" in message or "isn't in this team's project" in message, value)
        self.ingest.assert_not_called()

    def test_a_team_already_generating_is_refused(self):
        from dashboard.models import GenerationRun

        GenerationRun.objects.create(team=self.team, expires_at=at(11, 2, 13))
        message = page_messages(self.post(self.ended, follow=True))
        self.assertIn("already being generated", message)
        self.generate.assert_not_called()

    def test_a_failed_ingest_runs_no_summaries(self):
        from ingest.services import IngestError

        self.ingest.side_effect = IngestError("GitHub is down.")
        message = page_messages(self.post(self.ended, follow=True))
        self.assertIn("sample-org/api", message)  # Repos are pulled in name order.
        self.generate.assert_not_called()


class RetryBySprintTests(SprintGenerationFixture):
    def test_failed_students_are_reported_by_name_and_flags_are_not_touched(self):
        self.fail_for(self.grace)
        message = page_messages(self.post(self.ended, follow=True))
        self.assertIn("Generated 1 of 2 summaries for Team Alpha for Sprint 1", message)
        self.assertIn("Grace Example", message)
        self.assertIn("Flags weren't updated", message)
        self.compute_flags.assert_not_called()

    def test_retrying_until_every_student_succeeds_recomputes_flags(self):
        self.fail_for(self.grace)
        self.post(self.ended)
        self.generate.side_effect = self.save_summary
        self.client.post(retry_url(self.team.id))
        self.compute_flags.assert_called_once_with(self.team)

    def test_the_sprint_is_remembered_for_the_retry(self):
        self.fail_for(self.grace)
        self.post(self.ended)
        remembered = self.client.session["failed_summaries"][str(self.team.id)]
        self.assertEqual(remembered["sprint_id"], self.ended.id)
        self.assertEqual(remembered["student_ids"], [self.grace.id])

    def test_retrying_writes_only_the_failed_students_for_that_sprint_without_ingesting_again(self):
        self.fail_for(self.grace)
        self.post(self.ended)
        self.ingest.reset_mock()
        self.generate.reset_mock()
        self.generate.side_effect = self.save_summary
        response = self.client.post(retry_url(self.team.id), follow=True)
        self.ingest.assert_not_called()
        self.assertEqual([call.args for call in self.generate.call_args_list], [(self.grace, self.team, self.ended)])
        self.assertIn("Generated summaries for 1 student on Team Alpha for Sprint 1", page_messages(response))
        self.assertNotIn(str(self.team.id), self.client.session.get("failed_summaries", {}))

    def test_the_retry_button_shows_after_a_failed_run(self):
        self.fail_for(self.grace)
        self.post(self.ended)
        self.assertIn("Retry failed summaries", team_article(self.overview(), "Team Alpha"))


class SprintPagesTests(SprintGenerationFixture):
    def setUp(self):
        super().setUp()
        Requirement.objects.create(project=self.project, sprint=self.ended, number=1, text="Users can log in")
        Requirement.objects.create(project=self.project, sprint=self.running, number=1, text="Users can pay")
        # Summaries are created when the running sprint's was written, on Nov 2.
        self.summary = Summary.objects.create(team=self.team, user=self.ada, sprint=self.running)
        self.done = Summary.objects.create(team=self.team, user=self.grace, sprint=self.ended)
        self.claim = Claim.objects.create(
            summary=self.summary, text="Added payments.", category="feature", significance="major",
            artifact_ids=[], requirement_ids=[1], status=Claim.Status.VERIFIED,
        )
        from summaries.models import ValidationRun

        ValidationRun.objects.create(summary=self.summary, claims_total=1, claims_first_pass=1, claims_dropped=0, retries=0)
        ValidationRun.objects.create(summary=self.done, claims_total=0, claims_first_pass=0, claims_dropped=0, retries=0)

    def test_the_team_page_names_each_summarys_sprint_and_dates(self):
        content = self.client.get(team_url(self.team.id)).content.decode()
        self.assertIn("Sprint 2 (Oct 19 – Nov 8)", content)
        self.assertIn("Sprint 1 (Oct 5 – Oct 18)", content)
        self.assertNotIn("Week of None", content)

    def test_a_summary_written_before_its_sprint_ended_is_labelled_in_progress(self):
        content = self.client.get(team_url(self.team.id)).content.decode()
        links = re.findall(r"<a[^>]*summaries/%d/[^>]*>.*?</a>" % self.summary.id, content, re.S)
        self.assertTrue(any("in progress" in link for link in links), links)

    def test_the_summary_page_shows_the_sprint_and_its_covered_through_date(self):
        page = self.client.get(reverse("dashboard:summary", args=[self.summary.id]))
        self.assertContains(page, "Sprint 2")
        self.assertContains(page, "Oct 19, 2026")
        self.assertContains(page, "Nov 8, 2026")
        self.assertContains(page, "still in progress")
        self.assertContains(page, "up to Nov 2")
        self.assertNotContains(page, "None")

    def test_the_summary_page_links_to_the_students_other_sprints(self):
        Summary.objects.create(team=self.team, user=self.ada, sprint=self.ended)
        page = self.client.get(reverse("dashboard:summary", args=[self.summary.id]))
        self.assertContains(page, "Sprint 1")

    def test_requirement_text_comes_from_the_summarys_sprint(self):
        page = self.client.get(reverse("dashboard:summary", args=[self.summary.id]))
        self.assertContains(page, "Users can pay")
        self.assertNotContains(page, "Users can log in")

    def test_the_overview_shows_the_latest_sprint_instead_of_no_summaries(self):
        article = team_article(self.overview(), "Team Alpha")
        self.assertIn("Sprint 2", article)
        self.assertNotIn("No summaries yet", article)
        # The overview shows Sprint 1, which has ended without Ada's summary: as on the team
        # page, that's not generated rather than in progress.
        self.assertIn("Not generated", article)
        self.assertNotIn("In progress", article)
