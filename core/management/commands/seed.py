"""`python manage.py seed`: creates demo and local-development data (T-5, #16).

Safe to run repeatedly. Rows are matched on their natural keys and updated in place,
so a second run creates no duplicates and keeps anything already ingested or edited.

Passwords come from the SEED_PASSWORD environment variable. Nothing is written
unless it is set to a real value.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership

PASSWORD_VARIABLE = "SEED_PASSWORD"
PLACEHOLDER_PASSWORDS = {"", "replace-me"}

COURSE_NAME = "CS 4501, Fall 2026"
PROJECT_NAME = "Project 1"

# The group's real repo. Its three students are the group's GitHub logins, which all
# group members have agreed to use.
LIVE_REPO = "brooke-brown/llmsproject2026"
LIVE_TEAM = "Team Live"
LIVE_STUDENTS = ["todbukh", "brooke-brown", "akshitatiwari132"]

# Invented people and repos for the second team. The sample-org owner doesn't exist on
# GitHub, so "Generate summaries" can't pull real data for this team.
FAKE_TEAM = "Team Sample"
FAKE_REPO = "sample-org/team-sample-app"
FAKE_STUDENTS = ["sample-ada", "sample-grace"]

STAFF = [
    ("sample-instructor", Enrollment.Role.INSTRUCTOR),
    ("sample-ta", Enrollment.Role.TA),
]

REQUIREMENTS = [
    "Users can create an account and log in.",
    "Users can view and edit their profile.",
]

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo data: a course, staff, a project with requirements, and two teams."

    def handle(self, *args, **options):
        password = self.read_password()
        with transaction.atomic():
            self.seed(password)
        self.stdout.write("Seeded the demo course.")

    def read_password(self):
        password = os.environ.get(PASSWORD_VARIABLE, "").strip()
        if password in PLACEHOLDER_PASSWORDS:
            raise CommandError(
                f"Set {PASSWORD_VARIABLE} in the environment to a real password "
                "before seeding (see .env.example)."
            )
        return password

    def seed(self, password):
        course, _ = Course.objects.get_or_create(name=COURSE_NAME)

        for login, role in STAFF:
            user = self.make_user(login, password)
            Enrollment.objects.update_or_create(user=user, course=course, defaults={"role": role})

        project, _ = Project.objects.get_or_create(course=course, name=PROJECT_NAME)
        for number, text in enumerate(REQUIREMENTS, start=1):
            # Matched on number only, so requirement text edited in admin survives a rerun.
            Requirement.objects.get_or_create(
                project=project, number=number, defaults={"text": text}
            )

        self.seed_team(project, course, LIVE_TEAM, LIVE_REPO, LIVE_STUDENTS, password)
        self.seed_team(project, course, FAKE_TEAM, FAKE_REPO, FAKE_STUDENTS, password)

    def seed_team(self, project, course, team_name, full_name, logins, password):
        team, _ = Team.objects.get_or_create(project=project, name=team_name)
        Repo.objects.get_or_create(
            team=team,
            full_name=full_name,
            defaults={"url": f"https://github.com/{full_name}"},
        )
        for login in logins:
            # Students have no pages to log in to in the alpha, so they get no password.
            user = self.make_user(login, password=None)
            Enrollment.objects.update_or_create(
                user=user, course=course, defaults={"role": Enrollment.Role.STUDENT}
            )
            TeamMembership.objects.get_or_create(user=user, team=team)

    def make_user(self, login, password):
        """Staff get the SEED_PASSWORD password, set again on every run so changes apply.

        Students get no password (password=None), so they can't log in.
        """
        user, _ = User.objects.get_or_create(
            username=login, defaults={"github_login": login, "email": ""}
        )
        if password is None:
            user.set_unusable_password()
        else:
            user.set_password(password)
        user.save(update_fields=["password"])
        return user
