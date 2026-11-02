"""Tests for US-1 (#17): setting up a course, its teams and their repos in admin.

These drive the real admin pages, so they check what an instructor can do rather than
how the admin classes are written.
"""

from datetime import UTC, datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import Course, Enrollment, Project, Repo, Team, TeamMembership, User
from ingest.models import Artifact


def make_user(login, username=None):
    return User.objects.create_user(
        username=username or login, password="not-a-real-password", github_login=login
    )


def formset(prefix, rows=(), initial=0):
    """POST data for an inline formset. The first `initial` rows are existing ones."""
    data = {
        f"{prefix}-TOTAL_FORMS": len(rows),
        f"{prefix}-INITIAL_FORMS": initial,
        f"{prefix}-MIN_NUM_FORMS": 0,
        f"{prefix}-MAX_NUM_FORMS": 1000,
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"{prefix}-{index}-{field}"] = value
    return data


def team_form(project, name="Team A", members=(), repos=(), initial_members=0, initial_repos=0):
    """POST data for the team page, which holds a team's members and repos."""
    return {
        "project": project.pk,
        "name": name,
        **formset("memberships", members, initial_members),
        **formset("repos", repos, initial_repos),
    }


class AdminTestCase(TestCase):
    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser(
                username="admin",
                password="not-a-real-password",
                email="admin@example.com",
                github_login="course-admin",
            )
        )
        self.course = Course.objects.create(name="CS 3240, Fall 2026")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.team = Team.objects.create(project=self.project, name="Team A")

    def admin_url(self, model, action, *args):
        try:
            return reverse(f"admin:core_{model}_{action}", args=args)
        except NoReverseMatch:
            self.fail(f"{model} has no admin {action} page")

    def enrolled_student(self, login="octocat", course=None):
        student = make_user(login)
        Enrollment.objects.create(user=student, course=course or self.course, role="student")
        return student

    def assert_saved(self, response):
        if response.status_code != 302:
            errors = response.context["errors"] if response.context else None
            self.fail(f"Expected the page to save, got {response.status_code}: {errors}")

    def assert_rejected(self, response, expected_text):
        """The form came back with an error that explains the problem."""
        self.assertEqual(response.status_code, 200, "Expected the form to come back with an error")
        errors = " ".join(str(error) for error in response.context["errors"]).lower()
        self.assertIn(expected_text.lower(), errors)


class CreateAndEditTests(AdminTestCase):
    """I can create and edit courses, enrollments, projects, teams, memberships and repos."""

    def test_every_setup_model_has_list_and_add_pages(self):
        for model in ("course", "enrollment", "project", "team", "teammembership", "repo"):
            for action in ("changelist", "add"):
                with self.subTest(model=model, action=action):
                    response = self.client.get(self.admin_url(model, action))
                    self.assertEqual(response.status_code, 200)

    def test_create_a_course(self):
        response = self.client.post(self.admin_url("course", "add"), {"name": "CS 4501, Fall 2026"})
        self.assert_saved(response)
        self.assertTrue(Course.objects.filter(name="CS 4501, Fall 2026").exists())

    def test_edit_a_course(self):
        response = self.client.post(
            self.admin_url("course", "change", self.course.pk), {"name": "Renamed"}
        )
        self.assert_saved(response)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Renamed")

    def test_create_an_enrollment(self):
        user = make_user("octocat")
        response = self.client.post(
            self.admin_url("enrollment", "add"),
            {"user": user.pk, "course": self.course.pk, "role": "ta"},
        )
        self.assert_saved(response)
        self.assertEqual(Enrollment.objects.get(user=user, course=self.course).role, "ta")

    def test_edit_an_enrollment(self):
        student = self.enrolled_student()
        enrollment = Enrollment.objects.get(user=student)
        response = self.client.post(
            self.admin_url("enrollment", "change", enrollment.pk),
            {"user": student.pk, "course": self.course.pk, "role": "ta"},
        )
        self.assert_saved(response)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.role, "ta")

    def test_create_a_project(self):
        response = self.client.post(
            self.admin_url("project", "add"), {"course": self.course.pk, "name": "Project 2"}
        )
        self.assert_saved(response)
        self.assertTrue(Project.objects.filter(course=self.course, name="Project 2").exists())

    def test_edit_a_project(self):
        response = self.client.post(
            self.admin_url("project", "change", self.project.pk),
            {"course": self.course.pk, "name": "Renamed"},
        )
        self.assert_saved(response)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Renamed")

    def test_create_a_team(self):
        response = self.client.post(
            self.admin_url("team", "add"), team_form(self.project, name="Team B")
        )
        self.assert_saved(response)
        self.assertTrue(Team.objects.filter(project=self.project, name="Team B").exists())

    def test_edit_a_team(self):
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.project, name="Renamed"),
        )
        self.assert_saved(response)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Renamed")

    def test_create_a_membership(self):
        student = self.enrolled_student()
        response = self.client.post(
            self.admin_url("teammembership", "add"), {"user": student.pk, "team": self.team.pk}
        )
        self.assert_saved(response)
        self.assertTrue(TeamMembership.objects.filter(user=student, team=self.team).exists())

    def test_edit_a_membership_to_move_a_student_within_the_project(self):
        student = self.enrolled_student()
        membership = TeamMembership.objects.create(user=student, team=self.team)
        team_b = Team.objects.create(project=self.project, name="Team B")
        response = self.client.post(
            self.admin_url("teammembership", "change", membership.pk),
            {"user": student.pk, "team": team_b.pk},
        )
        self.assert_saved(response)
        membership.refresh_from_db()
        self.assertEqual(membership.team, team_b)

    def test_create_a_repo(self):
        response = self.client.post(
            self.admin_url("repo", "add"),
            {"team": self.team.pk, "url": "https://github.com/acme/app"},
        )
        self.assert_saved(response)
        self.assertEqual(Repo.objects.get(team=self.team).url, "https://github.com/acme/app")

    def test_edit_a_repo_updates_its_full_name(self):
        repo = Repo.objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        response = self.client.post(
            self.admin_url("repo", "change", repo.pk),
            {"team": self.team.pk, "url": "https://github.com/acme/renamed"},
        )
        self.assert_saved(response)
        repo.refresh_from_db()
        self.assertEqual(repo.full_name, "acme/renamed")


