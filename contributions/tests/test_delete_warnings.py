"""Deleting a team or project deletes its offline contributions, so the warnings have to say so."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from contributions.models import Contribution, Corroboration
from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user, team_url
from dashboard.tests.test_team_setup import team_delete_url

Role = Enrollment.Role


def form_tag(content, action):
    """The opening tag of the form posting to `action`, which holds its confirmation."""
    start = content.index(f'action="{action}"')
    return content[start : content.index(">", start)]


class DeleteWarningTests(TestCase):
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
            TeamMembership.objects.create(user=user, team=self.team)

    def contribute(self, count=1):
        return [
            Contribution.objects.create(
                team=self.team,
                student=self.student,
                date=date(2026, 9, 2),
                category="meeting",
                description="Ran the sprint planning meeting",
            )
            for _ in range(count)
        ]

    def team_page(self):
        return self.client.get(team_url(self.team.id)).content.decode()

    def setup_page(self):
        url = reverse("dashboard:project_setup", args=[self.project.id])
        return self.client.get(url).content.decode()

    def project_delete_url(self):
        return reverse("dashboard:project_delete", args=[self.project.id])

    def test_team_delete_confirmation_counts_contributions(self):
        self.contribute(2)
        tag = form_tag(self.team_page(), team_delete_url(self.team.id))
        self.assertIn("2 offline contributions", tag)

    def test_team_delete_confirmation_omits_contributions_when_there_are_none(self):
        tag = form_tag(self.team_page(), team_delete_url(self.team.id))
        self.assertNotIn("offline contribution", tag)

    def test_team_delete_note_mentions_contributions(self):
        self.assertIn("offline contributions", self.team_page())

    def test_project_delete_confirmation_counts_contributions(self):
        self.contribute()
        tag = form_tag(self.setup_page(), self.project_delete_url())
        self.assertIn("1 offline contribution", tag)
        self.assertNotIn("1 offline contributions", tag)

    def test_project_delete_confirmation_omits_contributions_when_there_are_none(self):
        tag = form_tag(self.setup_page(), self.project_delete_url())
        self.assertNotIn("offline contribution", tag)

    def test_project_delete_note_mentions_contributions(self):
        self.assertIn("offline contributions", self.setup_page())

    def test_deleting_the_team_deletes_its_contributions_and_corroborations(self):
        contribution = self.contribute()[0]
        Corroboration.objects.create(contribution=contribution, teammate=self.teammate)

        self.client.post(team_delete_url(self.team.id))

        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(Corroboration.objects.exists())

    def test_deleting_the_project_deletes_its_contributions(self):
        self.contribute()

        self.client.post(self.project_delete_url())

        self.assertFalse(Contribution.objects.exists())
