"""Tests for US-36 (#113): open a peer evaluation for a project.

These drive the real pages. "Now" is pinned by patching django.utils.timezone.now. Every name
here is made up: ratings are student education records (FERPA).
"""

from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user
from dashboard.tests.test_generate_summary import page_messages
from peereval.models import PeerEvaluation, PeerRating
from peereval.questions import QUESTIONS

Role = Enrollment.Role
NY = ZoneInfo("America/New_York")
# 9:00pm on Mon Nov 9 in New York is already Nov 10 in UTC.
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=NY)
LATER = NOW + timedelta(days=7)
ALL_FOURS = {question.key: 4 for question in QUESTIONS}


def url(name, *args):
    return reverse(f"peereval:{name}", args=args)


def setup_url(project):
    return reverse("dashboard:project_setup", args=[project.id])


class EvaluationFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)
        cls.student = make_user("sample-student")
        enroll(cls.student, cls.course, Role.STUDENT)
        cls.teammate = make_user("sample-teammate")
        enroll(cls.teammate, cls.course, Role.STUDENT)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Red")
        TeamMembership.objects.create(team=cls.team, user=cls.student)
        TeamMembership.objects.create(team=cls.team, user=cls.teammate)
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.other_instructor = make_user("other-instructor")
        enroll(cls.other_instructor, cls.other_course, Role.INSTRUCTOR)
        cls.other_project = Project.objects.create(course=cls.other_course, name="Other Project")

    def setUp(self):
        self.client.force_login(self.instructor)
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def evaluation(self, name="Midpoint", closes_at=LATER, project=None):
        return PeerEvaluation.objects.create(
            project=project or self.project,
            name=name,
            closes_at=closes_at,
            created_by=self.instructor,
        )

    def closed(self, name="Kickoff"):
        return self.evaluation(name=name, closes_at=NOW - timedelta(days=1))

    def rate(self, evaluation):
        return PeerRating.objects.create(
            evaluation=evaluation, rater=self.student, ratee=self.teammate, scores=ALL_FOURS
        )

    def create(self, name="Midpoint", closes_at="2026-11-20T17:00", project=None, follow=True):
        return self.client.post(
            url("evaluation_create", (project or self.project).id),
            {"name": name, "closes_at": closes_at},
            follow=follow,
        )

    def setup_page(self):
        return self.client.get(setup_url(self.project)).content.decode()