class TeamPageTests(AdminTestCase):
    """I can edit a team's members and repos on one page."""

    def test_team_page_shows_members_and_repos(self):
        response = self.client.get(self.admin_url("team", "change", self.team.pk))
        self.assertEqual(response.status_code, 200)
        inline_models = {inline.opts.model for inline in response.context["inline_admin_formsets"]}
        self.assertEqual(inline_models, {TeamMembership, Repo})

    def test_add_a_member_and_a_repo_together(self):
        student = self.enrolled_student()
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(
                self.project,
                members=[{"user": student.pk}],
                repos=[{"url": "https://github.com/acme/app"}],
            ),
        )
        self.assert_saved(response)
        self.assertTrue(TeamMembership.objects.filter(user=student, team=self.team).exists())
        self.assertEqual(Repo.objects.get(team=self.team).full_name, "acme/app")

    def test_create_a_team_with_its_members_and_repos(self):
        student = self.enrolled_student()
        response = self.client.post(
            self.admin_url("team", "add"),
            team_form(
                self.project,
                name="Team B",
                members=[{"user": student.pk}],
                repos=[{"url": "https://github.com/acme/app"}],
            ),
        )
        self.assert_saved(response)
        team = Team.objects.get(name="Team B")
        self.assertEqual(team.memberships.get().user, student)
        self.assertEqual(team.repos.get().full_name, "acme/app")

    def test_remove_a_member_and_a_repo(self):
        student = self.enrolled_student()
        membership = TeamMembership.objects.create(user=student, team=self.team)
        repo = Repo.objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(
                self.project,
                members=[{"id": membership.pk, "user": student.pk, "DELETE": "on"}],
                repos=[{"id": repo.pk, "url": repo.url, "DELETE": "on"}],
                initial_members=1,
                initial_repos=1,
            ),
        )
        self.assert_saved(response)
        self.assertFalse(self.team.memberships.exists())
        self.assertFalse(self.team.repos.exists())

    def test_saving_an_unchanged_team_keeps_its_members_and_repos(self):
        student = self.enrolled_student()
        membership = TeamMembership.objects.create(user=student, team=self.team)
        repo = Repo.objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(
                self.project,
                members=[{"id": membership.pk, "user": student.pk}],
                repos=[{"id": repo.pk, "url": repo.url}],
                initial_members=1,
                initial_repos=1,
            ),
        )
        self.assert_saved(response)
        self.assertEqual(self.team.memberships.get(), membership)
        self.assertEqual(self.team.repos.get().full_name, "acme/app")


