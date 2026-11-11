"""Tests for US-37 (#114): a student rates each teammate in an open peer evaluation.

These drive the real pages. "Now" is pinned by patching django.utils.timezone.now. Every name
here is made up: ratings are student education records (FERPA).

URLs are reversed per test rather than at module level, so each criterion passes or fails on
its own instead of the whole module failing on a single NoReverseMatch.
"""

import re
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user
from dashboard.tests.test_generate_summary import page_messages
from peereval import services
from peereval.models import PeerEvaluation, PeerRating
from peereval.questions import QUESTIONS

Role = Enrollment.Role
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=NY)
CLOSES = datetime(2026, 11, 20, 17, 0, tzinfo=NY)
ALL_FOURS = {question.key: 4 for question in QUESTIONS}
CONFIDENTIAL_NOTE = "Your ratings are seen only by your instructor and TAs, never by your teammates."


def evaluation_url(evaluation_id):
    return reverse("peereval:evaluation", args=[evaluation_id])


def rate_url(evaluation_id, ratee_id):
    return reverse("peereval:rate", args=[evaluation_id, ratee_id])


def answers(scores=None, comment=""):
    data = dict(scores if scores is not None else ALL_FOURS)
    data["comment"] = comment
    return data


def is_checked(page, key, score):
    """Whether the radio for `score` on question `key` is checked."""
    for tag in re.findall(r"<input[^>]*>", page):
        if f'name="{key}"' in tag and f'value="{score}"' in tag:
            return "checked" in tag
    raise AssertionError(f"No radio for {key}={score}")


class RatingFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)
        cls.student = make_user("sample-student", "Sam", "Student")
        cls.teammate = make_user("sample-teammate", "Tara", "Teammate")
        cls.second_teammate = make_user("sample-second", "Quinn", "Second")
        cls.other_team_student = make_user("sample-other", "Olive", "Other")
        cls.loner = make_user("sample-loner")
        for user in (
            cls.student, cls.teammate, cls.second_teammate, cls.other_team_student, cls.loner
        ):
            enroll(user, cls.course, Role.STUDENT)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Red")
        for user in (cls.student, cls.teammate, cls.second_teammate):
            TeamMembership.objects.create(team=cls.team, user=user)
        cls.other_team = Team.objects.create(project=cls.project, name="Team Blue")
        TeamMembership.objects.create(team=cls.other_team, user=cls.other_team_student)
        cls.other_project = Project.objects.create(course=cls.course, name="Project Two")
        cls.other_project_team = Team.objects.create(project=cls.other_project, name="Team Green")
        cls.other_project_student = make_user("sample-elsewhere")
        enroll(cls.other_project_student, cls.course, Role.STUDENT)
        TeamMembership.objects.create(team=cls.other_project_team, user=cls.other_project_student)
        cls.evaluation = PeerEvaluation.objects.create(
            project=cls.project, name="Midpoint", closes_at=CLOSES, created_by=cls.instructor
        )

    def setUp(self):
        self.client.force_login(self.student)
        self.now = mock.patch("django.utils.timezone.now", return_value=NOW)
        self.now.start()
        self.addCleanup(mock.patch.stopall)

    def move_to(self, moment):
        self.now.stop()
        self.now = mock.patch("django.utils.timezone.now", return_value=moment)
        self.now.start()

    def after_close(self):
        self.move_to(CLOSES + timedelta(minutes=1))

    def rating(self, rater=None, ratee=None, scores=None, comment=""):
        return PeerRating.objects.create(
            evaluation=self.evaluation,
            rater=rater or self.student,
            ratee=ratee or self.teammate,
            scores=scores or ALL_FOURS,
            comment=comment,
        )

    def submit(self, ratee=None, data=None, follow=True):
        return self.client.post(
            rate_url(self.evaluation.id, (ratee or self.teammate).id),
            data if data is not None else answers(),
            follow=follow,
        )

    def evaluation_page(self):
        return self.client.get(evaluation_url(self.evaluation.id)).content.decode()

    def rate_page(self, ratee=None):
        return self.client.get(rate_url(self.evaluation.id, (ratee or self.teammate).id))


class StudentHomeLinkTests(RatingFixture):
    def home(self):
        return self.client.get(reverse("dashboard:student_home")).content.decode()

    def test_open_evaluation_is_linked_with_its_closing_date(self):
        page = self.home()
        self.assertIn(evaluation_url(self.evaluation.id), page)
        self.assertIn("Midpoint", page)
        self.assertIn("Nov 20, 2026, 5:00 PM", page)

    def test_closed_evaluation_is_not_linked(self):
        self.after_close()
        self.assertNotIn(evaluation_url(self.evaluation.id), self.home())

    def test_other_projects_evaluation_is_not_linked(self):
        self.client.force_login(self.other_project_student)
        self.assertNotIn(evaluation_url(self.evaluation.id), self.home())