class SectionTests(EvaluationFixture):
    def test_section_lists_each_evaluation_with_its_closing_date_and_status(self):
        self.evaluation(name="Midpoint", closes_at=datetime(2026, 11, 20, 17, 0, tzinfo=NY))
        self.evaluation(name="Kickoff", closes_at=datetime(2026, 10, 2, 9, 30, tzinfo=NY))
        page = self.setup_page()
        self.assertIn("Peer evaluations", page)
        self.assertIn("Midpoint", page)
        self.assertIn("Nov 20, 2026, 5:00 PM", page)
        self.assertIn("Kickoff", page)
        self.assertIn("Oct 2, 2026, 9:30 AM", page)
        self.assertIn('<span class="tag ok">Open</span>', page)
        self.assertIn('<span class="tag none">Closed</span>', page)

    def test_closing_dates_show_in_new_york_time(self):
        # 2:00am UTC on Nov 21 is 9:00pm on Nov 20 in New York.
        self.evaluation(closes_at=datetime(2026, 11, 21, 2, 0, tzinfo=ZoneInfo("UTC")))
        self.assertIn("Nov 20, 2026, 9:00 PM", self.setup_page())

    def test_newest_evaluation_is_listed_first(self):
        self.closed(name="Kickoff")
        self.evaluation(name="Midpoint")
        page = self.setup_page()
        self.assertLess(page.index("Midpoint"), page.index("Kickoff"))

    def test_no_evaluations_says_so(self):
        self.assertIn("No peer evaluations yet.", self.setup_page())

    def test_section_links_to_new_peer_evaluation(self):
        page = self.setup_page()
        self.assertIn("New peer evaluation", page)
        self.assertIn(url("evaluation_create", self.project.id), page)

    def test_section_shows_every_question_with_its_labels(self):
        page = self.setup_page()
        for question in QUESTIONS:
            self.assertIn(question.text, page)
            self.assertIn(question.label_1, page)
            self.assertIn(question.label_5, page)

    def test_section_shows_how_many_ratings_an_evaluation_has(self):
        self.rate(self.evaluation())
        self.assertIn("1 rating", self.setup_page())

    def test_another_projects_evaluations_are_not_listed(self):
        self.evaluation(name="Elsewhere", project=self.other_project)
        self.assertNotIn("Elsewhere", self.setup_page())

    def test_open_evaluation_offers_change_close_and_delete(self):
        evaluation = self.evaluation()
        page = self.setup_page()
        self.assertIn(url("evaluation_change", evaluation.id), page)
        self.assertIn(url("evaluation_close", evaluation.id), page)
        self.assertIn(url("evaluation_delete", evaluation.id), page)

    def test_closed_evaluation_offers_no_change_or_close(self):
        evaluation = self.closed()
        page = self.setup_page()
        self.assertNotIn(url("evaluation_change", evaluation.id), page)
        self.assertNotIn(url("evaluation_close", evaluation.id), page)
        self.assertIn(url("evaluation_delete", evaluation.id), page)

    def test_delete_asks_for_confirmation(self):
        evaluation = self.evaluation()
        page = self.setup_page()
        form_start = page.index(url("evaluation_delete", evaluation.id))
        self.assertIn("return confirm(", page[form_start:form_start + 400])

    def test_project_delete_confirmation_counts_peer_evaluations(self):
        self.evaluation()
        self.closed()
        self.assertIn("2 peer evaluations", self.setup_page())


class CreateTests(EvaluationFixture):
    def test_new_page_asks_for_name_and_closing_time(self):
        page = self.client.get(url("evaluation_create", self.project.id)).content.decode()
        self.assertIn('name="name"', page)
        self.assertIn('name="closes_at"', page)
        self.assertIn('type="datetime-local"', page)
        self.assertIn("New York", page)

    def test_creating_saves_the_evaluation_and_returns_to_the_project(self):
        response = self.create()
        self.assertRedirects(response, setup_url(self.project))
        evaluation = PeerEvaluation.objects.get()
        self.assertEqual(evaluation.name, "Midpoint")
        self.assertEqual(evaluation.project, self.project)
        self.assertEqual(evaluation.created_by, self.instructor)
        self.assertIn("Opened Midpoint", page_messages(response))

    def test_closing_time_is_read_as_new_york_time(self):
        self.create(closes_at="2026-11-20T21:00")
        closes_at = PeerEvaluation.objects.get().closes_at
        self.assertIsNotNone(closes_at.tzinfo)
        self.assertEqual(closes_at, datetime(2026, 11, 20, 21, 0, tzinfo=NY))

    def test_name_is_trimmed(self):
        self.create(name="  Midpoint  ")
        self.assertEqual(PeerEvaluation.objects.get().name, "Midpoint")

    def test_new_evaluation_is_open(self):
        self.create()
        self.assertIn('<span class="tag ok">Open</span>', self.setup_page())

    def test_blank_name_is_rejected(self):
        response = self.create(name="   ")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter a name", response.content.decode())
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_service_rejects_a_blank_name_too(self):
        from peereval import services

        with self.assertRaisesMessage(services.EvaluationError, "Enter a name."):
            services.create_evaluation(self.project, self.instructor, "  ", LATER)

    def test_name_used_in_this_project_is_rejected_regardless_of_case(self):
        self.closed(name="Midpoint")
        response = self.create(name="MIDPOINT")
        self.assertIn(
            "This project already has a peer evaluation with this name.",
            response.content.decode(),
        )
        self.assertEqual(PeerEvaluation.objects.count(), 1)

    def test_name_used_in_another_project_is_allowed(self):
        self.evaluation(name="Midpoint", project=self.other_project)
        self.create(name="Midpoint")
        self.assertEqual(PeerEvaluation.objects.filter(project=self.project).count(), 1)

    def test_closing_time_in_the_past_is_rejected(self):
        response = self.create(closes_at="2026-11-09T20:59")
        self.assertIn("closing time must be in the future", response.content.decode())
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_closing_time_of_now_is_rejected(self):
        self.create(closes_at="2026-11-09T21:00")
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_missing_closing_time_is_rejected(self):
        response = self.create(closes_at="")
        self.assertIn("Enter a closing date and time", response.content.decode())
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_rejected_form_keeps_what_was_typed(self):
        response = self.create(name="Final review", closes_at="2026-11-01T17:00")
        page = response.content.decode()
        self.assertIn('value="Final review"', page)
        self.assertIn('value="2026-11-01T17:00"', page)

    def test_second_open_evaluation_is_refused_naming_the_open_one(self):
        self.evaluation(name="Midpoint")
        response = self.create(name="Final")
        self.assertIn("Midpoint is still open", response.content.decode())
        self.assertFalse(PeerEvaluation.objects.filter(name="Final").exists())

    def test_new_evaluation_is_allowed_once_the_open_one_has_closed(self):
        self.closed(name="Kickoff")
        self.create(name="Midpoint")
        self.assertTrue(PeerEvaluation.objects.filter(name="Midpoint").exists())

    def test_another_projects_open_evaluation_does_not_block(self):
        self.evaluation(name="Elsewhere", project=self.other_project)
        self.create(name="Midpoint")
        self.assertTrue(PeerEvaluation.objects.filter(project=self.project).exists())


