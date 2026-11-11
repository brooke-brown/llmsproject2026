"""Tests for the #154 review items on US-36: clock-change times, and hiding controls the
service would refuse. Every name here is made up."""

from django.template.loader import get_template

from dashboard.tests.test_generate_summary import page_messages
from peereval.forms import CLOCK_CHANGE_ERROR
from peereval.models import DUPLICATE_NAME_ERROR, PeerEvaluation

from .test_open_evaluation import EvaluationFixture, LATER, url

FALL_BACK = "2026-11-01T01:30"  # happens twice in New York
SPRING_FORWARD = "2027-03-14T02:30"  # never happens in New York


class ClockChangeTests(EvaluationFixture):
    def change(self, evaluation, closes_at):
        return self.client.post(
            url("evaluation_change", evaluation.id), {"closes_at": closes_at}, follow=True
        )

    def test_creating_with_a_repeated_time_gives_a_plain_message(self):
        page = self.create(closes_at=FALL_BACK).content.decode()
        self.assertIn(CLOCK_CHANGE_ERROR, page)
        self.assertNotIn("couldn’t be interpreted", page)
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_creating_with_a_skipped_time_gives_a_plain_message(self):
        page = self.create(closes_at=SPRING_FORWARD).content.decode()
        self.assertIn(CLOCK_CHANGE_ERROR, page)
        self.assertNotIn("couldn’t be interpreted", page)
        self.assertFalse(PeerEvaluation.objects.exists())

    def test_changing_to_a_repeated_time_gives_a_plain_message(self):
        evaluation = self.evaluation()
        messages = page_messages(self.change(evaluation, FALL_BACK))
        self.assertIn(CLOCK_CHANGE_ERROR, messages)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, LATER)

    def test_changing_to_a_skipped_time_gives_a_plain_message(self):
        evaluation = self.evaluation()
        messages = page_messages(self.change(evaluation, SPRING_FORWARD))
        self.assertIn(CLOCK_CHANGE_ERROR, messages)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.closes_at, LATER)

    def test_other_bad_input_keeps_its_own_message(self):
        page = self.create(closes_at="not a date").content.decode()
        self.assertIn("Enter a closing date and time", page)
        self.assertNotIn(CLOCK_CHANGE_ERROR, page)


class HiddenControlTests(EvaluationFixture):
    def test_evaluation_with_ratings_offers_no_delete(self):
        evaluation = self.evaluation()
        self.rate(evaluation)
        self.assertNotIn(url("evaluation_delete", evaluation.id), self.setup_page())

    def test_new_button_is_replaced_by_a_note_while_one_is_open(self):
        self.evaluation(name="Midpoint")
        page = self.setup_page()
        self.assertNotIn(url("evaluation_create", self.project.id), page)
        self.assertIn("Midpoint is open. Close it before opening another.", page)

    def test_new_button_returns_once_the_open_one_has_closed(self):
        self.closed()
        page = self.setup_page()
        self.assertIn(url("evaluation_create", self.project.id), page)
        self.assertNotIn("Close it before opening another", page)

    def test_another_projects_open_evaluation_does_not_hide_the_button(self):
        self.evaluation(project=self.other_project)
        self.assertIn(url("evaluation_create", self.project.id), self.setup_page())


class SharedWordingTests(EvaluationFixture):
    def test_service_and_model_refuse_a_duplicate_name_the_same_way(self):
        constraint = PeerEvaluation._meta.constraints[0]
        self.assertEqual(constraint.violation_error_message, DUPLICATE_NAME_ERROR)
        self.closed(name="Midpoint")
        self.assertIn(DUPLICATE_NAME_ERROR, self.create(name="midpoint").content.decode())


class StyleTests(EvaluationFixture):
    def test_narrow_forms_style_datetime_inputs_like_text_inputs(self):
        source = get_template("dashboard/base.html").template.source
        self.assertIn(
            '.narrow :is(input[type="text"], input[type="datetime-local"])', source
        )