class EvaluationPageTests(RatingFixture):
    def test_lists_each_teammate_but_not_me(self):
        page = self.evaluation_page()
        self.assertIn("Tara Teammate", page)
        self.assertIn("Quinn Second", page)
        self.assertNotIn("Sam Student", page)
        self.assertNotIn(rate_url(self.evaluation.id, self.student.id), page)

    def test_does_not_list_students_on_other_teams(self):
        self.assertNotIn("Olive Other", self.evaluation_page())

    def test_links_each_teammate_to_their_rating_form(self):
        page = self.evaluation_page()
        self.assertIn(rate_url(self.evaluation.id, self.teammate.id), page)
        self.assertIn(rate_url(self.evaluation.id, self.second_teammate.id), page)

    def test_shows_whether_each_teammate_is_rated(self):
        self.rating(ratee=self.teammate)
        page = self.evaluation_page()
        self.assertIn("Rated", page)
        self.assertEqual(page.count("Not rated yet"), 1)
        # Teammates are listed by first name, so Quinn (not rated) comes before Tara (rated).
        self.assertLess(page.index("Not rated yet"), page.index("Tara Teammate"))

    def test_not_rated_when_nobody_is_rated(self):
        page = self.evaluation_page()
        self.assertEqual(page.count("Not rated yet"), 2)

    def test_shows_closing_date(self):
        self.assertIn("Nov 20, 2026, 5:00 PM", self.evaluation_page())

    def test_says_ratings_are_confidential(self):
        self.assertIn(CONFIDENTIAL_NOTE, self.evaluation_page())


class RatingFormTests(RatingFixture):
    def test_shows_every_question_with_its_labels(self):
        page = self.rate_page().content.decode()
        for question in QUESTIONS:
            self.assertIn(question.text, page)
            self.assertIn(question.label_1, page)
            self.assertIn(question.label_5, page)
            for score in range(1, 6):
                self.assertFalse(is_checked(page, question.key, score))

    def test_names_the_teammate_and_says_ratings_are_confidential(self):
        page = self.rate_page().content.decode()
        self.assertIn("Tara Teammate", page)
        self.assertIn(CONFIDENTIAL_NOTE, page)

    def test_has_an_optional_comment_of_up_to_1000_characters(self):
        page = self.rate_page().content.decode()
        textarea = re.search(r"<textarea[^>]*>", page).group(0)
        self.assertIn('name="comment"', textarea)
        self.assertIn('maxlength="1000"', textarea)
        self.assertNotIn("required", textarea)

    def test_form_is_csrf_protected(self):
        self.assertIn("csrfmiddlewaretoken", self.rate_page().content.decode())

    def test_submitting_saves_the_rating(self):
        scores = {question.key: index + 1 for index, question in enumerate(QUESTIONS)}
        self.submit(data=answers(scores, comment="Kept the board up to date."))
        rating = PeerRating.objects.get()
        self.assertEqual(rating.evaluation, self.evaluation)
        self.assertEqual(rating.rater, self.student)
        self.assertEqual(rating.ratee, self.teammate)
        self.assertEqual(rating.scores, scores)
        self.assertEqual(rating.comment, "Kept the board up to date.")

    def test_comment_is_optional(self):
        self.submit()
        self.assertEqual(PeerRating.objects.get().comment, "")

    def test_after_submitting_im_back_on_the_evaluation_with_the_teammate_rated(self):
        response = self.submit(follow=False)
        # Not fetched by assertRedirects, which would use up the message.
        self.assertRedirects(
            response, evaluation_url(self.evaluation.id), fetch_redirect_response=False
        )
        response = self.client.get(evaluation_url(self.evaluation.id))
        page = response.content.decode()
        self.assertEqual(page.count("Not rated yet"), 1)
        self.assertIn("Saved your rating of Tara Teammate.", page_messages(response))

    def test_missing_answer_is_rejected_naming_the_question(self):
        scores = dict(ALL_FOURS)
        del scores["effort"]
        response = self.submit(data=answers(scores, comment="Half done."))
        self.assertFalse(PeerRating.objects.exists())
        page = response.content.decode()
        self.assertIn("Answer “Puts effort into the project”.", page)

    def test_missing_answer_keeps_my_other_answers(self):
        scores = {question.key: 2 for question in QUESTIONS}
        del scores["effort"]
        page = self.submit(data=answers(scores, comment="Half done.")).content.decode()
        for key in scores:
            self.assertTrue(is_checked(page, key, 2))
        self.assertIn("Half done.", page)

    def test_every_missing_answer_is_named(self):
        page = self.submit(data=answers({})).content.decode()
        for question in QUESTIONS:
            self.assertIn(f"Answer “{question.text}”.", page)

    def test_score_outside_1_to_5_is_rejected(self):
        self.submit(data=answers({**ALL_FOURS, "effort": 6}))
        self.assertFalse(PeerRating.objects.exists())

    def test_comment_over_1000_characters_is_rejected(self):
        self.submit(data=answers(comment="x" * 1001))
        self.assertFalse(PeerRating.objects.exists())

    def test_comment_of_1000_characters_is_accepted(self):
        self.submit(data=answers(comment="x" * 1000))
        self.assertEqual(len(PeerRating.objects.get().comment), 1000)


