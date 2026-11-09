"""Removing a student from a team deletes their offline contributions and confirmations on it,
and the confirmation says so first."""

from datetime import date

from django.test import TestCase

from contributions.models import Contribution, Corroboration
from contributions.tests.test_delete_warnings import form_tag
from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user, team_url
from dashboard.tests.test_team_setup import membership_remove_url

Role = Enrollment.Role


def contribute(team, student):
    return Contribution.objects.create(
        team=team,
        student=student,
        date=date(2026, 9, 2),
        category="meeting",
        description="Ran the sprint planning meeting",
    )


class RemovalFixture(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name="Course 1")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.team = Team.objects.create(project=self.project, name="Team A")
        instructor = make_user("sample-instructor")
        enroll(instructor, self.course, Role.INSTRUCTOR)
        self.client.force_login(instructor)

        self.student = make_user("sample-student-a")
        self.teammate = make_user("sample-student-b")
        for user in (self.student, self.teammate):
            enroll(user, self.course, Role.STUDENT)
        self.membership = TeamMembership.objects.create(user=self.student, team=self.team)
        TeamMembership.objects.create(user=self.teammate, team=self.team)

        # The same two students on a team in a later project, which removal must not touch.
        later_project = Project.objects.create(course=self.course, name="Project 2")
        self.later_team = Team.objects.create(project=later_project, name="Team B")
        TeamMembership.objects.create(user=self.student, team=self.later_team)
        TeamMembership.objects.create(user=self.teammate, team=self.later_team)

    def remove(self):
        return self.client.post(membership_remove_url(self.membership.id))

    def confirmation(self):
        content = self.client.get(team_url(self.team.id)).content.decode()
        return form_tag(content, membership_remove_url(self.membership.id))


class RemovalDeletesTests(RemovalFixture):
    def test_their_contributions_on_the_team_are_deleted(self):
        contribute(self.team, self.student)
        self.remove()
        self.assertFalse(Contribution.objects.exists())

    def test_confirmations_of_their_contributions_are_deleted(self):
        Corroboration.objects.create(
            contribution=contribute(self.team, self.student), teammate=self.teammate
        )
        self.remove()
        self.assertFalse(Corroboration.objects.exists())

    def test_confirmations_they_gave_on_the_team_are_deleted(self):
        Corroboration.objects.create(
            contribution=contribute(self.team, self.teammate), teammate=self.student
        )
        self.remove()
        self.assertFalse(Corroboration.objects.exists())

    def test_a_teammates_contribution_stays(self):
        contribution = contribute(self.team, self.teammate)
        self.remove()
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_their_contributions_on_another_team_stay(self):
        contribution = contribute(self.later_team, self.student)
        self.remove()
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_confirmations_they_gave_on_another_team_stay(self):
        confirmation = Corroboration.objects.create(
            contribution=contribute(self.later_team, self.teammate), teammate=self.student
        )
        self.remove()
        self.assertTrue(Corroboration.objects.filter(pk=confirmation.pk).exists())


class RemovalConfirmationTests(RemovalFixture):
    def test_counts_their_contributions(self):
        contribute(self.team, self.student)
        contribute(self.team, self.student)
        self.assertIn("2 offline contributions", self.confirmation())

    def test_counts_the_confirmations_they_gave(self):
        Corroboration.objects.create(
            contribution=contribute(self.team, self.teammate), teammate=self.student
        )
        tag = self.confirmation()
        self.assertIn("1 confirmation of", tag)
        self.assertNotIn("offline contribution", tag)

    def test_joins_both_counts(self):
        contribute(self.team, self.student)
        Corroboration.objects.create(
            contribution=contribute(self.team, self.teammate), teammate=self.student
        )
        tag = self.confirmation()
        self.assertIn("1 offline contribution and 1 confirmation of", tag)
        # The apostrophe sits inside a single-quoted JavaScript string.
        self.assertIn("teammates\\' work", tag)

    def test_ignores_what_they_have_on_another_team(self):
        contribute(self.later_team, self.student)
        Corroboration.objects.create(
            contribution=contribute(self.later_team, self.teammate), teammate=self.student
        )
        tag = self.confirmation()
        self.assertNotIn("offline contribution", tag)
        self.assertNotIn("confirmation of", tag)

    def test_says_nothing_when_there_are_none(self):
        tag = self.confirmation()
        self.assertNotIn("offline contribution", tag)
        self.assertNotIn("confirmation of", tag)
