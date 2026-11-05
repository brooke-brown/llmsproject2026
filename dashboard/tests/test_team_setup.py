"""Tests for US-27 (#91): set up teams, students and repos from my account.

These drive the real pages, so they check what an instructor sees. GitHub is mocked on
PyGithub's own class, so these don't depend on which module makes the call and nothing
touches the network. The LLM is mocked in the end-to-end test.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

import os
from datetime import date, datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from github import (
    BadCredentialsException,
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership
from dashboard.models import Flag
from ingest.models import Artifact
from summaries import schemas
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_generate_summary import generate_url, page_messages, team_article

Role = Enrollment.Role
User = get_user_model()

TOKEN = "sample-token-value"
TOKEN_ACCOUNT = "strive-bot"
WEEK = date(2026, 10, 26)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=ZoneInfo("America/New_York"))
NEW_REPO_URL = "https://github.com/acme/app"


def team_create_url(project_id):
    return reverse("dashboard:team_create", args=[project_id])


def team_rename_url(team_id):
    return reverse("dashboard:team_rename", args=[team_id])


def team_delete_url(team_id):
    return reverse("dashboard:team_delete", args=[team_id])


def student_add_url(team_id):
    return reverse("dashboard:student_add", args=[team_id])


def membership_edit_url(membership_id):
    return reverse("dashboard:membership_edit", args=[membership_id])


def membership_remove_url(membership_id):
    return reverse("dashboard:membership_remove", args=[membership_id])


def repo_add_url(team_id):
    return reverse("dashboard:repo_add", args=[team_id])


def repo_change_url(repo_id):
    return reverse("dashboard:repo_change", args=[repo_id])


def repo_remove_url(repo_id):
    return reverse("dashboard:repo_remove", args=[repo_id])


def github_error(status, message="GitHub's message"):
    return GithubException(status, {"message": message}, {})


class TeamSetupFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        # Deliberately not is_staff: the dashboard goes by Enrollment.
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.later_project = Project.objects.create(course=cls.course, name="Project Two")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.empty_team = Team.objects.create(project=cls.project, name="Team Beta")
        cls.later_team = Team.objects.create(project=cls.later_project, name="Team Later")

        cls.grace = make_user("sample-grace", "Grace", "Example")
        enroll(cls.grace, cls.course, Role.STUDENT)
        cls.membership = TeamMembership.objects.create(user=cls.grace, team=cls.team)
        cls.repo = Repo.objects.create(
            team=cls.team, url="https://github.com/acme/existing", full_name="acme/existing"
        )

        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.other_project = Project.objects.create(course=cls.other_course, name="Other Project")
        cls.other_team = Team.objects.create(project=cls.other_project, name="Other Team")

    def setUp(self):
        self.client.force_login(self.instructor)
        env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        self.get_repo = self.patch("github.Github.get_repo")
        self.get_user = self.patch(
            "github.Github.get_user", return_value=SimpleNamespace(login=TOKEN_ACCOUNT)
        )

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def summary_for(self, user, team):
        return Summary.objects.create(team=team, user=user, week_start=WEEK, artifact_count=1)

    def claim_citing(self, artifact, team=None):
        summary, _ = Summary.objects.get_or_create(
            team=team or self.team,
            user=self.grace,
            week_start=WEEK,
            defaults={"artifact_count": 1},
        )
        claim = Claim.objects.create(
            summary=summary,
            text="Added the login form.",
            category="feature",
            artifact_ids=[artifact.artifact_id],
            significance="moderate",
            status=Claim.Status.VERIFIED,
        )
        claim.artifacts.add(artifact)
        return claim

    def form_tag(self, content, action):
        start = content.rindex("<form", 0, content.index(f'action="{action}"'))
        return content[start : content.index(">", start)]

    def artifact_on(self, repo):
        return Artifact.objects.create(
            repo=repo,
            artifact_id=f"{repo.full_name}:pr:1",
            type=Artifact.Type.PR,
            url=f"https://github.com/{repo.full_name}/pull/1",
            timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/New_York")),
            author_login="sample-grace",
        )


class AddTeamTests(TeamSetupFixture):
    def create(self, name, project=None):
        return self.client.post(
            team_create_url((project or self.project).id), {"name": name}, follow=True
        )

    def team_names(self, project=None):
        return sorted((project or self.project).teams.values_list("name", flat=True))

    def test_overview_offers_an_add_team_form_per_project(self):
        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, f'action="{team_create_url(self.project.id)}"')
        self.assertContains(response, f'action="{team_create_url(self.later_project.id)}"')

    def test_new_team_appears_on_the_overview(self):
        response = self.create("Team Gamma")

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertTrue(self.project.teams.filter(name="Team Gamma").exists())
        self.assertContains(response, "Team Gamma")

    def test_surrounding_whitespace_is_removed(self):
        self.create("  Team Gamma  ")

        self.assertTrue(self.project.teams.filter(name="Team Gamma").exists())

    def test_blank_names_are_rejected_with_a_message(self):
        for name in ["", "   "]:
            with self.subTest(name=name):
                before = self.team_names()

                response = self.create(name)

                self.assertIn("Enter a team name.", page_messages(response))
                self.assertEqual(self.team_names(), before)

    def test_missing_name_is_rejected(self):
        response = self.client.post(team_create_url(self.project.id), {}, follow=True)

        self.assertIn("Enter a team name.", page_messages(response))

    def test_a_name_used_in_this_project_is_rejected_regardless_of_case(self):
        for name in ["Team Alpha", "TEAM alpha", "  team alpha "]:
            with self.subTest(name=name):
                before = self.team_names()

                response = self.create(name)

                self.assertIn("already has a team with this name", page_messages(response))
                self.assertEqual(self.team_names(), before)

    def test_case_is_ignored_beyond_ascii_too(self):
        Team.objects.create(project=self.project, name="Équipe")

        response = self.create("équipe")

        self.assertIn("already has a team with this name", page_messages(response))
        self.assertFalse(self.project.teams.filter(name="équipe").exists())

    def test_a_name_used_only_in_another_project_is_allowed(self):
        self.create("Team Later")

        self.assertTrue(self.project.teams.filter(name="Team Later").exists())

    def test_a_name_longer_than_the_field_is_rejected_with_a_message(self):
        before = self.team_names()

        response = self.create("x" * 201)

        self.assertIn("200 characters or fewer", page_messages(response))
        self.assertEqual(self.team_names(), before)


class RenameTeamTests(TeamSetupFixture):
    def rename(self, name):
        return self.client.post(team_rename_url(self.team.id), {"name": name}, follow=True)

    def stored_name(self):
        return Team.objects.get(pk=self.team.pk).name

    def test_rename_changes_the_name_and_keeps_students_and_repos(self):
        response = self.rename("Team Renamed")

        self.assertRedirects(response, team_url(self.team.id))
        self.assertEqual(self.stored_name(), "Team Renamed")
        self.assertContains(response, "Team Renamed")
        self.assertTrue(TeamMembership.objects.filter(pk=self.membership.pk).exists())
        self.assertTrue(Repo.objects.filter(pk=self.repo.pk).exists())

    def test_a_team_can_change_the_case_of_its_own_name(self):
        self.rename("TEAM ALPHA")

        self.assertEqual(self.stored_name(), "TEAM ALPHA")

    def test_blank_names_are_rejected_with_a_message(self):
        for name in ["", "   "]:
            with self.subTest(name=name):
                response = self.rename(name)

                self.assertIn("Enter a team name.", page_messages(response))
                self.assertEqual(self.stored_name(), "Team Alpha")

    def test_a_name_used_by_another_team_in_this_project_is_rejected_regardless_of_case(self):
        for name in ["Team Beta", "TEAM beta"]:
            with self.subTest(name=name):
                response = self.rename(name)

                self.assertIn("already has a team with this name", page_messages(response))
                self.assertEqual(self.stored_name(), "Team Alpha")

    def test_a_name_used_only_in_another_project_is_allowed(self):
        self.rename("Team Later")

        self.assertEqual(self.stored_name(), "Team Later")

    def test_a_name_longer_than_the_field_is_rejected_with_a_message(self):
        response = self.rename("x" * 201)

        self.assertIn("200 characters or fewer", page_messages(response))
        self.assertEqual(self.stored_name(), "Team Alpha")


class DeleteTeamTests(TeamSetupFixture):
    def delete(self, team=None):
        return self.client.post(team_delete_url((team or self.team).id), follow=True)

    def test_team_page_offers_a_delete_form(self):
        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, f'action="{team_delete_url(self.team.id)}"')

    def test_a_team_with_no_summaries_is_deleted(self):
        response = self.delete()

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertNotContains(response, "Team Alpha</strong>")

    def test_deleting_a_team_keeps_its_students_accounts_and_enrollments(self):
        self.delete()

        self.assertTrue(User.objects.filter(pk=self.grace.pk).exists())
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.grace, course=self.course, role=Role.STUDENT
            ).exists()
        )

    def test_a_team_with_artifacts_but_no_summaries_is_deleted(self):
        self.artifact_on(self.repo)

        self.delete()

        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())

    def test_a_team_with_summaries_is_deleted_along_with_them(self):
        summary = self.summary_for(self.grace, self.team)
        claim = self.claim_citing(self.artifact_on(self.repo))

        response = self.delete()

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertFalse(Summary.objects.filter(pk=summary.pk).exists())
        self.assertFalse(Claim.objects.filter(pk=claim.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.grace.pk).exists())

    def test_the_confirmation_warns_how_many_summaries_go_with_it(self):
        self.summary_for(self.grace, self.team)

        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, team_delete_url(self.team.id))
        self.assertIn("1 summary", tag)
        self.assertIn("permanently", tag)

    def test_the_confirmation_doesnt_mention_summaries_when_there_are_none(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, team_delete_url(self.team.id))
        self.assertIn("confirm(", tag)
        self.assertNotIn("summar", tag)
    def test_another_teams_summaries_dont_block_the_delete(self):
        self.summary_for(self.grace, self.later_team)

        self.delete()

        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())


class AddStudentTests(TeamSetupFixture):
    def add(self, team=None, full_name="Ada Lovelace", login="ada-lovelace", email=""):
        return self.client.post(
            student_add_url((team or self.team).id),
            {"full_name": full_name, "github_login": login, "email": email},
            follow=True,
        )

    def assert_on_team(self, user, team=None):
        self.assertTrue(TeamMembership.objects.filter(user=user, team=team or self.team).exists())

    def role_in_course(self, user):
        return Enrollment.objects.get(user=user, course=self.course).role

    def assert_no_account_named(self, login):
        self.assertFalse(User.objects.filter(github_login__iexact=login).exists())
        self.assertFalse(User.objects.filter(username__iexact=login).exists())

    def test_team_page_offers_the_form_and_explains_the_email(self):
        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, f'action="{student_add_url(self.team.id)}"')
        self.assertContains(response, 'name="full_name"')
        self.assertContains(response, 'name="github_login"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "Co-authored-by:")

    def test_new_student_is_added_and_shown_on_the_team_page(self):
        response = self.add(email="ada@example.edu")

        self.assertRedirects(response, team_url(self.team.id))
        student = User.objects.get(github_login="ada-lovelace")
        self.assertEqual(student.get_full_name(), "Ada Lovelace")
        self.assertEqual(student.email, "ada@example.edu")
        self.assert_on_team(student)
        self.assertContains(response, "Ada Lovelace")

    def test_new_students_username_is_their_github_username(self):
        self.add()

        self.assertEqual(User.objects.get(github_login="ada-lovelace").username, "ada-lovelace")

    def test_new_student_cant_log_in(self):
        self.add()

        student = User.objects.get(github_login="ada-lovelace")
        self.assertFalse(student.has_usable_password())
        self.assertFalse(student.is_staff)
        self.assertFalse(student.is_superuser)

    def test_new_student_is_enrolled_in_the_course_as_a_student(self):
        self.add()

        student = User.objects.get(github_login="ada-lovelace")
        self.assertEqual(self.role_in_course(student), Role.STUDENT)

    def test_email_is_optional(self):
        self.add(email="")

        self.assertEqual(User.objects.get(github_login="ada-lovelace").email, "")

    def test_name_of_one_word_or_many_is_kept_whole(self):
        for index, name in enumerate(["Cher", "Mary Jane Watson"]):
            with self.subTest(name=name):
                login = f"sample-new-{index}"

                self.add(team=self.empty_team, full_name=name, login=login)

                self.assertEqual(User.objects.get(github_login=login).get_full_name(), name)

    def test_surrounding_whitespace_is_removed(self):
        self.add(full_name="  Ada Lovelace ", login=" ada-lovelace ", email=" ada@example.edu ")

        student = User.objects.get(github_login="ada-lovelace")
        self.assertEqual(student.get_full_name(), "Ada Lovelace")
        self.assertEqual(student.email, "ada@example.edu")

    def test_blank_name_is_rejected_with_a_message(self):
        for name in ["", "   "]:
            with self.subTest(name=name):
                response = self.add(full_name=name)

                self.assertIn("Enter the student's full name.", page_messages(response))
                self.assert_no_account_named("ada-lovelace")

    def test_a_name_longer_than_the_fields_is_rejected_with_a_message(self):
        response = self.add(full_name="x" * 151)

        self.assertIn("name is too long", page_messages(response))
        self.assert_no_account_named("ada-lovelace")

    def test_invalid_email_is_rejected_with_a_message(self):
        response = self.add(email="not-an-email")

        self.assertIn("valid email", page_messages(response))
        self.assert_no_account_named("ada-lovelace")

    def test_email_longer_than_the_field_is_rejected_with_a_message(self):
        response = self.add(email=f"{'x' * 60}@{'y' * 60}.{'z' * 60}.{'w' * 60}.{'v' * 60}.edu")

        self.assertIn("valid email", page_messages(response))
        self.assert_no_account_named("ada-lovelace")

    def test_a_clash_at_the_last_step_leaves_no_account_or_enrollment_behind(self):
        """Two adds at once can pass every check, and a constraint stops the second."""
        with mock.patch.object(TeamMembership, "save", side_effect=IntegrityError):
            response = self.add()

        self.assertIn("try again", page_messages(response))
        self.assert_no_account_named("ada-lovelace")
        self.assertEqual(Enrollment.objects.filter(course=self.course).count(), 2)

    def test_invalid_github_usernames_are_rejected_with_a_message(self):
        invalid = [
            "",
            "   ",
            "-ada",
            "ada-",
            "ada--lovelace",
            "ada lovelace",
            "ada_lovelace",
            "ada.lovelace",
            "ada/lovelace",
            "@ada",
            "x" * 40,
        ]
        for login in invalid:
            with self.subTest(login=login):
                before = User.objects.count()

                response = self.add(login=login)

                self.assertIn("valid GitHub username", page_messages(response))
                self.assertEqual(User.objects.count(), before)
                self.assertEqual(self.team.memberships.count(), 1)

    def test_longest_valid_github_username_is_accepted(self):
        self.add(login="x" * 39)

        self.assertTrue(User.objects.filter(github_login="x" * 39).exists())

    def test_missing_fields_are_rejected_without_an_error_page(self):
        response = self.client.post(student_add_url(self.team.id), {}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(page_messages(response), "")
        self.assertEqual(self.team.memberships.count(), 1)

    def test_existing_account_is_used_regardless_of_case(self):
        ada = make_user("Ada-Lovelace", "Augusta", "King")
        before = User.objects.count()

        self.add(login="ada-LOVELACE")

        self.assertEqual(User.objects.count(), before)
        self.assert_on_team(ada)

    def test_existing_accounts_name_and_email_arent_changed(self):
        ada = make_user("ada-lovelace", "Augusta", "King")
        User.objects.filter(pk=ada.pk).update(email="augusta@example.edu")

        self.add(full_name="Ada Lovelace", email="ada@example.edu")

        ada.refresh_from_db()
        self.assertEqual(ada.get_full_name(), "Augusta King")
        self.assertEqual(ada.email, "augusta@example.edu")
        self.assertEqual(ada.github_login, "ada-lovelace")

    def test_existing_accounts_blank_email_isnt_filled_in(self):
        ada = make_user("ada-lovelace")

        self.add(email="ada@example.edu")

        ada.refresh_from_db()
        self.assertEqual(ada.email, "")

    def test_existing_accounts_password_isnt_changed(self):
        ada = make_user("ada-lovelace")

        self.add()

        ada.refresh_from_db()
        self.assertTrue(ada.check_password("not-a-real-password"))

    def test_existing_account_not_in_the_course_is_enrolled_as_a_student(self):
        ada = make_user("ada-lovelace")

        self.add()

        self.assertEqual(self.role_in_course(ada), Role.STUDENT)

    def test_existing_student_in_the_course_keeps_one_enrollment(self):
        ada = make_user("ada-lovelace")
        enroll(ada, self.course, Role.STUDENT)

        self.add()

        self.assertEqual(Enrollment.objects.filter(user=ada, course=self.course).count(), 1)
        self.assert_on_team(ada)

    def test_instructor_elsewhere_can_be_a_student_here(self):
        ada = make_user("ada-lovelace")
        enroll(ada, self.other_course, Role.INSTRUCTOR)

        self.add()

        self.assertEqual(self.role_in_course(ada), Role.STUDENT)
        self.assertEqual(Enrollment.objects.get(user=ada, course=self.other_course).role, "instructor")
        self.assert_on_team(ada)

    def test_a_username_taken_by_a_different_account_is_refused_with_a_message(self):
        other = User.objects.create_user(
            username="ada-lovelace", password="not-a-real-password", github_login="someone-else"
        )
        before = User.objects.count()

        response = self.add()

        self.assertIn("already taken", page_messages(response))
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(Enrollment.objects.filter(user=other, course=self.course).exists())
        self.assertFalse(TeamMembership.objects.filter(user=other).exists())

    def test_student_on_another_team_in_this_project_is_refused_and_it_is_named(self):
        response = self.add(team=self.empty_team, login="SAMPLE-grace")

        messages = page_messages(response)
        self.assertIn("Team Alpha", messages)
        self.assertIn("already on", messages)
        self.assertFalse(self.empty_team.memberships.exists())
        self.assertEqual(TeamMembership.objects.filter(user=self.grace).count(), 1)

    def test_student_already_on_this_team_is_refused_and_it_is_named(self):
        response = self.add(login="sample-grace")

        messages = page_messages(response)
        self.assertIn("Team Alpha", messages)
        self.assertIn("already on", messages)
        self.assertEqual(TeamMembership.objects.filter(user=self.grace).count(), 1)

    def test_student_on_a_team_in_another_project_can_be_added(self):
        self.add(team=self.later_team, login="sample-grace")

        self.assert_on_team(self.grace, self.later_team)
        self.assert_on_team(self.grace, self.team)

    def test_this_courses_instructor_or_ta_is_refused_and_their_role_doesnt_change(self):
        ta = make_user("sample-ta", "Tess", "Assistant")
        enroll(ta, self.course, Role.TA)
        for user, role, label in [
            (self.instructor, Role.INSTRUCTOR, "instructor"),
            (ta, Role.TA, "TA"),
        ]:
            with self.subTest(role=role):
                response = self.add(login=user.github_login.upper())

                self.assertIn(label, page_messages(response))
                self.assertIn("can't be added to a team", page_messages(response))
                self.assertFalse(TeamMembership.objects.filter(user=user).exists())
                self.assertEqual(self.role_in_course(user), role)


class EditStudentTests(TeamSetupFixture):
    def edit(self, **fields):
        data = {"full_name": "Grace Hopper"}
        data.update(fields)
        return self.client.post(membership_edit_url(self.membership.id), data, follow=True)

    def stored(self):
        return User.objects.get(pk=self.grace.pk)

    def page(self, query=""):
        return self.client.get(team_url(self.team.id) + query)

    def query(self):
        return f"?edit_student={self.membership.id}"

    def test_team_page_has_an_edit_link_per_student_and_no_form_until_clicked(self):
        response = self.page()

        self.assertContains(response, f'href="{self.query()}"')
        self.assertNotContains(response, f'action="{membership_edit_url(self.membership.id)}"')

    def test_edit_shows_only_the_name_with_cancel(self):
        content = self.page(self.query()).content.decode()

        start = content.index(f'action="{membership_edit_url(self.membership.id)}"')
        form = content[start : content.index("</form>", start)]
        self.assertIn('name="full_name"', form)
        self.assertIn('value="Grace Example"', form)
        self.assertIn("autofocus", form)
        self.assertIn("Cancel", form)
        self.assertNotIn('name="github_login"', form)
        self.assertNotIn('name="email"', form)

    def test_edit_shows_the_github_username_as_read_only_text_below_the_name(self):
        content = self.page(self.query()).content.decode()

        start = content.index(f'action="{membership_edit_url(self.membership.id)}"')
        form = content[start : content.index("</form>", start)]
        self.assertRegex(form, r'(?s)name="full_name".*<code>sample-grace</code>')

    def test_a_meaningless_edit_value_shows_the_normal_page(self):
        for query in ["?edit_student=abc", "?edit_student=", "?edit_student=999999"]:
            with self.subTest(query=query):
                response = self.page(query)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(
                    response, f'action="{membership_edit_url(self.membership.id)}"'
                )

    def test_the_name_can_be_changed(self):
        response = self.edit()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertEqual(self.stored().get_full_name(), "Grace Hopper")
        self.assertContains(response, "Grace Hopper")

    def test_submitted_github_username_and_email_are_ignored(self):
        self.edit(github_login="grace-hopper", email="grace@example.edu", username="grace-hopper")

        stored = self.stored()
        self.assertEqual(stored.get_full_name(), "Grace Hopper")
        self.assertEqual(stored.github_login, "sample-grace")
        self.assertEqual(stored.username, "sample-grace")
        self.assertEqual(stored.email, "")

    def test_bad_names_are_refused_and_nothing_changes(self):
        cases = [("  ", "Enter the student's full name."), ("x" * 151, "name is too long")]
        for name, message in cases:
            with self.subTest(name=name):
                response = self.edit(full_name=name)

                self.assertIn(message, page_messages(response))
                self.assertEqual(self.stored().get_full_name(), "Grace Example")

    def test_a_student_who_is_also_a_student_elsewhere_can_be_edited(self):
        enroll(self.grace, self.other_course, Role.STUDENT)

        self.edit()

        self.assertEqual(self.stored().get_full_name(), "Grace Hopper")

    def test_an_account_that_runs_another_course_cant_be_edited_from_here(self):
        """An instructor here shouldn't be able to rename another course's staff."""
        for role in [Role.INSTRUCTOR, Role.TA]:
            with self.subTest(role=role):
                enrollment = enroll(self.grace, self.other_course, role)

                response = self.edit()

                self.assertIn("in another course", page_messages(response))
                self.assertEqual(self.stored().get_full_name(), "Grace Example")
                enrollment.delete()

    def test_a_staff_or_superuser_account_cant_be_edited_from_here(self):
        for field in ["is_staff", "is_superuser"]:
            with self.subTest(field=field):
                User.objects.filter(pk=self.grace.pk).update(**{field: True})

                response = self.edit()

                self.assertIn("admin", page_messages(response))
                self.assertEqual(self.stored().get_full_name(), "Grace Example")
                User.objects.filter(pk=self.grace.pk).update(**{field: False})


class RemoveStudentTests(TeamSetupFixture):
    def remove(self):
        return self.client.post(membership_remove_url(self.membership.id), follow=True)

    def on_team(self):
        return TeamMembership.objects.filter(pk=self.membership.pk).exists()

    def test_team_page_offers_a_remove_form_per_student(self):
        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, f'action="{membership_remove_url(self.membership.id)}"')

    def test_the_remove_control_is_small_text_not_a_big_button(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        start = content.rindex(
            "<form", 0, content.index(f'action="{membership_remove_url(self.membership.id)}"')
        )
        form = content[start : content.index("</form>", start)]
        self.assertIn('class="text-button"', form)
        self.assertNotIn("Remove from team", form)
        self.assertIn('onsubmit="return confirm(', form)

    def test_a_student_with_no_summaries_is_removed(self):
        response = self.remove()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertFalse(self.on_team())
        self.assertEqual(list(response.context["memberships"]), [])

    def test_removal_keeps_their_account_and_enrollment(self):
        self.remove()

        self.assertTrue(User.objects.filter(pk=self.grace.pk).exists())
        self.assertEqual(
            Enrollment.objects.get(user=self.grace, course=self.course).role, Role.STUDENT
        )

    def test_a_student_with_summaries_is_removed_along_with_those_summaries(self):
        summary = self.summary_for(self.grace, self.team)
        elsewhere = self.summary_for(self.grace, self.later_team)

        response = self.remove()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertFalse(self.on_team())
        self.assertFalse(Summary.objects.filter(pk=summary.pk).exists())
        self.assertTrue(Summary.objects.filter(pk=elsewhere.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.grace.pk).exists())

    def test_a_teammates_summaries_stay(self):
        teammate = make_user("sample-teammate")
        enroll(teammate, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=teammate, team=self.team)
        summary = self.summary_for(teammate, self.team)
        self.summary_for(self.grace, self.team)

        self.remove()

        self.assertTrue(Summary.objects.filter(pk=summary.pk).exists())

    def test_the_teams_flags_are_recomputed_without_them(self):
        flag = Flag.objects.create(
            team=self.team,
            user=self.grace,
            rule="low-requirement-share",
            reason="Sample reason.",
            severity=Flag.Severity.FLAG,
            week_start=WEEK,
        )

        self.remove()

        self.assertFalse(Flag.objects.filter(pk=flag.pk).exists())

    def test_the_confirmation_warns_how_many_summaries_go_with_them(self):
        self.summary_for(self.grace, self.team)

        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, membership_remove_url(self.membership.id))
        self.assertIn("1 summary", tag)
        self.assertIn("permanently", tag)

    def test_the_confirmation_and_message_use_the_name_the_page_shows(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, membership_remove_url(self.membership.id))
        self.assertIn("Remove Grace Example from this team?", tag)
        self.assertIn("Removed Grace Example", page_messages(self.remove()))

    def test_the_confirmation_says_their_account_stays(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, membership_remove_url(self.membership.id))
        self.assertIn("account", tag)
        self.assertNotIn("summar", tag)
    def test_their_summaries_on_another_team_dont_block_it(self):
        self.summary_for(self.grace, self.later_team)

        self.remove()

        self.assertFalse(self.on_team())

    def test_a_teammates_summaries_dont_block_it(self):
        teammate = make_user("sample-teammate")
        enroll(teammate, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=teammate, team=self.team)
        self.summary_for(teammate, self.team)

        self.remove()

        self.assertFalse(self.on_team())


class AddRepoTests(TeamSetupFixture):
    def add(self, url=NEW_REPO_URL, team=None):
        return self.client.post(
            repo_add_url((team or self.empty_team).id), {"url": url}, follow=True
        )

    def full_names(self, team=None):
        return sorted((team or self.empty_team).repos.values_list("full_name", flat=True))

    def assert_not_saved(self):
        self.assertEqual(self.full_names(), [])

    def test_team_page_offers_the_form_when_the_team_has_no_repo(self):
        response = self.client.get(team_url(self.empty_team.id))

        self.assertContains(response, f'action="{repo_add_url(self.empty_team.id)}"')
        self.assertContains(response, 'name="url"')

    def test_team_page_doesnt_offer_it_when_the_team_has_a_repo(self):
        response = self.client.get(team_url(self.team.id))

        self.assertNotContains(response, f'action="{repo_add_url(self.team.id)}"')
        self.assertNotContains(response, "Add repo")
    def test_repo_is_added_and_shown_on_the_team_page(self):
        response = self.add()

        self.assertRedirects(response, team_url(self.empty_team.id))
        self.assertEqual(self.full_names(), ["acme/app"])
        self.assertContains(response, "acme/app")
    def test_url_variations_are_the_same_repo(self):
        urls = [
            "https://github.com/acme/app",
            "https://github.com/acme/app.git",
            "https://github.com/acme/app/",
            "https://github.com/acme/app.git/",
            "  https://github.com/acme/app  ",
        ]
        for url in urls:
            with self.subTest(url=url):
                Repo.objects.filter(team=self.empty_team).delete()

                self.add(url, team=self.empty_team)

                self.assertEqual(self.full_names(self.empty_team), ["acme/app"])

    def test_non_github_urls_are_rejected_with_a_message_and_no_github_call(self):
        urls = [
            "",
            "not a url",
            "http://github.com/acme/app",
            "git@github.com:acme/app.git",
            "https://gitlab.com/acme/app",
            "https://github.com.example.com/acme/app",
            "https://github.com/acme",
            "https://github.com/acme/app/issues",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.add(url)

                self.assertIn("Enter a GitHub repo URL", page_messages(response))
                self.assert_not_saved()
        self.get_repo.assert_not_called()

    def test_a_team_with_a_repo_cant_add_another(self):
        for url in [NEW_REPO_URL, "https://github.com/ACME/Existing.git/"]:
            with self.subTest(url=url):
                response = self.add(url, team=self.team)

                self.assertIn("already has a repo", page_messages(response))
                self.assertEqual(self.full_names(self.team), ["acme/existing"])
        self.get_repo.assert_not_called()
    def test_the_same_repo_added_at_the_same_moment_is_rejected_with_a_message(self):
        """Two adds at once can both pass the form's check, and the constraint stops the second."""
        with mock.patch("core.forms.RepoForm.save", side_effect=IntegrityError):
            response = self.add()

        self.assertIn("This team already has this repo.", page_messages(response))
        self.assert_not_saved()

    def test_adding_checks_that_github_can_read_the_named_repo(self):
        self.add("https://github.com/acme/app.git/")

        self.get_repo.assert_called_once()
        self.assertEqual(self.get_repo.call_args.args[0], "acme/app")

    def test_adding_doesnt_pull_any_activity(self):
        with mock.patch("ingest.services.ingest") as ingest:
            self.add()

        ingest.assert_not_called()
        self.assertFalse(Artifact.objects.filter(repo__full_name="acme/app").exists())

    def test_missing_or_unreadable_repo_names_the_tokens_account(self):
        errors = [
            UnknownObjectException(404, {"message": "Not Found"}, {}),
            github_error(403, "Resource not accessible"),
        ]
        for error in errors:
            with self.subTest(status=error.status):
                self.get_repo.side_effect = error

                response = self.add()

                messages = page_messages(response)
                self.assertIn(f"Add {TOKEN_ACCOUNT} to this repo", messages)
                self.assertIn("STRIVE", messages)
                self.assert_not_saved()

    def test_unknown_account_name_still_says_strives_account_needs_access(self):
        failures = [
            BadCredentialsException(401, {"message": "Bad credentials"}, {}),
            github_error(500),
            requests.ConnectionError("no network"),
        ]
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.get_user.side_effect = failure

                response = self.add()

                self.assertIn("STRIVE's GitHub account", page_messages(response))
                self.assertIn("access", page_messages(response))
                self.assert_not_saved()

    def test_a_rejected_token_says_so(self):
        self.get_repo.side_effect = BadCredentialsException(401, {"message": "Bad credentials"}, {})

        response = self.add()

        self.assertIn("GITHUB_TOKEN", page_messages(response))
        self.assert_not_saved()

    def test_a_missing_token_says_so_without_calling_github(self):
        with mock.patch.dict(os.environ):
            del os.environ["GITHUB_TOKEN"]
            response = self.add()

        self.assertIn("GITHUB_TOKEN isn't set", page_messages(response))
        self.get_repo.assert_not_called()
        self.assert_not_saved()

    def test_a_rate_limit_says_so_rather_than_asking_for_access(self):
        errors = [
            RateLimitExceededException(403, {"message": "API rate limit exceeded"}, {}),
            github_error(429),
        ]
        for error in errors:
            with self.subTest(status=error.status):
                self.get_repo.side_effect = error

                response = self.add()

                self.assertIn("rate limit", page_messages(response))
                self.assertNotIn(TOKEN_ACCOUNT, page_messages(response))
                self.assert_not_saved()

    def test_a_github_server_error_says_to_try_again(self):
        self.get_repo.side_effect = github_error(500)

        response = self.add()

        self.assertIn("Try again later", page_messages(response))
        self.assert_not_saved()

    def test_being_unable_to_reach_github_says_so(self):
        self.get_repo.side_effect = requests.ConnectionError("no network")

        response = self.add()

        self.assertIn("reach GitHub", page_messages(response))
        self.assert_not_saved()

    def test_the_token_never_reaches_the_page(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": TOKEN}, {})

        response = self.add()

        self.assertNotContains(response, TOKEN)

    def test_a_team_with_several_repos_from_the_admin_cant_add_another(self):
        Repo.objects.create(
            team=self.team, url="https://github.com/acme/second", full_name="acme/second"
        )

        response = self.add(team=self.team)

        self.assertIn("already has a repo", page_messages(response))
        self.assertEqual(self.full_names(self.team), ["acme/existing", "acme/second"])
    def test_the_same_repo_can_be_used_by_a_team_in_another_project(self):
        self.add("https://github.com/acme/existing", team=self.later_team)

        self.assertEqual(self.full_names(self.later_team), ["acme/existing"])
        self.assertEqual(self.full_names(self.team), ["acme/existing"])


class ChangeRepoTests(TeamSetupFixture):
    CHANGED = "https://github.com/acme/changed"

    def change(self, url=CHANGED, repo=None):
        return self.client.post(
            repo_change_url((repo or self.repo).id), {"url": url}, follow=True
        )

    def stored(self):
        return Repo.objects.get(pk=self.repo.pk).full_name

    def page(self, query=""):
        return self.client.get(team_url(self.team.id) + query)

    def change_query(self):
        return f"?change_repo={self.repo.id}"

    def test_team_page_offers_a_change_link_and_no_form_until_it_is_clicked(self):
        response = self.page()

        self.assertContains(response, f'href="{self.change_query()}"')
        self.assertNotContains(response, f'action="{repo_change_url(self.repo.id)}"')

    def test_change_shows_the_current_url_with_cancel(self):
        response = self.page(self.change_query())

        self.assertContains(response, f'action="{repo_change_url(self.repo.id)}"')
        self.assertContains(response, 'value="https://github.com/acme/existing"')
        self.assertContains(response, f'href="{team_url(self.team.id)}"')
        self.assertContains(response, "Cancel")

    def test_a_meaningless_change_value_shows_the_normal_page(self):
        for query in ["?change_repo=abc", "?change_repo=", "?change_repo=999999"]:
            with self.subTest(query=query):
                response = self.page(query)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, f'action="{repo_change_url(self.repo.id)}"')

    def test_change_asks_for_confirmation_first(self):
        content = self.page(self.change_query()).content.decode()

        start = content.rindex("<form", 0, content.index(f'action="{repo_change_url(self.repo.id)}"'))
        tag = content[start : content.index(">", start)]
        self.assertIn('onsubmit="return confirm(', tag)
        self.assertIn("acme/existing", tag)

    def test_change_points_the_teams_repo_at_the_new_one(self):
        response = self.change()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertEqual(self.team.repos.count(), 1)
        stored = Repo.objects.get(pk=self.repo.pk)
        self.assertEqual(stored.full_name, "acme/changed")
        self.assertEqual(stored.url, self.CHANGED)
        self.assertContains(response, "acme/changed")

    def test_change_checks_that_github_can_read_the_new_repo(self):
        self.change("https://github.com/acme/changed.git/")

        self.get_repo.assert_called_once()
        self.assertEqual(self.get_repo.call_args.args[0], "acme/changed")
        self.assertEqual(self.stored(), "acme/changed")

    def test_an_unreadable_repo_keeps_the_old_one_and_names_the_tokens_account(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})

        response = self.change()

        self.assertIn(f"Add {TOKEN_ACCOUNT} to this repo", page_messages(response))
        self.assertEqual(self.stored(), "acme/existing")

    def test_a_non_github_url_keeps_the_old_one(self):
        for url in ["", "https://gitlab.com/acme/app"]:
            with self.subTest(url=url):
                response = self.change(url)

                self.assertIn("Enter a GitHub repo URL", page_messages(response))
                self.assertEqual(self.stored(), "acme/existing")
        self.get_repo.assert_not_called()

    def test_changing_to_the_same_repo_is_refused_regardless_of_case(self):
        response = self.change("https://github.com/ACME/Existing.git/")

        self.assertIn("This team already has this repo.", page_messages(response))
        self.assertEqual(self.stored(), "acme/existing")
        self.get_repo.assert_not_called()

    def test_changing_to_another_of_this_teams_repos_from_the_admin_is_refused(self):
        Repo.objects.create(
            team=self.team, url="https://github.com/acme/second", full_name="acme/second"
        )

        response = self.change("https://github.com/acme/second")

        self.assertIn("This team already has this repo.", page_messages(response))
        self.assertEqual(self.stored(), "acme/existing")

    def test_two_changes_at_the_same_moment_show_a_message(self):
        with mock.patch("core.forms.RepoForm.save", side_effect=IntegrityError):
            response = self.change()

        self.assertIn("This team already has this repo.", page_messages(response))
        self.assertEqual(self.stored(), "acme/existing")

    def test_a_repo_with_artifacts_is_refused_with_a_message_and_no_github_call(self):
        artifact = self.artifact_on(self.repo)

        response = self.change()

        self.assertIn("has stored artifacts", page_messages(response))
        self.assertEqual(self.stored(), "acme/existing")
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())
        self.get_repo.assert_not_called()

    def test_a_repo_with_uncited_artifacts_offers_remove_but_not_change(self):
        self.artifact_on(self.repo)

        response = self.page(self.change_query())

        self.assertNotContains(response, f'action="{repo_change_url(self.repo.id)}"')
        self.assertNotContains(response, f'href="{self.change_query()}"')
        self.assertContains(response, f'action="{repo_remove_url(self.repo.id)}"')
        self.assertContains(response, "remove this one first")

    def test_a_repo_with_cited_artifacts_offers_neither_and_says_why(self):
        self.claim_citing(self.artifact_on(self.repo))

        response = self.page(self.change_query())

        self.assertNotContains(response, f'action="{repo_change_url(self.repo.id)}"')
        self.assertNotContains(response, f'href="{self.change_query()}"')
        self.assertNotContains(response, f'action="{repo_remove_url(self.repo.id)}"')
        self.assertContains(response, "summaries cite")
    def test_another_teams_copy_isnt_touched(self):
        copy = Repo.objects.create(
            team=self.later_team, url=self.repo.url, full_name=self.repo.full_name
        )

        self.change()

        self.assertEqual(Repo.objects.get(pk=copy.pk).full_name, "acme/existing")