class ChangeRatingTests(RatingFixture):
    def test_form_opens_with_my_earlier_answers(self):
        self.rating(scores={**ALL_FOURS, "effort": 2}, comment="Earlier comment.")
        page = self.rate_page().content.decode()
        self.assertTrue(is_checked(page, "effort", 2))
        self.assertTrue(is_checked(page, "communication", 4))
        self.assertFalse(is_checked(page, "effort", 4))
        self.assertIn("Earlier comment.", page)

    def test_resubmitting_changes_the_same_rating(self):
        self.rating(comment="Earlier comment.")
        self.submit(data=answers({**ALL_FOURS, "on_time": 1}, comment="Changed."))
        rating = PeerRating.objects.get()
        self.assertEqual(rating.scores["on_time"], 1)
        self.assertEqual(rating.comment, "Changed.")

    def test_rejected_change_keeps_the_earlier_rating(self):
        self.rating()
        scores = dict(ALL_FOURS)
        del scores["quality"]
        self.submit(data=answers({**scores, "effort": 1}))
        self.assertEqual(PeerRating.objects.get().scores, ALL_FOURS)


class ConfidentialityTests(RatingFixture):
    def setUp(self):
        super().setUp()
        # Ratings about me, and between my teammates, that I must never see.
        self.rating(
            rater=self.teammate, ratee=self.student,
            scores={key: 1 for key in ALL_FOURS}, comment="Secret about Sam.",
        )
        self.rating(
            rater=self.second_teammate, ratee=self.teammate, comment="Secret about Tara.",
        )

    def pages(self):
        return [
            self.client.get(reverse("dashboard:student_home")).content.decode(),
            self.evaluation_page(),
            self.rate_page(self.teammate).content.decode(),
            self.rate_page(self.second_teammate).content.decode(),
        ]

    def test_no_page_shows_ratings_given_to_me_or_by_teammates(self):
        for page in self.pages():
            self.assertNotIn("Secret about Sam.", page)
            self.assertNotIn("Secret about Tara.", page)

    def test_a_teammates_rating_of_me_does_not_fill_my_form(self):
        page = self.rate_page(self.teammate).content.decode()
        for key in ALL_FOURS:
            self.assertFalse(is_checked(page, key, 1))

    def test_a_teammate_rating_me_does_not_show_them_as_rated(self):
        self.assertEqual(self.evaluation_page().count("Not rated yet"), 2)


class AfterCloseTests(RatingFixture):
    def test_submitting_after_the_closing_time_is_refused(self):
        self.after_close()
        response = self.submit()
        self.assertFalse(PeerRating.objects.exists())
        self.assertIn("Midpoint has closed", page_messages(response))

    def test_page_left_open_until_after_close_is_refused_on_submit(self):
        self.assertEqual(self.rate_page().status_code, 200)
        self.after_close()
        self.submit()
        self.assertFalse(PeerRating.objects.exists())

    def test_closing_between_the_views_check_and_the_save_is_refused(self):
        refusal = services.EvaluationError(services.closed_message(self.evaluation))
        with mock.patch("peereval.services.save_rating", side_effect=refusal):
            response = self.submit()
        self.assertRedirects(response, evaluation_url(self.evaluation.id))
        self.assertIn("Midpoint has closed", page_messages(response))

    def test_closing_is_checked_at_the_exact_closing_time(self):
        self.move_to(CLOSES)
        self.submit()
        self.assertFalse(PeerRating.objects.exists())

    def test_changing_after_close_is_refused(self):
        self.rating(comment="Before close.")
        self.after_close()
        self.submit(data=answers({**ALL_FOURS, "effort": 1}, comment="After close."))
        rating = PeerRating.objects.get()
        self.assertEqual(rating.scores, ALL_FOURS)
        self.assertEqual(rating.comment, "Before close.")

    def test_rating_form_is_not_shown_after_close(self):
        self.rating(comment="Before close.")
        self.after_close()
        response = self.rate_page()
        self.assertRedirects(response, evaluation_url(self.evaluation.id))
        response = self.client.get(rate_url(self.evaluation.id, self.teammate.id), follow=True)
        self.assertIn("Midpoint has closed", page_messages(response))
        self.assertNotIn("Before close.", response.content.decode())

    def test_closed_evaluation_shows_who_i_rated_but_not_my_answers(self):
        self.rating(comment="Before close.")
        self.after_close()
        page = self.evaluation_page()
        self.assertIn("Tara Teammate", page)
        self.assertIn("Rated", page)
        self.assertIn("Not rated yet", page)
        self.assertNotIn("Before close.", page)
        self.assertNotIn(rate_url(self.evaluation.id, self.teammate.id), page)
        self.assertIn("closed", page)