class ChangeTests(EvaluationFixture):
    def change(self, evaluation, closes_at):
        return self.client.post(
            url("evaluation_change", evaluation.id), {"closes_at": closes_at}, follow=True
        )

    def test_open_evaluation_closing_time_can_change(self):
        evaluation = self.evaluation()
        response = self.change(evaluation, "2026-11-25T12:00")
        self.assertRedirects(response, setup_url(self.project))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, datetime(2026, 11, 25, 12, 0, tzinfo=NY))
        self.assertIn("Midpoint now closes Nov 25, 2026, 12:00 PM", page_messages(response))

    def test_closing_time_cannot_move_into_the_past(self):
        evaluation = self.evaluation()
        response = self.change(evaluation, "2026-11-01T12:00")
        self.assertIn("closing time must be in the future", page_messages(response))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, LATER)

    def test_blank_closing_time_is_rejected(self):
        evaluation = self.evaluation()
        response = self.change(evaluation, "")
        self.assertIn("Enter a closing date and time", page_messages(response))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, LATER)

    def test_change_form_starts_with_the_current_closing_time_in_new_york(self):
        self.evaluation(closes_at=datetime(2026, 11, 21, 2, 0, tzinfo=ZoneInfo("UTC")))
        self.assertIn('value="2026-11-20T21:00"', self.setup_page())

    def test_closed_evaluation_cannot_change(self):
        evaluation = self.closed()
        before = evaluation.closes_at
        response = self.change(evaluation, "2026-11-25T12:00")
        self.assertIn("Kickoff has closed", page_messages(response))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, before)

    def test_close_now_closes_it(self):
        evaluation = self.evaluation()
        response = self.client.post(url("evaluation_close", evaluation.id), follow=True)
        self.assertRedirects(response, setup_url(self.project))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, NOW)
        self.assertIn("Closed Midpoint", page_messages(response))
        self.assertIn('<span class="tag none">Closed</span>', self.setup_page())

    def test_closing_now_lets_a_new_one_open(self):
        evaluation = self.evaluation(name="Midpoint")
        self.client.post(url("evaluation_close", evaluation.id))
        self.create(name="Final")
        self.assertTrue(PeerEvaluation.objects.filter(name="Final").exists())

    def test_closing_a_closed_evaluation_is_refused(self):
        evaluation = self.closed()
        before = evaluation.closes_at
        response = self.client.post(url("evaluation_close", evaluation.id), follow=True)
        self.assertIn("Kickoff has closed", page_messages(response))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, before)


