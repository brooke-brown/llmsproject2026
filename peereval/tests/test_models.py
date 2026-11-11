"""Tests for T-17 (#105): the peereval app, its questions and models.

The models and questions are looked up per test rather than imported at module level, so each
criterion passes or fails on its own instead of the whole module failing on a single ImportError.

All students here are made up: ratings and comments are student education records (FERPA).
"""

import importlib
from datetime import UTC, datetime
from unittest import mock

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.urls import reverse

from core.models import Course, Project, Team, TeamMembership, User

NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
CLOSES_AT = datetime(2026, 9, 11, 23, 59, tzinfo=UTC)

QUESTIONS = {
    "communication": (
        "Communicates with the team",
        "rarely",
        "consistently and clearly",
    ),
    "on_time": ("Gets their work done on time", "rarely", "always"),
    "effort": ("Puts effort into the project", "very little", "a great deal"),
    "quality": ("Does work the team can build on", "rarely", "consistently"),
    "cooperation": ("Works well with teammates", "rarely", "consistently"),
}
ALL_FOURS = {key: 4 for key in QUESTIONS}


def evaluation_model():
    return apps.get_model("peereval", "PeerEvaluation")


def rating_model():
    return apps.get_model("peereval", "PeerRating")


def questions():
    return importlib.import_module("peereval.questions").QUESTIONS


def make_user(login):
    return User.objects.create_user(
        username=login, password="not-a-real-password", github_login=login
    )


def freeze(moment=NOW):
    return mock.patch("django.utils.timezone.now", return_value=moment)


class PeerEvalTestCase(TestCase):
    """Made-up students: A and B share a team, C is on another team in the same project,
    D is on a team in a different project and E isn't on any team."""

    def setUp(self):
        frozen = freeze()
        frozen.start()
        self.addCleanup(frozen.stop)

        self.course = Course.objects.create(name="Course 1")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.other_project = Project.objects.create(course=self.course, name="Project 2")
        self.team = Team.objects.create(project=self.project, name="Team A")
        self.other_team = Team.objects.create(project=self.project, name="Team B")
        self.other_project_team = Team.objects.create(project=self.other_project, name="Team C")

        self.instructor = make_user("instructor-1")
        self.student = make_user("student-a")
        self.teammate = make_user("student-b")
        self.other_team_student = make_user("student-c")
        self.other_project_student = make_user("student-d")
        self.teamless_student = make_user("student-e")

        TeamMembership.objects.create(user=self.student, team=self.team)
        TeamMembership.objects.create(user=self.teammate, team=self.team)
        TeamMembership.objects.create(user=self.other_team_student, team=self.other_team)
        TeamMembership.objects.create(
            user=self.other_project_student, team=self.other_project_team
        )

        self.evaluation = self.create_evaluation()

    def create_evaluation(self, **overrides):
        values = {
            "project": self.project,
            "name": "Midpoint",
            "closes_at": CLOSES_AT,
            "created_by": self.instructor,
        }
        return evaluation_model().objects.create(**{**values, **overrides})

    def build_rating(self, **overrides):
        """A valid, unsaved rating of student B by student A."""
        values = {
            "evaluation": self.evaluation,
            "rater": self.student,
            "ratee": self.teammate,
            "scores": dict(ALL_FOURS),
        }
        return rating_model()(**{**values, **overrides})

    def create_rating(self, **overrides):
        rating = self.build_rating(**overrides)
        rating.save()
        return rating


class AppSetupTests(TestCase):
    def test_app_is_installed(self):
        self.assertTrue(apps.is_installed("peereval"))

    def test_migrations_are_in_the_app(self):
        loader = MigrationLoader(connection)
        self.assertTrue([name for name in loader.disk_migrations if name[0] == "peereval"])

    def test_migrations_match_the_models(self):
        try:
            call_command("makemigrations", "peereval", "--check", "--dry-run", verbosity=0)
        except SystemExit:
            self.fail("The peereval models have changes with no migration.")


class QuestionTests(TestCase):
    def test_keys_are_the_beta_set_in_order(self):
        self.assertEqual([question.key for question in questions()], list(QUESTIONS))

    def test_each_question_has_its_text_and_labels_for_1_and_5(self):
        for question in questions():
            with self.subTest(question.key):
                text, low, high = QUESTIONS[question.key]
                self.assertEqual(question.text, text)
                self.assertEqual(question.label_1, low)
                self.assertEqual(question.label_5, high)

    def test_questions_cannot_be_changed_at_runtime(self):
        self.assertIsInstance(questions(), tuple)
        with self.assertRaises(AttributeError):
            questions()[0].key = "renamed"