class SaveRatingServiceTests(RatingFixture):
    """save_rating re-reads the evaluation under its lock, for a change between the view's
    check and the save."""

    def test_refuses_an_evaluation_closed_since_it_was_read(self):
        stale = PeerEvaluation.objects.get(pk=self.evaluation.pk)
        PeerEvaluation.objects.filter(pk=stale.pk).update(closes_at=NOW)
        with self.assertRaisesMessage(services.EvaluationError, "Midpoint has closed"):
            services.save_rating(stale, self.student, self.teammate, ALL_FOURS, "")
        self.assertFalse(PeerRating.objects.exists())

    def test_an_evaluation_deleted_since_it_was_read_is_404(self):
        stale = PeerEvaluation.objects.get(pk=self.evaluation.pk)
        PeerEvaluation.objects.filter(pk=stale.pk).delete()
        with self.assertRaises(Http404):
            services.save_rating(stale, self.student, self.teammate, ALL_FOURS, "")

    def test_refuses_a_ratee_no_longer_on_the_team(self):
        TeamMembership.objects.filter(user=self.teammate).delete()
        with self.assertRaises(ValidationError):
            services.save_rating(self.evaluation, self.student, self.teammate, ALL_FOURS, "")
        self.assertFalse(PeerRating.objects.exists())


class AccessTests(RatingFixture):
    def assert_status(self, status, user=None):
        if user is not None:
            self.client.force_login(user)
        self.assertEqual(self.client.get(evaluation_url(self.evaluation.id)).status_code, status)
        self.assertEqual(self.rate_page().status_code, status)
        self.assertEqual(self.submit(follow=False).status_code, status)
        self.assertFalse(PeerRating.objects.exists())

    def test_instructor_is_refused(self):
        self.assert_status(403, self.instructor)

    def test_ta_is_refused(self):
        self.assert_status(403, self.ta)

    def test_student_not_on_a_team_is_refused(self):
        self.assert_status(403, self.loner)

    def test_student_on_another_projects_team_is_refused(self):
        self.assert_status(403, self.other_project_student)

    def test_unenrolled_user_is_refused(self):
        self.assert_status(403, make_user("sample-unenrolled"))

    def test_team_member_no_longer_a_course_student_is_refused(self):
        Enrollment.objects.filter(user=self.student).delete()
        self.assert_status(403)

    def test_anonymous_user_is_sent_to_log_in(self):
        self.client.logout()
        response = self.client.get(evaluation_url(self.evaluation.id))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_rating_a_student_on_another_team_is_refused(self):
        self.assertEqual(self.rate_page(self.other_team_student).status_code, 403)
        self.assertEqual(self.submit(self.other_team_student, follow=False).status_code, 403)
        self.assertFalse(PeerRating.objects.exists())

    def test_rating_myself_is_refused(self):
        self.assertEqual(self.rate_page(self.student).status_code, 403)
        self.assertEqual(self.submit(self.student, follow=False).status_code, 403)
        self.assertFalse(PeerRating.objects.exists())

    def test_missing_evaluation_is_404(self):
        self.assertEqual(self.client.get(evaluation_url(999999)).status_code, 404)
        self.assertEqual(
            self.client.get(rate_url(999999, self.teammate.id)).status_code, 404
        )
        self.assertEqual(
            self.client.post(rate_url(999999, self.teammate.id), answers()).status_code, 404
        )

    def test_missing_teammate_is_404(self):
        self.assertEqual(self.client.get(rate_url(self.evaluation.id, 999999)).status_code, 404)
        self.assertEqual(
            self.client.post(rate_url(self.evaluation.id, 999999), answers()).status_code, 404
        )

    def test_submitting_needs_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(rate_url(self.evaluation.id, self.teammate.id), answers())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PeerRating.objects.exists())

    def test_evaluation_page_is_read_only(self):
        response = self.client.post(evaluation_url(self.evaluation.id))
        self.assertEqual(response.status_code, 405)