class DeleteTests(EvaluationFixture):
    def test_evaluation_with_no_ratings_is_deleted(self):
        evaluation = self.evaluation()
        response = self.client.post(url("evaluation_delete", evaluation.id), follow=True)
        self.assertRedirects(response, setup_url(self.project))
        self.assertFalse(PeerEvaluation.objects.exists())
        self.assertIn("Deleted Midpoint", page_messages(response))

    def test_closed_evaluation_with_no_ratings_is_deleted(self):
        evaluation = self.closed()
        self.client.post(url("evaluation_delete", evaluation.id))
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_evaluation_with_ratings_is_not_deleted(self):
        evaluation = self.evaluation()
        self.rate(evaluation)
        response = self.client.post(url("evaluation_delete", evaluation.id), follow=True)
        self.assertIn("Midpoint has ratings, so it can't be deleted", page_messages(response))
        self.assertTrue(PeerEvaluation.objects.filter(pk=evaluation.pk).exists())
        self.assertEqual(PeerRating.objects.count(), 1)


class AccessTests(EvaluationFixture):
    def post_endpoints(self, evaluation):
        return [
            (url("evaluation_create", self.project.id), {"name": "New", "closes_at": "2026-11-20T17:00"}),
            (url("evaluation_change", evaluation.id), {"closes_at": "2026-11-25T12:00"}),
            (url("evaluation_close", evaluation.id), {}),
            (url("evaluation_delete", evaluation.id), {}),
        ]

    def test_only_the_courses_instructors_can_make_changes(self):
        evaluation = self.closed()
        for user in (self.ta, self.student, self.other_instructor, make_user("unenrolled")):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.client.get(url("evaluation_create", self.project.id)).status_code, 403
                )
                for endpoint, data in self.post_endpoints(evaluation):
                    self.assertEqual(self.client.post(endpoint, data).status_code, 403, endpoint)
        evaluation.refresh_from_db()
        self.assertEqual(PeerEvaluation.objects.count(), 1)
        self.assertEqual(evaluation.closes_at, NOW - timedelta(days=1))

    def test_logged_out_users_are_sent_to_log_in(self):
        evaluation = self.evaluation()
        self.client.logout()
        for endpoint, data in self.post_endpoints(evaluation):
            response = self.client.post(endpoint, data)
            self.assertEqual(response.status_code, 302, endpoint)
            self.assertIn(reverse("login"), response["Location"])

    def test_missing_evaluation_or_project_is_404(self):
        self.assertEqual(self.client.get(url("evaluation_create", 9999)).status_code, 404)
        for name in ("evaluation_change", "evaluation_close", "evaluation_delete"):
            self.assertEqual(self.client.post(url(name, 9999)).status_code, 404, name)

    def test_changes_are_post_only(self):
        evaluation = self.evaluation()
        for name in ("evaluation_change", "evaluation_close", "evaluation_delete"):
            self.assertEqual(self.client.get(url(name, evaluation.id)).status_code, 405, name)

    def test_changes_need_a_csrf_token(self):
        evaluation = self.evaluation()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        for endpoint, data in self.post_endpoints(evaluation):
            self.assertEqual(client.post(endpoint, data).status_code, 403, endpoint)
        self.assertEqual(PeerEvaluation.objects.count(), 1)

    def test_ta_does_not_see_the_section(self):
        # TAs can't open "Edit project" at all, so they never see the controls.
        self.client.force_login(self.ta)
        self.assertEqual(self.client.get(setup_url(self.project)).status_code, 403)
