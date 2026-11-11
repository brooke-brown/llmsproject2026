"""Tests for US-38 (#115): instructors and TAs see how teammates rated each student.

These drive the real team page. "Now" is pinned by patching django.utils.timezone.now. Every
name here is made up: ratings are student education records (FERPA).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.flags import compute_flags
from dashboard.models import Flag
from dashboard.tests.sprint_helpers import give, sprint_for
from dashboard.tests.test_course_overview import enroll, make_user
from peereval import services
from peereval.models import PeerEvaluation, PeerRating
from peereval.questions import QUESTIONS

Role = Enrollment.Role
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=NY)
MIDPOINT_CLOSED = datetime(2026, 10, 30, 17, 0, tzinfo=NY)
FINAL_CLOSES = datetime(2026, 11, 20, 17, 0, tzinfo=NY)
KEYS = [question.key for question in QUESTIONS]


def scores(value=4, **overrides):
    return {key: overrides.get(key, value) for key in KEYS}


def team_url(team_id):
    return reverse("dashboard:team", args=[team_id])


class ResultsFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)
        cls.sam = make_user("sample-sam", "Sam", "Student")
        cls.tara = make_user("sample-tara", "Tara", "Teammate")
        cls.quinn = make_user("sample-quinn", "Quinn", "Second")
        cls.riley = make_user("sample-riley", "Riley", "Third")
        cls.students = (cls.sam, cls.tara, cls.quinn, cls.riley)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Red")
        for user in cls.students:
            enroll(user, cls.course, Role.STUDENT)
            TeamMembership.objects.create(team=cls.team, user=user)
        cls.midpoint = PeerEvaluation.objects.create(
            project=cls.project, name="Midpoint", closes_at=MIDPOINT_CLOSED,
            created_by=cls.instructor,
        )
        cls.final = PeerEvaluation.objects.create(
            project=cls.project, name="Final", closes_at=FINAL_CLOSES, created_by=cls.instructor
        )

    def setUp(self):
        self.client.force_login(self.instructor)
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rate(self, rater, ratee, evaluation=None, comment="", **score_kwargs):
        return PeerRating.objects.create(
            evaluation=evaluation or self.final, rater=rater, ratee=ratee,
            scores=scores(**score_kwargs), comment=comment,
        )

    def page(self, **params):
        response = self.client.get(team_url(self.team.id), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def row(self, user, evaluation=None):
        rows = services.rating_summary(self.team, evaluation or self.final)
        return next(row for row in rows if row.user == user)


class EvaluationMenuTests(ResultsFixture):
    def test_team_page_has_a_peer_evaluation_section_for_the_latest_evaluation(self):
        self.rate(self.tara, self.sam, comment="Final comment about Sam.")
        self.rate(self.tara, self.sam, evaluation=self.midpoint, comment="Midpoint comment.")
        page = self.page()
        self.assertIn("Peer evaluation", page)
        self.assertIn("Final comment about Sam.", page)
        self.assertNotIn("Midpoint comment.", page)

    def test_menu_lists_every_evaluation_newest_first(self):
        page = self.page()
        self.assertIn('name="evaluation"', page)
        self.assertLess(page.index(">Final"), page.index(">Midpoint"))

    def test_menu_switches_to_an_earlier_evaluation(self):
        self.rate(self.tara, self.sam, comment="Final comment about Sam.")
        self.rate(self.tara, self.sam, evaluation=self.midpoint, comment="Midpoint comment.")
        page = self.page(evaluation=self.midpoint.id)
        self.assertIn("Midpoint comment.", page)
        self.assertNotIn("Final comment about Sam.", page)

    def test_menu_keeps_the_chosen_sprint(self):
        sprint = sprint_for(self.project)
        page = self.page(sprint=sprint.id)
        self.assertIn(f'name="sprint" value="{sprint.id}"', page)

    def test_sprint_menu_keeps_the_chosen_evaluation(self):
        sprint_for(self.project)
        page = self.page(evaluation=self.midpoint.id)
        self.assertIn(f'name="evaluation" value="{self.midpoint.id}"', page)

    def test_an_unknown_or_other_projects_evaluation_shows_the_latest(self):
        other_project = Project.objects.create(course=self.course, name="Project Two")
        elsewhere = PeerEvaluation.objects.create(
            project=other_project, name="Elsewhere", closes_at=FINAL_CLOSES,
            created_by=self.instructor,
        )
        for value in (str(elsewhere.id), "999999", "abc"):
            with self.subTest(value):
                response = self.client.get(team_url(self.team.id), {"evaluation": value})
                self.assertEqual(response.context["peer_evaluation"], self.final)

    def test_a_project_without_evaluations_says_so(self):
        PeerEvaluation.objects.all().delete()
        page = self.page()
        self.assertIn("No peer evaluations yet", page)
        self.assertNotIn('name="evaluation"', page)


class AverageTests(ResultsFixture):
    def test_average_for_each_question_to_one_decimal(self):
        self.rate(self.tara, self.sam, communication=4, effort=5)
        self.rate(self.quinn, self.sam, communication=5, effort=5)
        self.rate(self.riley, self.sam, communication=5, effort=2)
        averages = {a.question.key: a.average for a in self.row(self.sam).averages}
        self.assertEqual(averages["communication"], Decimal("4.7"))
        self.assertEqual(averages["effort"], Decimal("4.0"))
        self.assertEqual(averages["quality"], Decimal("4.0"))

    def test_averages_follow_the_question_order(self):
        self.rate(self.tara, self.sam)
        self.assertEqual([a.question.key for a in self.row(self.sam).averages], KEYS)

    def test_half_rounds_up(self):
        self.rate(self.tara, self.sam, communication=3)
        self.rate(self.quinn, self.sam, communication=4)
        averages = {a.question.key: a.average for a in self.row(self.sam).averages}
        self.assertEqual(averages["communication"], Decimal("3.5"))

    def test_a_question_added_after_a_rating_averages_only_the_ratings_that_answer_it(self):
        rating = self.rate(self.tara, self.sam)
        PeerRating.objects.filter(pk=rating.pk).update(
            scores={key: 4 for key in KEYS if key != "quality"}
        )
        averages = {a.question.key: a.average for a in self.row(self.sam).averages}
        self.assertIsNone(averages["quality"])

    def test_page_shows_averages_and_how_many_teammates_rated(self):
        self.rate(self.tara, self.sam, communication=4)
        self.rate(self.quinn, self.sam, communication=5)
        page = self.page()
        self.assertIn("4.5", page)
        self.assertIn("2 of 3 teammates", page)

    def test_counts_teammates_who_rated(self):
        self.rate(self.tara, self.sam)
        row = self.row(self.sam)
        self.assertEqual((row.rater_count, row.teammate_count), (1, 3))

    def test_ratings_of_other_students_do_not_count(self):
        self.rate(self.sam, self.tara, communication=1)
        self.rate(self.tara, self.sam, communication=5)
        averages = {a.question.key: a.average for a in self.row(self.sam).averages}
        self.assertEqual(averages["communication"], Decimal("5.0"))

    def test_ratings_from_another_evaluation_do_not_count(self):
        self.rate(self.tara, self.sam, evaluation=self.midpoint, communication=1)
        self.assertEqual(self.row(self.sam).rater_count, 0)


class ExpandTests(ResultsFixture):
    def test_each_teammates_ratings_by_name_with_scores_and_comment(self):
        self.rate(self.tara, self.sam, comment="Sam kept the board current.", effort=2)
        row = self.row(self.sam)
        [rating] = row.ratings
        self.assertEqual(rating.rater, self.tara)
        self.assertEqual(rating.comment, "Sam kept the board current.")
        self.assertEqual(
            [(question.key, score) for question, score in rating.scores],
            [(key, 2 if key == "effort" else 4) for key in KEYS],
        )

    def test_page_expands_to_each_rating(self):
        self.rate(self.tara, self.sam, comment="Sam kept the board current.")
        self.rate(self.quinn, self.sam, comment="Sam & co. <b>shipped</b>.")
        page = self.page()
        self.assertIn("<details", page)
        self.assertIn("Tara Teammate", page)
        self.assertIn("Quinn Second", page)
        self.assertIn("Sam kept the board current.", page)
        # Comments are escaped.
        self.assertIn("Sam &amp; co. &lt;b&gt;shipped&lt;/b&gt;.", page)

    def test_raters_are_listed_by_name(self):
        self.rate(self.tara, self.sam)
        self.rate(self.quinn, self.sam)
        self.assertEqual([r.rater for r in self.row(self.sam).ratings], [self.quinn, self.tara])


class StillRatingTests(ResultsFixture):
    def rate_everyone(self, rater, evaluation=None):
        for ratee in self.students:
            if ratee != rater:
                self.rate(rater, ratee, evaluation=evaluation)

    def test_counts_how_many_teammates_each_student_has_rated(self):
        self.rate_everyone(self.sam)
        self.rate(self.tara, self.sam)
        self.assertTrue(self.row(self.sam).finished_rating)
        self.assertEqual(self.row(self.tara).rated_count, 1)
        self.assertFalse(self.row(self.tara).finished_rating)

    def test_open_evaluation_names_students_who_havent_finished(self):
        self.rate_everyone(self.sam)
        self.rate(self.tara, self.sam)
        page = self.page()
        self.assertIn("Still rating", page)
        start = page.index("Still rating")
        still_rating = page[start:page.index("</p>", start)]
        self.assertIn("Tara Teammate", still_rating)
        self.assertIn("Quinn Second", still_rating)
        self.assertIn("Riley Third", still_rating)
        self.assertNotIn("Sam Student", still_rating)

    def test_open_evaluation_says_when_everyone_has_finished(self):
        for student in self.students:
            self.rate_everyone(student)
        page = self.page()
        self.assertNotIn("Still rating", page)
        self.assertIn("Every student has rated all their teammates", page)

    def test_closed_evaluation_does_not_list_who_is_still_rating(self):
        page = self.page(evaluation=self.midpoint.id)
        self.assertNotIn("Still rating", page)


class NoRatingsTests(ResultsFixture):
    def test_a_student_nobody_rated_shows_no_ratings_yet(self):
        self.rate(self.tara, self.sam)
        row = self.row(self.quinn)
        self.assertEqual(row.rater_count, 0)
        self.assertTrue(all(a.average is None for a in row.averages))
        page = self.page()
        self.assertEqual(page.count("No ratings yet"), 3)

    def test_no_zeros_for_a_student_nobody_rated(self):
        page = self.page()
        self.assertEqual(page.count("No ratings yet"), 4)
        self.assertNotIn("0.0", page)
        self.assertNotIn("0 of 3 teammates", page)


class SeparateFromEvidenceTests(ResultsFixture):
    def test_averages_are_labelled_as_teammates_opinions(self):
        self.rate(self.tara, self.sam)
        page = self.page()
        self.assertIn("teammates' opinions", page.lower())
        self.assertIn("not verified against GitHub", page)

    def test_peer_section_comes_after_the_summaries_and_share_chart(self):
        sprint = sprint_for(self.project)
        for student in self.students:
            give(student, self.team, "moderate", sprint=sprint)
        page = self.page(sprint=sprint.id)
        self.assertLess(page.index("Summaries by student"), page.index('id="peer-title"'))
        self.assertLess(page.index('id="share-title"'), page.index('id="peer-title"'))

    def test_students_are_in_the_same_order_as_the_summaries(self):
        response = self.client.get(team_url(self.team.id))
        summaries_order = [m.user for m in response.context["memberships"]]
        peer_order = [row.user for row in response.context["peer_results"]]
        self.assertEqual(peer_order, summaries_order)
        self.assertEqual(peer_order, [self.quinn, self.sam, self.tara, self.riley])

    def test_ratings_never_change_shares_pie_or_flags(self):
        sprint = sprint_for(self.project)
        give(self.sam, self.team, "major", "major", sprint=sprint)
        for student in (self.tara, self.quinn, self.riley):
            give(student, self.team, "minor", sprint=sprint)

        def snapshot():
            flags = sorted(
                (f.rule, f.user_id, f.severity, f.reason) for f in compute_flags(self.team)
            )
            response = self.client.get(team_url(self.team.id), {"sprint": sprint.id})
            memberships = response.context["memberships"]
            shares = {m.user_id: (m.contribution_share, m.pie) for m in memberships}
            return flags, shares

        before = snapshot()
        self.assertTrue(before[0], "The fixture should raise a flag to compare.")
        for rater in (self.tara, self.quinn, self.riley):
            self.rate(rater, self.sam, value=1, evaluation=self.midpoint)
            self.rate(rater, self.sam, value=1)
        self.assertEqual(snapshot(), before)


class AccessTests(ResultsFixture):
    def test_instructor_and_ta_see_ratings(self):
        self.rate(self.tara, self.sam, comment="Visible to staff.")
        for user in (self.instructor, self.ta):
            with self.subTest(user.username):
                self.client.force_login(user)
                self.assertIn("Visible to staff.", self.page())

    def test_students_and_outsiders_get_403(self):
        outsider = make_user("sample-outsider")
        other_course = Course.objects.create(name="CS 3240, Fall 2026")
        enroll(outsider, other_course, Role.INSTRUCTOR)
        for user in (*self.students, outsider):
            with self.subTest(user.username):
                self.client.force_login(user)
                for params in ({}, {"evaluation": self.midpoint.id}):
                    response = self.client.get(team_url(self.team.id), params)
                    self.assertEqual(response.status_code, 403)

    def test_anonymous_users_are_sent_to_log_in(self):
        self.client.logout()
        response = self.client.get(team_url(self.team.id))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class StudentsNeverSeeRatingsTests(ResultsFixture):
    """Logs in as each student on the team and visits every page they can reach."""

    def setUp(self):
        super().setUp()
        sprint = sprint_for(self.project)
        for student in self.students:
            give(student, self.team, "minor", sprint=sprint)
        self.sprint = sprint
        # A distinct comment and low scores for every pair, in both evaluations.
        for evaluation in (self.midpoint, self.final):
            for rater in self.students:
                for ratee in self.students:
                    if rater != ratee:
                        self.rate(
                            rater, ratee, evaluation=evaluation, value=2, communication=1,
                            comment=f"SECRET {evaluation.name} {rater.username} on {ratee.username}",
                        )

    def pages_for(self, student):
        self.client.force_login(student)
        urls = [
            reverse("dashboard:home"),
            reverse("dashboard:student_home"),
            team_url(self.team.id),
            reverse("dashboard:summary", args=[student.summaries.get().id]),
        ]
        for evaluation in (self.midpoint, self.final):
            urls.append(reverse("peereval:evaluation", args=[evaluation.id]))
            for teammate in self.students:
                if teammate != student:
                    urls.append(reverse("peereval:rate", args=[evaluation.id, teammate.id]))
        pages = []
        for url in urls:
            response = self.client.get(url, follow=True)
            pages.append((url, response.content.decode()))
        return pages

    def assert_hidden(self, student, own_comments_allowed):
        for url, page in self.pages_for(student):
            with self.subTest(student=student.username, url=url):
                self.assertNotIn("teammates rated", page)
                self.assertNotIn("No ratings yet", page)
                self.assertNotIn('id="peer-title"', page)
                for rating in PeerRating.objects.all():
                    own_open = (
                        own_comments_allowed
                        and rating.rater == student
                        and rating.evaluation == self.final
                    )
                    if not own_open:
                        self.assertNotIn(rating.comment, page)

    def test_no_student_sees_ratings_averages_or_comments_while_open(self):
        # While Final is open, a student's own rating form shows their own answers (US-37);
        # nothing anyone else wrote, nor any average, appears.
        for student in self.students:
            self.assert_hidden(student, own_comments_allowed=True)

    def test_no_student_sees_any_rating_once_every_evaluation_has_closed(self):
        with mock.patch(
            "django.utils.timezone.now", return_value=FINAL_CLOSES + timedelta(days=1)
        ):
            for student in self.students:
                self.assert_hidden(student, own_comments_allowed=False)

    def test_a_flag_dispute_page_shows_no_ratings(self):
        flag = Flag.objects.create(
            team=self.team, user=self.sam, rule="low_share", reason="Low share.",
            severity="flag", sprint=self.sprint,
        )
        self.client.force_login(self.sam)
        url = reverse(
            "dashboard:flag_dispute", args=[self.team.id, flag.rule, self.sprint.id]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for rating in PeerRating.objects.all():
            self.assertNotIn(rating.comment, page)
