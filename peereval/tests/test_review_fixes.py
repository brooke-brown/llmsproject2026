"""Review fixes for T-17 (#153): ratings go with the team membership that made them valid,
delete confirmations say so, and adding a question later doesn't break earlier ratings.

All students here are made up: ratings and comments are student education records (FERPA).
"""

from datetime import UTC, datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from contributions.tests.test_delete_warnings import form_tag
from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user, team_url
from dashboard.tests.test_team_setup import membership_remove_url, team_delete_url
from peereval import questions
from peereval.models import PeerEvaluation, PeerRating

Role = Enrollment.Role
CLOSES_AT = datetime(2026, 9, 11, 23, 59, tzinfo=UTC)
ALL_FOURS = {key: 4 for key in questions.QUESTION_KEYS}


class RatingFixture(TestCase):
    """A, B and C share Team A in Project 1; D is on Team B. A and B also share a team in
    Project 2, whose ratings nothing here may touch."""

    def setUp(self):
        self.course = Course.objects.create(name="Course 1")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.team = Team.objects.create(project=self.project, name="Team A")
        self.other_team = Team.objects.create(project=self.project, name="Team B")
        self.instructor = make_user("sample-instructor")
        enroll(self.instructor, self.course, Role.INSTRUCTOR)
        self.client.force_login(self.instructor)

        self.student = make_user("sample-student-a")
        self.teammate = make_user("sample-student-b")
        self.third = make_user("sample-student-c")
        self.other_team_student = make_user("sample-student-d")
        for user in (self.student, self.teammate, self.third, self.other_team_student):
            enroll(user, self.course, Role.STUDENT)
        self.membership = TeamMembership.objects.create(user=self.student, team=self.team)
        TeamMembership.objects.create(user=self.teammate, team=self.team)
        TeamMembership.objects.create(user=self.third, team=self.team)
        TeamMembership.objects.create(user=self.other_team_student, team=self.other_team)

        self.later_project = Project.objects.create(course=self.course, name="Project 2")
        self.later_team = Team.objects.create(project=self.later_project, name="Team C")
        TeamMembership.objects.create(user=self.student, team=self.later_team)
        TeamMembership.objects.create(user=self.teammate, team=self.later_team)

        self.evaluation = self.make_evaluation(self.project)
        self.later_evaluation = self.make_evaluation(self.later_project)

    def make_evaluation(self, project):
        return PeerEvaluation.objects.create(
            project=project, name="Midpoint", closes_at=CLOSES_AT, created_by=self.instructor
        )

    def rate(self, rater, ratee, evaluation=None):
        return PeerRating.objects.create(
            evaluation=evaluation or self.evaluation,
            rater=rater,
            ratee=ratee,
            scores=dict(ALL_FOURS),
        )

    def rate_everyone(self):
        """Every pair on Team A rates each other, and A and B rate each other in Project 2."""
        team_a = (self.student, self.teammate, self.third)
        for rater in team_a:
            for ratee in team_a:
                if rater != ratee:
                    self.rate(rater, ratee)
        self.later_ratings = [
            self.rate(self.student, self.teammate, self.later_evaluation),
            self.rate(self.teammate, self.student, self.later_evaluation),
        ]

    def pairs(self, evaluation=None):
        return set(
            PeerRating.objects.filter(evaluation=evaluation or self.evaluation).values_list(
                "rater__github_login", "ratee__github_login"
            )
        )


class RatingsFollowTeamMembershipTests(RatingFixture):
    def test_removing_a_student_deletes_ratings_they_gave_and_got_on_the_team(self):
        self.rate_everyone()

        self.client.post(membership_remove_url(self.membership.id))

        self.assertEqual(
            self.pairs(),
            {("sample-student-b", "sample-student-c"), ("sample-student-c", "sample-student-b")},
        )

    def test_removing_a_student_keeps_their_ratings_in_other_projects(self):
        self.rate_everyone()

        self.client.post(membership_remove_url(self.membership.id))

        self.assertEqual(len(self.pairs(self.later_evaluation)), 2)

    def test_deleting_a_membership_in_the_admin_deletes_its_ratings(self):
        """The admin calls no service, so the cleanup can't live in one."""
        self.rate_everyone()

        self.membership.delete()

        self.assertFalse(PeerRating.objects.filter(evaluation=self.evaluation, rater=self.student))
        self.assertFalse(PeerRating.objects.filter(evaluation=self.evaluation, ratee=self.student))

    def test_ratings_left_behind_still_pass_clean(self):
        self.rate_everyone()

        self.membership.delete()

        for rating in PeerRating.objects.all():
            rating.full_clean()

    def test_deleting_the_team_deletes_its_ratings(self):
        self.rate_everyone()

        self.client.post(team_delete_url(self.team.id))

        self.assertEqual(self.pairs(), set())
        self.assertEqual(len(self.pairs(self.later_evaluation)), 2)

    def test_a_student_on_two_teams_in_the_project_keeps_ratings_with_the_other_team(self):
        """Admin doesn't stop it, so the cleanup checks for any shared team, not this one."""
        TeamMembership.objects.create(user=self.student, team=self.other_team)
        self.rate(self.student, self.other_team_student)
        self.rate(self.student, self.teammate)

        self.membership.delete()

        self.assertEqual(self.pairs(), {("sample-student-a", "sample-student-d")})