class RemoveRepoTests(TeamSetupFixture):
    def remove(self):
        return self.client.post(repo_remove_url(self.repo.id), follow=True)

    def exists(self):
        return Repo.objects.filter(pk=self.repo.pk).exists()

    def test_team_page_offers_a_remove_form_per_repo(self):
        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, f'action="{repo_remove_url(self.repo.id)}"')

    def test_a_repo_with_no_artifacts_is_removed(self):
        response = self.remove()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertFalse(self.exists())

    def test_a_repo_with_artifacts_no_claim_cites_is_removed_along_with_them(self):
        artifact = self.artifact_on(self.repo)

        response = self.remove()

        self.assertRedirects(response, team_url(self.team.id))
        self.assertFalse(self.exists())
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_a_repo_with_artifacts_a_claim_cites_is_refused_with_a_message(self):
        """Removing it would leave a verified claim on the page with no GitHub link."""
        artifact = self.artifact_on(self.repo)
        claim = self.claim_citing(artifact)

        response = self.remove()

        self.assertIn("Summaries cite", page_messages(response))
        self.assertTrue(self.exists())
        self.assertEqual(list(claim.artifacts.all()), [artifact])

    def test_the_confirmation_explains_what_happens(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        tag = self.form_tag(content, repo_remove_url(self.repo.id))
        self.assertIn("activity", tag)
    def test_artifacts_on_another_teams_copy_dont_block_it(self):
        copy = Repo.objects.create(
            team=self.later_team, url=self.repo.url, full_name=self.repo.full_name
        )
        self.artifact_on(copy)

        self.remove()

        self.assertFalse(self.exists())
        self.assertTrue(Repo.objects.filter(pk=copy.pk).exists())


class MissingSetupTests(TeamSetupFixture):
    def overview_card(self, team):
        content = self.client.get(overview_url(self.course.id)).content.decode()
        return team_article(content, team.name)

    def test_overview_says_a_team_has_no_repos_yet(self):
        self.assertIn("No repo yet", self.overview_card(self.empty_team))

    def test_overview_says_a_team_has_no_students_yet(self):
        self.assertIn("No students", self.overview_card(self.empty_team))

    def test_overview_doesnt_say_so_for_a_team_that_has_both(self):
        card = self.overview_card(self.team)

        self.assertNotIn("No repo yet", card)
        self.assertNotIn("No students", card)

    def test_team_page_says_what_the_team_is_missing(self):
        response = self.client.get(team_url(self.empty_team.id))

        self.assertContains(response, "No repo yet")
        self.assertContains(response, "No students")

    def test_project_says_it_has_no_teams_or_requirements_yet(self):
        project = Project.objects.create(course=self.course, name="Project Empty")

        response = self.client.get(overview_url(self.course.id))

        section = response.content.decode().split(f'id="project-{project.id}"')[1]
        self.assertIn("No teams", section)
        self.assertIn("No requirements", section)

    def test_generating_for_a_team_with_no_repos_says_so_and_writes_nothing(self):
        """Otherwise each student would get a "No activity" summary that looks like a finding."""
        Repo.objects.filter(team=self.team).delete()
        clock = self.patch("django.utils.timezone.now", return_value=NOW)
        generate = self.patch("summaries.services.generate_summary")

        response = self.client.post(
            generate_url(self.team.id), {"week_start": WEEK.isoformat()}, follow=True
        )

        self.assertTrue(clock.called)
        self.assertIn("no repo", page_messages(response).lower())
        generate.assert_not_called()
        self.assertFalse(Summary.objects.filter(team=self.team).exists())


class EndToEndTests(TestCase):
    """The real generate_summary, validator and flag rules. Only GitHub and the LLM are mocked."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

    def setUp(self):
        self.client.force_login(self.instructor)
        for patcher in (
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}),
            mock.patch("github.Github.get_repo"),
            mock.patch("django.utils.timezone.now", return_value=NOW),
            mock.patch("ingest.services.ingest", side_effect=self.pull_one_pr),
            mock.patch(
                "summaries.services.generate_claims",
                return_value=[
                    schemas.Claim(
                        text="Opened the login form PR.",
                        category="feature",
                        artifact_ids=["acme/app:pr:1"],
                        requirement_ids=[],
                        significance="major",
                    )
                ],
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def pull_one_pr(repo, week_start, week_end):
        return [
            Artifact.objects.create(
                repo=repo,
                artifact_id=f"{repo.full_name}:pr:1",
                type=Artifact.Type.PR,
                url=f"https://github.com/{repo.full_name}/pull/1",
                timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/New_York")),
                author_login="ada-lovelace",
            )
        ]

    def test_an_instructor_goes_from_nothing_to_reading_a_summary(self):
        self.assertFalse(self.instructor.is_staff)

        self.client.post(
            reverse("dashboard:project_create", args=[self.course.id]), {"name": "Project One"}
        )
        project = Project.objects.get(course=self.course, name="Project One")

        overview = self.client.get(overview_url(self.course.id))
        self.assertContains(overview, f'action="{team_create_url(project.id)}"')
        self.client.post(team_create_url(project.id), {"name": "Team Alpha"})
        team = project.teams.get(name="Team Alpha")

        page = self.client.get(team_url(team.id))
        self.assertContains(page, f'action="{student_add_url(team.id)}"')
        self.assertContains(page, f'action="{repo_add_url(team.id)}"')
        self.client.post(
            student_add_url(team.id),
            {"full_name": "Ada Lovelace", "github_login": "ada-lovelace", "email": ""},
        )
        self.client.post(repo_add_url(team.id), {"url": NEW_REPO_URL})

        overview = self.client.get(overview_url(self.course.id))
        self.assertContains(overview, f'action="{generate_url(team.id)}"')
        self.client.post(generate_url(team.id), {"week_start": WEEK.isoformat()})

        summary = Summary.objects.get(team=team, week_start=WEEK)
        self.assertEqual(summary.user.github_login, "ada-lovelace")
        page = self.client.get(team_url(team.id))
        summary_url = reverse("dashboard:summary", args=[summary.id])
        self.assertContains(page, f'href="{summary_url}"')

        page = self.client.get(summary_url)
        self.assertContains(page, "Opened the login form PR.")
        self.assertContains(page, "https://github.com/acme/app/pull/1")


class AccessTests(TeamSetupFixture):
    def get_requests(self):
        """Every setup request, as (description, URL, data)."""
        return [
            ("add team", team_create_url(self.project.id), {"name": "Team New"}),
            ("rename team", team_rename_url(self.team.id), {"name": "Team Renamed"}),
            ("delete team", team_delete_url(self.empty_team.id), {}),
            (
                "add student",
                student_add_url(self.team.id),
                {"full_name": "Ada Lovelace", "github_login": "ada-lovelace", "email": ""},
            ),
            (
                "edit student",
                membership_edit_url(self.membership.id),
                {"full_name": "Grace Hopper", "github_login": "grace-hopper", "email": ""},
            ),
            ("remove student", membership_remove_url(self.membership.id), {}),
            ("change repo", repo_change_url(self.repo.id), {"url": ChangeRepoTests.CHANGED}),
            ("remove repo", repo_remove_url(self.repo.id), {}),
            ("add repo", repo_add_url(self.team.id), {"url": NEW_REPO_URL}),
        ]

    def state(self):
        return {
            "teams": sorted(Team.objects.values_list("id", "name")),
            "memberships": sorted(TeamMembership.objects.values_list("id", flat=True)),
            "repos": sorted(Repo.objects.values_list("id", "full_name")),
            "users": sorted(User.objects.values_list("id", "first_name", "github_login")),
            "enrollments": sorted(Enrollment.objects.values_list("user_id", "course_id", "role")),
        }

    def setup_urls(self):
        return [url for _description, url, _data in self.get_requests()]

    def assert_refused(self, user):
        self.client.force_login(user)
        before = self.state()
        for description, url, data in self.get_requests():
            with self.subTest(request=description):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self.state(), before)
        self.get_repo.assert_not_called()

    def test_instructor_without_is_staff_can_do_all_of_it(self):
        self.assertFalse(self.instructor.is_staff)
        for description, url, data in self.get_requests():
            with self.subTest(request=description):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("/admin/", response["Location"])

        names = set(self.project.teams.values_list("name", flat=True))
        self.assertEqual(names, {"Team New", "Team Renamed"})
        self.assertTrue(self.team.memberships.filter(user__github_login="ada-lovelace").exists())
        self.assertFalse(self.team.memberships.filter(user=self.grace).exists())
        self.assertEqual(list(self.team.repos.values_list("full_name", flat=True)), ["acme/app"])

    def test_ta_is_refused(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        self.assert_refused(ta)

    def test_student_is_refused(self):
        self.assert_refused(self.grace)

    def test_another_courses_instructor_is_refused(self):
        other = make_user("sample-other")
        enroll(other, self.other_course, Role.INSTRUCTOR)

        self.assert_refused(other)

    def test_being_an_instructor_elsewhere_doesnt_help_a_ta(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        enroll(ta, self.other_course, Role.INSTRUCTOR)

        self.assert_refused(ta)

    def test_unenrolled_user_is_refused(self):
        self.assert_refused(make_user("sample-unenrolled"))

    def test_unenrolled_superuser_is_refused(self):
        self.assert_refused(
            User.objects.create_superuser(
                username="sample-admin", password="not-a-real-password", github_login="sample-admin"
            )
        )

    def test_anonymous_user_is_sent_to_log_in(self):
        self.client.logout()
        before = self.state()
        for description, url, data in self.get_requests():
            with self.subTest(request=description):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])
        self.assertEqual(self.state(), before)

    def test_ta_doesnt_see_these_actions(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.client.force_login(ta)

        overview = self.client.get(overview_url(self.course.id))
        page = self.client.get(team_url(self.team.id))

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(page.status_code, 200)
        for url in self.setup_urls():
            with self.subTest(url=url):
                self.assertNotContains(overview, url)
                self.assertNotContains(page, url)
        self.assertNotContains(page, 'name="github_login"')
        # What the team has is still theirs to see.
        self.assertContains(page, "acme/existing")
        self.assertContains(page, "Grace Example")

    def test_missing_project_team_student_or_repo_is_a_404(self):
        missing = 999999
        requests_to_make = [
            ("add team", team_create_url(missing), {"name": "Team New"}),
            ("rename team", team_rename_url(missing), {"name": "Team New"}),
            ("delete team", team_delete_url(missing), {}),
            (
                "add student",
                student_add_url(missing),
                {"full_name": "Ada Lovelace", "github_login": "ada-lovelace", "email": ""},
            ),
            (
                "edit student",
                membership_edit_url(missing),
                {"full_name": "Grace Hopper", "github_login": "grace-hopper", "email": ""},
            ),
            ("remove student", membership_remove_url(missing), {}),
            ("add repo", repo_add_url(missing), {"url": NEW_REPO_URL}),
            ("change repo", repo_change_url(missing), {"url": NEW_REPO_URL}),
            ("remove repo", repo_remove_url(missing), {}),
        ]
        for description, url, data in requests_to_make:
            with self.subTest(request=description):
                self.assertEqual(self.client.post(url, data).status_code, 404)

    def test_changes_refuse_get(self):
        before = self.state()
        for description, url, _data in self.get_requests():
            with self.subTest(request=description):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.state(), before)

    def test_changes_need_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        before = self.state()
        for description, url, data in self.get_requests():
            with self.subTest(request=description):
                self.assertEqual(client.post(url, data).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_every_setup_form_posts_with_a_csrf_token(self):
        for page in [overview_url(self.course.id), team_url(self.team.id)]:
            with self.subTest(page=page):
                content = self.client.get(page).content.decode()
                forms = content.split("<form")[1:]
                setup_forms = [
                    form for form in forms if any(url in form for url in self.setup_urls())
                ]
                self.assertTrue(setup_forms)
                for form in setup_forms:
                    self.assertIn('method="post"', form.split(">")[0])
                    self.assertIn("csrfmiddlewaretoken", form)