class FindUsersTests(AdminTestCase):
    """I can find users by GitHub login."""

    def setUp(self):
        super().setUp()
        # The username differs, so these can only match on the GitHub login.
        self.match = make_user("octo-hub", username="student1")
        self.other = make_user("someone-else", username="student2")

    def test_user_list_searches_by_github_login(self):
        response = self.client.get(self.admin_url("user", "changelist"), {"q": "OCTO"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].queryset), [self.match])

    def test_user_list_shows_the_github_login(self):
        response = self.client.get(self.admin_url("user", "changelist"))
        self.assertContains(response, "octo-hub")

    def test_team_page_member_picker_is_a_search_box(self):
        response = self.client.get(self.admin_url("team", "change", self.team.pk))
        self.assertContains(response, "admin-autocomplete")

    def test_team_page_member_picker_searches_by_github_login(self):
        response = self.client.get(
            reverse("admin:autocomplete"),
            {
                "app_label": "core",
                "model_name": "teammembership",
                "field_name": "user",
                "term": "octo",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["id"] for result in response.json()["results"]], [str(self.match.pk)]
        )

    def user_form(self, github_login):
        return {
            "username": "new-student",
            "github_login": github_login,
            "usable_password": "false",
            "password1": "",
            "password2": "",
        }

    def test_create_a_user_with_a_github_login(self):
        response = self.client.post(self.admin_url("user", "add"), self.user_form("new-login"))
        self.assert_saved(response)
        self.assertEqual(User.objects.get(username="new-student").github_login, "new-login")

    def test_creating_a_user_requires_a_github_login(self):
        response = self.client.post(self.admin_url("user", "add"), self.user_form(""))
        self.assert_rejected(response, "required")
        self.assertFalse(User.objects.filter(username="new-student").exists())

    def test_user_page_can_edit_the_github_login(self):
        response = self.client.get(self.admin_url("user", "change", self.match.pk))
        self.assertContains(response, 'name="github_login"')


