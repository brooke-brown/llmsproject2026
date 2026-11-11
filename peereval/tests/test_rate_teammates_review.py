"""Tests for the #155 review items on US-37. Every name here is made up."""

from unittest import mock

from django.core.exceptions import ValidationError

from dashboard import services as dashboard_services
from dashboard.tests.test_generate_summary import page_messages
from peereval import services
from peereval.forms import COMMENT_MAX_LENGTH, RatingForm
from peereval.models import PeerEvaluation, PeerRating

from .test_rate_teammates import ALL_FOURS, CLOSES, RatingFixture, answers, rate_url


class CommentLineBreakTests(RatingFixture):
    def test_a_line_break_counts_as_one_character(self):
        # The browser counts this as exactly the limit: each \r\n is one character to it.
        comment = "a" * (COMMENT_MAX_LENGTH - 2) + "\r\n" + "b"
        form = RatingForm(answers(comment=comment))
        self.assertTrue(form.is_valid(), form.errors)

    def test_line_breaks_are_saved_as_newlines(self):
        self.client.post(
            rate_url(self.evaluation.id, self.teammate.id), answers(comment="one\r\ntwo")
        )
        rating = PeerRating.objects.get(rater=self.student, ratee=self.teammate)
        self.assertEqual(rating.comment, "one\ntwo")

    def test_a_comment_over_the_limit_is_still_refused(self):
        comment = "a" * (COMMENT_MAX_LENGTH - 1) + "\r\n" + "b"
        form = RatingForm(answers(comment=comment))
        self.assertFalse(form.is_valid())
        self.assertIn("1,000 characters or fewer", str(form.errors["comment"]))


class SaveValidationErrorTests(RatingFixture):
    def test_service_turns_a_model_error_into_an_evaluation_error(self):
        with mock.patch.object(
            PeerRating, "full_clean", side_effect=ValidationError("They aren't on the same team.")
        ):
            with self.assertRaisesMessage(services.EvaluationError, "aren't on the same team"):
                services.save_rating(self.evaluation, self.student, self.teammate, ALL_FOURS, "")
        self.assertFalse(PeerRating.objects.exists())

    def test_ratee_leaving_mid_request_shows_a_message_not_a_500(self):
        real_get_teammate = services.get_teammate

        def get_then_leave(team, user, ratee_id):
            ratee = real_get_teammate(team, user, ratee_id)
            self.team.memberships.filter(user=ratee).delete()
            return ratee

        with mock.patch.object(services, "get_teammate", side_effect=get_then_leave):
            response = self.client.post(
                rate_url(self.evaluation.id, self.teammate.id), answers(), follow=True
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(page_messages(response))
        self.assertFalse(PeerRating.objects.exists())


class OpenDefinitionTests(RatingFixture):
    def test_queryset_and_instance_agree_before_and_after_closing(self):
        self.assertTrue(self.evaluation.is_open_now())
        self.assertIn(self.evaluation, PeerEvaluation.objects.open())
        self.move_to(CLOSES)
        self.assertFalse(self.evaluation.is_open_now())
        self.assertNotIn(self.evaluation, PeerEvaluation.objects.open())


class QueryTests(RatingFixture):
    def test_evaluation_page_does_not_reload_the_project(self):
        evaluation, team = services.get_evaluation_for_student(self.student, self.evaluation.id)
        with self.assertNumQueries(0):
            self.assertEqual(team.project.course, self.course)
        self.assertEqual(team, self.team)


class SharedTeammateListTests(RatingFixture):
    def test_student_home_and_evaluation_list_teammates_the_same_way(self):
        (course,) = dashboard_services.student_home_courses(self.student)
        (home,) = [m.teammates for m in course.memberships if m.team_id == self.team.id]
        listed = services.teammates_with_status(self.evaluation, self.team, self.student)
        self.assertEqual(home, listed)
        # Quinn before Tara: by first name.
        self.assertEqual(
            [user.github_login for user in listed], ["sample-second", "sample-teammate"]
        )
