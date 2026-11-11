"""Shared setup for the sprint tests (T-19, #119): a tiny project with its own data.

Dates are in 2026. Oct 23 is a Friday, so Oct 23-29 is a Friday-Thursday sprint.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Course, Project, Repo, Requirement, Sprint, Team, TeamMembership
from ingest.models import Artifact
from summaries.schemas import Claim

NEW_YORK = ZoneInfo("America/New_York")
REPO = "acme/app"


def at(month, day, hour=12, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=NEW_YORK)


def make_claim(*artifact_ids, requirement_ids=()):
    return Claim(
        text="Added the login form.",
        category="feature",
        artifact_ids=list(artifact_ids),
        requirement_ids=list(requirement_ids),
        significance="minor",
    )


class SprintTestCase(TestCase):
    def setUp(self):
        course = Course.objects.create(name="CS 4501, Fall 2026")
        self.project = Project.objects.create(course=course, name="Project 1")
        self.team = Team.objects.create(project=self.project, name="Team A")
        self.repo = Repo.objects.create(team=self.team, url=f"https://github.com/{REPO}", full_name=REPO)
        users = get_user_model().objects
        self.student = users.create_user("ada", github_login="ada-dev", password="x")
        self.other = users.create_user("bo", github_login="bo-dev", password="x")
        for user in (self.student, self.other):
            TeamMembership.objects.create(user=user, team=self.team)
        # Friday Oct 23 to Thursday Oct 29.
        self.sprint = Sprint.objects.create(
            project=self.project, name="Sprint 1", start_date=date(2026, 10, 23), end_date=date(2026, 10, 29)
        )

    def add_commit(self, suffix, when, author="ada-dev"):
        artifact_id = f"{REPO}:commit:{suffix}"
        Artifact.objects.create(
            repo=self.repo,
            artifact_id=artifact_id,
            type="commit",
            url=f"https://github.com/{REPO}/commit/{suffix}",
            timestamp=when,
            title=f"Commit {suffix}",
            author_login=author,
        )
        return artifact_id

    def add_requirement(self, sprint, number, text):
        return Requirement.objects.create(project=self.project, sprint=sprint, number=number, text=text)