class RepoUrlTests(AdminTestCase):
    """A non-GitHub URL is rejected; every spelling of a GitHub URL is the same repo."""

    SAME_REPO_URLS = [
        "https://github.com/acme/app",
        "https://github.com/acme/app/",
        "https://github.com/acme/app.git",
        "https://github.com/acme/app.git/",
    ]

    REJECTED_URLS = [
        # Other hosts, including ones that only look like GitHub.
        "https://gitlab.com/acme/app",
        "https://bitbucket.org/acme/app",
        "https://notgithub.com/acme/app",
        "https://github.com.evil.example/acme/app",
        "https://evil.example/github.com/acme/app",
        "https://user:secret@github.com/acme/app",
        "https://github.com:8443/acme/app",
        # Not HTTPS.
        "http://github.com/acme/app",
        "git@github.com:acme/app.git",
        "ssh://git@github.com/acme/app.git",
        # GitHub, but not exactly one repo.
        "https://github.com/",
        "https://github.com/acme",
        "https://github.com/acme/.git",
        "https://github.com/acme/app/issues",
        "https://github.com/acme/app/tree/main",
        "https://github.com/acme/app?tab=readme",
        "https://github.com/acme/app#readme",
        "https://github.com/acme/my%20app",
        # Not a URL.
        "acme/app",
        "not a url",
    ]

    def add_repo(self, url, **extra):
        return self.client.post(
            self.admin_url("repo", "add"), {"team": self.team.pk, "url": url, **extra}
        )

    def test_each_spelling_gives_the_same_full_name(self):
        for url in self.SAME_REPO_URLS:
            with self.subTest(url=url):
                Repo.objects.all().delete()
                self.assert_saved(self.add_repo(url))
                self.assertEqual(Repo.objects.get(team=self.team).full_name, "acme/app")

    def test_each_spelling_is_a_duplicate_of_an_existing_repo(self):
        Repo.objects.create(team=self.team, url="https://github.com/acme/app", full_name="acme/app")
        for url in [*self.SAME_REPO_URLS, "https://github.com/ACME/App.git"]:
            with self.subTest(url=url):
                self.assert_rejected(self.add_repo(url), "already has this repo")
                self.assertEqual(Repo.objects.filter(team=self.team).count(), 1)

    def test_dots_inside_a_repo_name_are_kept(self):
        cases = {
            "https://github.com/acme/acme.github.io": "acme/acme.github.io",
            "https://github.com/acme/my.app.git": "acme/my.app",
        }
        for url, full_name in cases.items():
            with self.subTest(url=url):
                Repo.objects.all().delete()
                self.assert_saved(self.add_repo(url))
                self.assertEqual(Repo.objects.get(team=self.team).full_name, full_name)

    def test_full_name_comes_from_the_url_and_not_from_the_form(self):
        self.assert_saved(self.add_repo("https://github.com/acme/app", full_name="evil/other"))
        self.assertEqual(Repo.objects.get(team=self.team).full_name, "acme/app")

    def test_other_urls_are_rejected_with_a_clear_error(self):
        for url in self.REJECTED_URLS:
            with self.subTest(url=url):
                self.assert_rejected(self.add_repo(url), "github")
                self.assertFalse(Repo.objects.exists())

    def test_team_page_rejects_other_urls_with_a_clear_error(self):
        for url in self.REJECTED_URLS:
            with self.subTest(url=url):
                response = self.client.post(
                    self.admin_url("team", "change", self.team.pk),
                    team_form(self.project, repos=[{"url": url}]),
                )
                self.assert_rejected(response, "github")
                self.assertFalse(Repo.objects.exists())

    def test_team_page_fills_in_the_full_name_for_each_spelling(self):
        for url in self.SAME_REPO_URLS:
            with self.subTest(url=url):
                Repo.objects.all().delete()
                response = self.client.post(
                    self.admin_url("team", "change", self.team.pk),
                    team_form(self.project, repos=[{"url": url}]),
                )
                self.assert_saved(response)
                self.assertEqual(self.team.repos.get().full_name, "acme/app")

    def test_team_page_rejects_a_repo_the_team_already_has(self):
        Repo.objects.create(team=self.team, url="https://github.com/acme/app", full_name="acme/app")
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.project, repos=[{"url": "https://github.com/acme/app.git"}]),
        )
        self.assert_rejected(response, "already has this repo")
        self.assertEqual(self.team.repos.count(), 1)

    def test_team_page_rejects_two_spellings_of_a_repo_in_one_save(self):
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(
                self.project,
                repos=[
                    {"url": "https://github.com/acme/app"},
                    {"url": "https://github.com/ACME/app.git/"},
                ],
            ),
        )
        self.assert_rejected(response, "already has this repo")
        self.assertFalse(self.team.repos.exists())

    def test_another_team_can_use_the_same_repo(self):
        Repo.objects.create(team=self.team, url="https://github.com/acme/app", full_name="acme/app")
        team_b = Team.objects.create(project=self.project, name="Team B")
        response = self.client.post(
            self.admin_url("repo", "add"),
            {"team": team_b.pk, "url": "https://github.com/acme/app"},
        )
        self.assert_saved(response)
        self.assertEqual(team_b.repos.get().full_name, "acme/app")


class RepoWithArtifactsTests(AdminTestCase):
    """Artifact IDs start with full_name, so it can't change once a repo has artifacts."""

    NEW_URL = "https://github.com/acme/other"

    def setUp(self):
        super().setUp()
        self.repo = Repo.objects.create(
            team=self.team, url="https://github.com/acme/app", full_name="acme/app"
        )
        Artifact.objects.create(
            repo=self.repo,
            artifact_id="acme/app:pr:12",
            type="pr",
            url="https://github.com/acme/app/pull/12",
            timestamp=datetime(2026, 10, 26, 12, tzinfo=UTC),
        )

    def post_repo_page(self, url):
        return self.client.post(
            self.admin_url("repo", "change", self.repo.pk), {"team": self.team.pk, "url": url}
        )

    def post_team_page(self, url):
        return self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.project, repos=[{"id": self.repo.pk, "url": url}], initial_repos=1),
        )

    def assert_repo_unchanged(self):
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.url, "https://github.com/acme/app")
        self.assertEqual(self.repo.full_name, "acme/app")

    def test_repo_page_rejects_pointing_it_at_a_different_repo(self):
        self.assert_rejected(self.post_repo_page(self.NEW_URL), "artifacts")
        self.assert_repo_unchanged()

    def test_team_page_rejects_pointing_it_at_a_different_repo(self):
        self.assert_rejected(self.post_team_page(self.NEW_URL), "artifacts")
        self.assert_repo_unchanged()

    def test_another_spelling_of_the_same_repo_keeps_the_full_name(self):
        # The artifact check compares IDs to full_name exactly, so even a change of case
        # would leave existing artifact IDs with the wrong prefix.
        for url in ("https://github.com/acme/app.git/", "https://github.com/ACME/App"):
            with self.subTest(url=url):
                self.assert_saved(self.post_repo_page(url))
                self.repo.refresh_from_db()
                self.assertEqual(self.repo.full_name, "acme/app")

    def test_a_repo_with_no_artifacts_can_still_change(self):
        self.repo.artifacts.all().delete()
        self.assert_saved(self.post_repo_page(self.NEW_URL))
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.full_name, "acme/other")