class DeleteWarningTests(RatingFixture):
    def setup_page(self):
        url = reverse("dashboard:project_setup", args=[self.project.id])
        return self.client.get(url).content.decode()

    def team_page(self):
        return self.client.get(team_url(self.team.id)).content.decode()

    def project_delete_tag(self):
        url = reverse("dashboard:project_delete", args=[self.project.id])
        return form_tag(self.setup_page(), url)

    def test_project_delete_confirmation_counts_peer_ratings(self):
        self.rate_everyone()
        self.assertIn("6 peer ratings", self.project_delete_tag())

    def test_project_delete_confirmation_uses_the_singular(self):
        self.rate(self.student, self.teammate)
        tag = self.project_delete_tag()
        self.assertIn("1 peer rating", tag)
        self.assertNotIn("1 peer ratings", tag)

    def test_project_delete_confirmation_omits_ratings_when_there_are_none(self):
        self.assertNotIn("peer rating", self.project_delete_tag())

    def test_project_delete_note_mentions_peer_evaluations(self):
        self.assertIn("peer evaluations and their ratings", self.setup_page())

    def test_deleting_the_project_deletes_its_ratings(self):
        self.rate_everyone()
        self.client.post(reverse("dashboard:project_delete", args=[self.project.id]))
        self.assertFalse(PeerRating.objects.filter(evaluation__project=self.project.id))

    def test_team_delete_confirmation_counts_the_teams_ratings(self):
        self.rate_everyone()  # Project 2's two ratings between A and B aren't Team A's.
        tag = form_tag(self.team_page(), team_delete_url(self.team.id))
        self.assertIn("6 peer ratings", tag)

    def test_team_delete_confirmation_omits_ratings_when_there_are_none(self):
        tag = form_tag(self.team_page(), team_delete_url(self.team.id))
        self.assertNotIn("peer rating", tag)

    def test_team_delete_note_mentions_peer_ratings(self):
        self.assertIn("peer ratings", self.team_page())

    def test_removal_confirmation_counts_ratings_given_and_received_on_the_team(self):
        self.rate_everyone()
        tag = form_tag(self.team_page(), membership_remove_url(self.membership.id))
        self.assertIn("4 peer ratings they gave or received on this team", tag)

    def test_removal_confirmation_omits_ratings_when_there_are_none(self):
        self.rate(self.teammate, self.third)
        tag = form_tag(self.team_page(), membership_remove_url(self.membership.id))
        self.assertNotIn("peer rating", tag)


class AddingAQuestionLaterTests(RatingFixture):
    def add_question(self):
        added = questions.QUESTION_KEYS | {"initiative"}
        return mock.patch("peereval.models.QUESTION_KEYS", added)

    def test_an_earlier_rating_still_passes_clean(self):
        rating = self.rate(self.student, self.teammate)
        with self.add_question():
            rating.refresh_from_db()
            rating.full_clean()

    def test_an_earlier_rating_can_answer_the_new_question(self):
        rating = self.rate(self.student, self.teammate)
        with self.add_question():
            rating.scores = {**ALL_FOURS, "initiative": 3}
            rating.full_clean()

    def test_an_earlier_rating_still_needs_the_questions_it_answered(self):
        rating = self.rate(self.student, self.teammate)
        with self.add_question():
            rating.scores = {key: 4 for key in sorted(ALL_FOURS)[1:]}
            with self.assertRaisesMessage(Exception, "Missing a score"):
                rating.full_clean()

    def test_an_earlier_rating_still_refuses_an_unknown_question(self):
        rating = self.rate(self.student, self.teammate)
        rating.scores = {**ALL_FOURS, "punctuality": 4}
        with self.assertRaisesMessage(Exception, "Unknown question"):
            rating.full_clean()

    def test_a_new_rating_needs_the_new_question(self):
        with self.add_question():
            rating = PeerRating(
                evaluation=self.evaluation,
                rater=self.student,
                ratee=self.teammate,
                scores=dict(ALL_FOURS),
            )
            with self.assertRaisesMessage(Exception, "Missing a score for: initiative"):
                rating.full_clean()