class PeerEvaluationTests(PeerEvalTestCase):
    def test_stores_every_field(self):
        self.evaluation.refresh_from_db()
        self.assertEqual(self.evaluation.project, self.project)
        self.assertEqual(self.evaluation.name, "Midpoint")
        self.assertEqual(self.evaluation.closes_at, CLOSES_AT)
        self.assertEqual(self.evaluation.created_by, self.instructor)
        self.assertEqual(self.evaluation.created_at, NOW)

    def test_project_and_created_by_are_foreign_keys(self):
        fields = evaluation_model()._meta
        self.assertIsInstance(fields.get_field("project"), models.ForeignKey)
        self.assertIs(fields.get_field("project").related_model, Project)
        self.assertIsInstance(fields.get_field("created_by"), models.ForeignKey)
        self.assertIs(fields.get_field("created_by").related_model, User)

    def test_closes_at_is_a_date_and_time(self):
        self.assertIsInstance(
            evaluation_model()._meta.get_field("closes_at"), models.DateTimeField
        )

    def test_name_is_required(self):
        evaluation = evaluation_model()(
            project=self.project, name="", closes_at=CLOSES_AT, created_by=self.instructor
        )
        with self.assertRaises(ValidationError) as caught:
            evaluation.full_clean()
        self.assertIn("name", caught.exception.message_dict)

    def test_database_refuses_a_name_used_in_the_project_in_any_case(self):
        for name in ("Midpoint", "midpoint", "MIDPOINT"):
            with self.subTest(name=name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_evaluation(name=name)

    def test_full_clean_refuses_a_name_used_in_the_project_in_any_case(self):
        evaluation = evaluation_model()(
            project=self.project,
            name="midPoint",
            closes_at=CLOSES_AT,
            created_by=self.instructor,
        )
        with self.assertRaises(ValidationError):
            evaluation.full_clean()

    def test_another_project_can_reuse_a_name(self):
        self.create_evaluation(project=self.other_project, name="Midpoint")

    def test_a_project_can_have_several_evaluations(self):
        self.create_evaluation(name="Final")
        self.assertEqual(evaluation_model().objects.filter(project=self.project).count(), 2)


class PeerRatingFieldTests(PeerEvalTestCase):
    def test_stores_every_field(self):
        rating = self.create_rating(comment="Kept the board up to date")
        rating.refresh_from_db()
        self.assertEqual(rating.evaluation, self.evaluation)
        self.assertEqual(rating.rater, self.student)
        self.assertEqual(rating.ratee, self.teammate)
        self.assertEqual(rating.scores, ALL_FOURS)
        self.assertEqual(rating.comment, "Kept the board up to date")

    def test_foreign_keys(self):
        fields = rating_model()._meta
        expected = {"evaluation": evaluation_model(), "rater": User, "ratee": User}
        for name, related in expected.items():
            with self.subTest(name):
                self.assertIsInstance(fields.get_field(name), models.ForeignKey)
                self.assertIs(fields.get_field(name).related_model, related)

    def test_scores_is_a_json_field(self):
        self.assertIsInstance(rating_model()._meta.get_field("scores"), models.JSONField)

    def test_comment_is_optional(self):
        rating = self.build_rating()
        rating.full_clean()
        rating.save()
        rating.refresh_from_db()
        self.assertEqual(rating.comment, "")

    def test_submitted_at_is_set_once_and_updated_at_on_every_save(self):
        later = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        rating = self.create_rating()
        self.assertEqual(rating.submitted_at, NOW)
        self.assertEqual(rating.updated_at, NOW)

        with freeze(later):
            rating.comment = "Changed my mind"
            rating.save()
        rating.refresh_from_db()

        self.assertEqual(rating.submitted_at, NOW)
        self.assertEqual(rating.updated_at, later)

    def test_database_refuses_a_second_rating_of_the_same_ratee(self):
        self.create_rating()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_rating(scores={key: 2 for key in QUESTIONS})

    def test_a_rater_can_rate_the_same_ratee_in_another_evaluation(self):
        self.create_rating()
        self.create_rating(evaluation=self.create_evaluation(name="Final"))

    def test_two_raters_can_rate_the_same_ratee(self):
        third = make_user("student-f")
        TeamMembership.objects.create(user=third, team=self.team)
        self.create_rating()
        self.create_rating(rater=third)
        self.assertEqual(rating_model().objects.filter(ratee=self.teammate).count(), 2)

    def test_str_does_not_show_scores_or_the_comment(self):
        rating = self.create_rating(
            scores={**ALL_FOURS, "effort": 1}, comment="Secret remark"
        )
        text = str(rating)
        self.assertNotIn("Secret remark", text)
        self.assertNotIn("effort", text)
        self.assertNotIn("{", text)
        self.assertNotIn("scores", text.lower())


class PeerRatingCleanTests(PeerEvalTestCase):
    def assert_refused(self, rating):
        with self.assertRaises(ValidationError):
            rating.full_clean()

    def test_valid_rating_passes(self):
        self.build_rating().full_clean()

    def test_every_score_from_1_to_5_is_accepted(self):
        for score in range(1, 6):
            with self.subTest(score=score):
                self.build_rating(scores={key: score for key in QUESTIONS}).full_clean()

    def test_rating_yourself_is_refused(self):
        self.assert_refused(self.build_rating(ratee=self.student))

    def test_rater_and_ratee_not_on_the_same_team_are_refused(self):
        for ratee in (self.other_team_student, self.other_project_student, self.teamless_student):
            with self.subTest(ratee=ratee.github_login):
                self.assert_refused(self.build_rating(ratee=ratee))

    def test_rater_not_on_a_team_is_refused(self):
        self.assert_refused(self.build_rating(rater=self.teamless_student))

    def test_teammates_in_another_project_only_are_refused(self):
        """A and B share a team in Project 2 too, but this evaluation is for Project 1."""
        other_evaluation = self.create_evaluation(project=self.other_project, name="Midpoint")
        TeamMembership.objects.filter(user=self.teammate).delete()
        TeamMembership.objects.create(user=self.student, team=self.other_project_team)
        TeamMembership.objects.create(user=self.teammate, team=self.other_project_team)

        self.build_rating(evaluation=other_evaluation).full_clean()
        self.assert_refused(self.build_rating())

    def test_a_missing_question_key_is_refused(self):
        for key in QUESTIONS:
            with self.subTest(missing=key):
                scores = {other: 4 for other in QUESTIONS if other != key}
                self.assert_refused(self.build_rating(scores=scores))

    def test_empty_scores_are_refused(self):
        self.assert_refused(self.build_rating(scores={}))

    def test_an_unknown_question_key_is_refused(self):
        self.assert_refused(self.build_rating(scores={**ALL_FOURS, "punctuality": 4}))

    def test_a_score_outside_1_to_5_is_refused(self):
        for score in (0, 6, -1, 10):
            with self.subTest(score=score):
                self.assert_refused(self.build_rating(scores={**ALL_FOURS, "effort": score}))

    def test_a_score_that_is_not_an_integer_is_refused(self):
        for score in (3.5, 4.0, "4", None, True, [4]):
            with self.subTest(score=score):
                self.assert_refused(self.build_rating(scores={**ALL_FOURS, "effort": score}))

    def test_scores_that_are_not_an_object_are_refused(self):
        for scores in ([4, 4, 4, 4, 4], "4", 4, None):
            with self.subTest(scores=scores):
                self.assert_refused(self.build_rating(scores=scores))

    def test_missing_values_are_reported_rather_than_crashing(self):
        """A form calls clean even after a required field failed, so it can't assume them."""
        for missing in ("evaluation", "rater", "ratee"):
            with self.subTest(missing=missing):
                rating = self.build_rating(**{missing: None})
                with self.assertRaises(ValidationError):
                    rating.full_clean()


class NotUsedElsewhereTests(TestCase):
    def test_summaries_and_flags_never_import_peereval(self):
        """Ratings aren't passed to the LLM and never count toward flags."""
        for module in ("summaries.services", "dashboard.flags"):
            with self.subTest(module):
                source = importlib.import_module(module).__file__
                with open(source) as handle:
                    self.assertNotIn("peereval", handle.read())


class AdminTests(PeerEvalTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(
            User.objects.create_superuser(
                username="admin-user",
                email="admin@example.com",
                password="not-a-real-password",
                github_login="admin-user",
            )
        )

    def test_both_models_are_registered(self):
        self.assertTrue(admin.site.is_registered(evaluation_model()))
        self.assertTrue(admin.site.is_registered(rating_model()))

    def test_both_lists_open(self):
        self.create_rating()
        for name in ("peerevaluation", "peerrating"):
            with self.subTest(name):
                response = self.client.get(reverse(f"admin:peereval_{name}_changelist"))
                self.assertEqual(response.status_code, 200)

    def test_submitting_a_blank_form_shows_errors(self):
        for name in ("peerevaluation", "peerrating"):
            with self.subTest(name):
                response = self.client.post(reverse(f"admin:peereval_{name}_add"), {})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