class MembershipRuleFixtures(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.student = self.enrolled_student()
        self.team_b = Team.objects.create(project=self.project, name="Team B")
        self.later_project = Project.objects.create(course=self.course, name="Project 2")
        self.later_team = Team.objects.create(project=self.later_project, name="Later Team")
        self.other_course = Course.objects.create(name="CS 4501, Fall 2026")
        self.other_project = Project.objects.create(course=self.other_course, name="Project 1")
        self.other_team = Team.objects.create(project=self.other_project, name="Other Team")


class MembershipValidationTests(MembershipRuleFixtures):
    """The rules hold for full_clean(), which every admin form runs."""

    def test_accepts_a_student_enrolled_in_the_course(self):
        TeamMembership(user=self.student, team=self.team).full_clean()

    def test_rejects_a_user_with_no_enrollment(self):
        membership = TeamMembership(user=make_user("hubot"), team=self.team)
        with self.assertRaisesMessage(ValidationError, "enrolled"):
            membership.full_clean()

    def test_rejects_a_student_enrolled_only_in_another_course(self):
        membership = TeamMembership(user=self.student, team=self.other_team)
        with self.assertRaisesMessage(ValidationError, "enrolled"):
            membership.full_clean()

    def test_rejects_a_second_team_in_the_same_project(self):
        TeamMembership.objects.create(user=self.student, team=self.team)
        membership = TeamMembership(user=self.student, team=self.team_b)
        # Naming the existing team tells the instructor where to go to fix it.
        with self.assertRaisesMessage(ValidationError, "Team A"):
            membership.full_clean()

    def test_rejects_course_staff(self):
        for role in ("ta", "instructor"):
            with self.subTest(role=role):
                staff = make_user(f"staff-{role}")
                Enrollment.objects.create(user=staff, course=self.course, role=role)
                with self.assertRaisesMessage(ValidationError, "student"):
                    TeamMembership(user=staff, team=self.team).full_clean()

    def test_accepts_a_student_who_is_a_ta_in_another_course(self):
        Enrollment.objects.create(user=self.student, course=self.other_course, role="ta")
        TeamMembership(user=self.student, team=self.team).full_clean()

    def test_accepts_a_team_in_another_project_of_the_course(self):
        TeamMembership.objects.create(user=self.student, team=self.team)
        TeamMembership(user=self.student, team=self.later_team).full_clean()

    def test_accepts_a_membership_rechecked_against_itself(self):
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        membership.full_clean()


class MembershipAdminTests(MembershipRuleFixtures):
    """I can't put a student on two teams in a project, or on a team outside their course."""

    def test_membership_page_rejects_a_second_team_in_the_same_project(self):
        TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("teammembership", "add"),
            {"user": self.student.pk, "team": self.team_b.pk},
        )
        self.assert_rejected(response, "Team A")
        self.assertEqual(self.student.team_memberships.count(), 1)

    def test_membership_page_rejects_an_unenrolled_user(self):
        response = self.client.post(
            self.admin_url("teammembership", "add"),
            {"user": self.student.pk, "team": self.other_team.pk},
        )
        self.assert_rejected(response, "enrolled")
        self.assertFalse(self.student.team_memberships.exists())

    def test_membership_page_rejects_a_ta(self):
        ta = make_user("a-ta")
        Enrollment.objects.create(user=ta, course=self.course, role="ta")
        response = self.client.post(
            self.admin_url("teammembership", "add"), {"user": ta.pk, "team": self.team.pk}
        )
        self.assert_rejected(response, "student")
        self.assertFalse(ta.team_memberships.exists())

    def test_team_page_rejects_a_ta(self):
        ta = make_user("a-ta")
        Enrollment.objects.create(user=ta, course=self.course, role="ta")
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.project, members=[{"user": ta.pk}]),
        )
        self.assert_rejected(response, "student")
        self.assertFalse(ta.team_memberships.exists())

    def test_moving_a_team_rejects_a_member_who_is_a_ta_in_the_new_course(self):
        Enrollment.objects.create(user=self.student, course=self.other_course, role="ta")
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.other_project, **self.existing_member_rows(membership)),
        )
        self.assert_rejected(response, "student")
        self.team.refresh_from_db()
        self.assertEqual(self.team.project, self.project)

    def test_enrollment_page_rejects_changes_that_strand_a_team_member(self):
        # Otherwise a TA, or someone outside the course, could stay on a team this way.
        TeamMembership.objects.create(user=self.student, team=self.team)
        enrollment = Enrollment.objects.get(user=self.student, course=self.course)
        changes = {
            "role": {"course": self.course.pk, "role": "ta"},
            "course": {"course": self.other_course.pk, "role": "student"},
        }
        for change, form in changes.items():
            with self.subTest(change=change):
                response = self.client.post(
                    self.admin_url("enrollment", "change", enrollment.pk),
                    {"user": self.student.pk, **form},
                )
                self.assert_rejected(response, "Team A")
                enrollment.refresh_from_db()
                self.assertEqual(enrollment.role, "student")
                self.assertEqual(enrollment.course, self.course)

    def test_enrollment_page_allows_changing_a_student_with_no_team(self):
        enrollment = Enrollment.objects.get(user=self.student, course=self.course)
        response = self.client.post(
            self.admin_url("enrollment", "change", enrollment.pk),
            {"user": self.student.pk, "course": self.course.pk, "role": "ta"},
        )
        self.assert_saved(response)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.role, "ta")

    def test_membership_page_rejects_moving_a_student_out_of_their_course(self):
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("teammembership", "change", membership.pk),
            {"user": self.student.pk, "team": self.other_team.pk},
        )
        self.assert_rejected(response, "enrolled")
        membership.refresh_from_db()
        self.assertEqual(membership.team, self.team)

    def test_team_page_rejects_a_student_already_on_a_team_in_the_project(self):
        TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "change", self.team_b.pk),
            team_form(self.project, name="Team B", members=[{"user": self.student.pk}]),
        )
        self.assert_rejected(response, "Team A")
        self.assertFalse(self.team_b.memberships.exists())

    def test_team_page_rejects_an_unenrolled_user(self):
        response = self.client.post(
            self.admin_url("team", "change", self.other_team.pk),
            team_form(self.other_project, name="Other Team", members=[{"user": self.student.pk}]),
        )
        self.assert_rejected(response, "enrolled")
        self.assertFalse(self.other_team.memberships.exists())

    def test_new_team_page_rejects_a_student_already_on_a_team_in_the_project(self):
        TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "add"),
            team_form(self.project, name="Team C", members=[{"user": self.student.pk}]),
        )
        self.assert_rejected(response, "Team A")
        self.assertFalse(Team.objects.filter(name="Team C").exists())

    def test_new_team_page_rejects_an_unenrolled_user(self):
        response = self.client.post(
            self.admin_url("team", "add"),
            team_form(self.other_project, name="Team C", members=[{"user": self.student.pk}]),
        )
        self.assert_rejected(response, "enrolled")
        self.assertFalse(Team.objects.filter(name="Team C").exists())

    def test_team_page_rejects_the_same_student_twice_in_one_save(self):
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(
                self.project, members=[{"user": self.student.pk}, {"user": self.student.pk}]
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["errors"])
        self.assertFalse(self.team.memberships.exists())

    def existing_member_rows(self, membership):
        return {"members": [{"id": membership.pk, "user": self.student.pk}], "initial_members": 1}

    def test_moving_a_team_rejects_a_member_not_enrolled_in_the_new_course(self):
        # The member's row is unchanged, so only the team's project puts them out of bounds.
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.other_project, **self.existing_member_rows(membership)),
        )
        self.assert_rejected(response, "enrolled")
        self.team.refresh_from_db()
        self.assertEqual(self.team.project, self.project)

    def test_moving_a_team_rejects_a_member_already_on_a_team_in_the_new_project(self):
        TeamMembership.objects.create(user=self.student, team=self.later_team)
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.later_project, **self.existing_member_rows(membership)),
        )
        self.assert_rejected(response, "Later Team")
        self.team.refresh_from_db()
        self.assertEqual(self.team.project, self.project)

    def test_moving_a_team_within_the_course_keeps_its_members(self):
        membership = TeamMembership.objects.create(user=self.student, team=self.team)
        response = self.client.post(
            self.admin_url("team", "change", self.team.pk),
            team_form(self.later_project, **self.existing_member_rows(membership)),
        )
        self.assert_saved(response)
        self.team.refresh_from_db()
        self.assertEqual(self.team.project, self.later_project)
        self.assertEqual(self.team.memberships.get(), membership)
