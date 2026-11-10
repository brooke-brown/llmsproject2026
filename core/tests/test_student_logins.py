"""Tests for T-14 (#102): the account side of student logins.

Covers `User.must_change_password`, `set_temporary_password` and the forced password change.
The access helpers and where people land after logging in are in
`dashboard/tests/test_student_access.py`.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core import services
from core.models import Course, Enrollment, Project, Team, TeamMembership

Role = Enrollment.Role
User = get_user_model()

OLD_PASSWORD = "not-a-real-password"
NEW_PASSWORD = "a-fresh-Passphrase-42"
TEMPORARY_PASSWORD = "river-Lantern-9317"
# What a browser sends when opening a page. A plain fetch() sends */* instead.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def make_user(login, **extra):
    return User.objects.create_user(
        username=login, password=OLD_PASSWORD, github_login=login, **extra
    )


def password_change_url():
    return reverse("password_change")


class MustChangePasswordFieldTests(TestCase):
    def test_defaults_to_false(self):
        self.assertIs(make_user("sample-casey").must_change_password, False)

    def test_is_stored(self):
        user = make_user("sample-casey", must_change_password=True)
        user.refresh_from_db()
        self.assertIs(user.must_change_password, True)


class SetTemporaryPasswordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.student = make_user("sample-casey")
        Enrollment.objects.create(user=cls.student, course=cls.course, role=Role.STUDENT)

    def assert_unchanged(self, user):
        stored = User.objects.get(pk=user.pk)
        self.assertTrue(stored.check_password(OLD_PASSWORD))
        self.assertIs(stored.must_change_password, False)

    def test_returns_nothing(self):
        self.assertIsNone(services.set_temporary_password(self.student, TEMPORARY_PASSWORD))

    def test_saves_the_password(self):
        services.set_temporary_password(self.student, TEMPORARY_PASSWORD)
        self.assertTrue(User.objects.get(pk=self.student.pk).check_password(TEMPORARY_PASSWORD))

    def test_stores_only_a_hash(self):
        services.set_temporary_password(self.student, TEMPORARY_PASSWORD)
        self.assertNotIn(TEMPORARY_PASSWORD, User.objects.get(pk=self.student.pk).password)

    def test_sets_must_change_password(self):
        services.set_temporary_password(self.student, TEMPORARY_PASSWORD)
        self.assertIs(User.objects.get(pk=self.student.pk).must_change_password, True)

    def test_works_for_a_student_with_no_usable_password(self):
        # How add_student creates accounts today.
        student = User.objects.create_user(username="sample-new", github_login="sample-new")
        services.set_temporary_password(student, TEMPORARY_PASSWORD)
        self.assertTrue(User.objects.get(pk=student.pk).check_password(TEMPORARY_PASSWORD))

    def test_each_password_validator_is_applied(self):
        cases = [
            ("short1x", "too short"),
            ("password123", "too common"),
            ("83749283749", "entirely numeric"),
            # Only caught if the user is passed to the validators.
            ("sample-casey1", "too similar"),
        ]
        for password, rule in cases:
            with self.subTest(rule=rule):
                with self.assertRaises(services.ProjectSetupError) as caught:
                    services.set_temporary_password(self.student, password)
                self.assertIn(rule, str(caught.exception))

    def test_error_names_every_rule_that_failed(self):
        with self.assertRaises(services.ProjectSetupError) as caught:
            services.set_temporary_password(self.student, "48213")
        self.assertIn("too short", str(caught.exception))
        self.assertIn("entirely numeric", str(caught.exception))

    def test_error_does_not_repeat_the_password(self):
        with self.assertRaises(services.ProjectSetupError) as caught:
            services.set_temporary_password(self.student, "short1x")
        self.assertNotIn("short1x", str(caught.exception))

    def test_a_rejected_password_changes_nothing(self):
        with self.assertRaises(services.ProjectSetupError):
            services.set_temporary_password(self.student, "short1x")
        self.assert_unchanged(self.student)

    def test_refuses_staff_accounts(self):
        for flag in ("is_staff", "is_superuser"):
            with self.subTest(flag=flag):
                account = make_user(f"sample-{flag.replace('_', '-')}", **{flag: True})
                Enrollment.objects.create(user=account, course=self.course, role=Role.STUDENT)
                with self.assertRaises(services.ProjectSetupError) as caught:
                    services.set_temporary_password(account, TEMPORARY_PASSWORD)
                self.assertIn("admin", str(caught.exception))
                self.assert_unchanged(account)

    def test_refuses_instructors_and_tas(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                account = make_user(f"sample-{role}")
                Enrollment.objects.create(user=account, course=self.course, role=role)
                with self.assertRaises(services.ProjectSetupError) as caught:
                    services.set_temporary_password(account, TEMPORARY_PASSWORD)
                self.assertIn("admin", str(caught.exception))
                # The role can be in this very course, so the message can't say "another".
                self.assertNotIn("another course", str(caught.exception))
                self.assert_unchanged(account)

    def test_refuses_a_student_who_is_staff_in_another_course(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                account = make_user(f"sample-both-{role}")
                Enrollment.objects.create(user=account, course=self.course, role=Role.STUDENT)
                Enrollment.objects.create(user=account, course=self.other_course, role=role)
                with self.assertRaises(services.ProjectSetupError) as caught:
                    services.set_temporary_password(account, TEMPORARY_PASSWORD)
                self.assertIn("admin", str(caught.exception))
                self.assert_unchanged(account)


class ForcedPasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.student = make_user("sample-casey", must_change_password=True)
        Enrollment.objects.create(user=cls.student, course=cls.course, role=Role.STUDENT)
        TeamMembership.objects.create(user=cls.student, team=cls.team)

    def setUp(self):
        self.client.force_login(self.student)

    def change_password(self, old=OLD_PASSWORD, new=NEW_PASSWORD, confirm=None):
        return self.client.post(
            password_change_url(),
            {"old_password": old, "new_password1": new, "new_password2": confirm or new},
            follow=True,
        )

    def stored_flag(self):
        return User.objects.get(pk=self.student.pk).must_change_password

    def assert_sent_to_password_change(self, response):
        self.assertEqual(response.status_code, 302)
        # Any query string, such as `next`, is the implementation's choice.
        self.assertEqual(response.url.split("?")[0], password_change_url())

    def test_every_page_redirects_to_the_password_change_page(self):
        pages = [
            reverse("dashboard:home"),
            reverse("dashboard:student_home"),
            reverse("dashboard:course_overview", args=[self.course.id]),
            reverse("dashboard:team", args=[self.team.id]),
            reverse("dashboard:project_setup", args=[self.project.id]),
            reverse("login"),
        ]
        for page in pages:
            with self.subTest(page=page):
                self.assert_sent_to_password_change(self.client.get(page))

    def test_admin_redirects_to_the_password_change_page(self):
        self.client.force_login(make_user("sample-staff", is_staff=True, must_change_password=True))
        self.assert_sent_to_password_change(self.client.get(reverse("admin:index")))

    def assert_refused_as_json(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"], "password_change_required")

    def test_a_post_is_refused_as_json_and_does_nothing(self):
        response = self.client.post(
            reverse("dashboard:team_rename", args=[self.team.id]), {"name": "Renamed"}
        )
        self.assert_refused_as_json(response)
        self.assertEqual(Team.objects.get(pk=self.team.pk).name, "Team Alpha")

    def test_a_script_asking_for_data_is_refused_as_json(self):
        poll = reverse("dashboard:course_generating_status", args=[self.course.id])
        for accept in ("*/*", "application/json"):
            with self.subTest(accept=accept):
                self.assert_refused_as_json(self.client.get(poll, headers={"accept": accept}))

    def test_opening_a_page_in_a_browser_is_redirected(self):
        response = self.client.get(
            reverse("dashboard:student_home"), headers={"accept": BROWSER_ACCEPT}
        )
        self.assert_sent_to_password_change(response)

    def team_page_with_query(self):
        return reverse("dashboard:team", args=[self.team.id]) + "?edit_student=1"

    def test_the_redirect_remembers_the_page_and_its_query(self):
        page = self.team_page_with_query()

        response = self.client.get(page)

        self.assertEqual(response.url, f"{password_change_url()}?{urlencode({'next': page})}")

    def test_the_login_page_is_not_remembered(self):
        """Going back to it after the change would show a logged-in user the login form."""
        self.assertEqual(self.client.get(reverse("login")).url, password_change_url())

    def post_change_to(self, url):
        return self.client.post(
            url,
            {
                "old_password": OLD_PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )

    def test_after_changing_it_they_go_to_the_page_they_asked_for(self):
        page = self.team_page_with_query()
        redirected_to = self.client.get(page).url

        response = self.post_change_to(redirected_to)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, page)

    def test_the_form_posts_back_to_the_url_holding_next(self):
        response = self.client.get(self.client.get(self.team_page_with_query()).url)

        self.assertContains(response, '<form method="post">')

    def test_a_next_on_another_site_is_ignored(self):
        for target in ("https://evil.example/", "//evil.example/"):
            with self.subTest(target=target):
                self.student.set_password(OLD_PASSWORD)
                self.student.must_change_password = True
                self.student.save()
                self.client.force_login(self.student)

                response = self.post_change_to(
                    f"{password_change_url()}?{urlencode({'next': target})}"
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("dashboard:home"))

    def test_the_password_change_page_itself_is_not_redirected(self):
        self.assertEqual(self.client.get(password_change_url()).status_code, 200)

    def test_logout_is_not_redirected(self):
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_logout_is_not_redirected(self):
        self.client.force_login(make_user("sample-staff", is_staff=True, must_change_password=True))
        self.client.post(reverse("admin:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_static_files_are_not_redirected(self):
        response = self.client.get(f"{settings.STATIC_URL}dashboard/sample.css")
        self.assertNotEqual(response.status_code, 302)

    def test_logging_in_with_a_temporary_password_goes_to_the_change_page(self):
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {"username": self.student.username, "password": OLD_PASSWORD},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], password_change_url())

    def test_changing_the_password_clears_the_field(self):
        response = self.change_password()
        self.assertEqual(response.status_code, 200)
        self.assertIs(self.stored_flag(), False)
        self.assertTrue(User.objects.get(pk=self.student.pk).check_password(NEW_PASSWORD))

    def test_after_changing_the_password_pages_are_reachable_again(self):
        self.change_password()
        # The student stays logged in, since the change updates the session.
        response = self.client.get(reverse("dashboard:student_home"))
        self.assertEqual(response.status_code, 200)

    def test_a_student_lands_on_the_student_home_after_changing_it(self):
        response = self.change_password()
        self.assertEqual(response.request["PATH_INFO"], reverse("dashboard:student_home"))

    def test_a_wrong_current_password_keeps_the_field(self):
        self.change_password(old="not-the-current-password")
        self.assertIs(self.stored_flag(), True)

    def test_a_new_password_failing_the_validators_keeps_the_field(self):
        self.change_password(new="short1x")
        self.assertIs(self.stored_flag(), True)

    def test_mismatched_new_passwords_keep_the_field(self):
        self.change_password(confirm="a-different-Passphrase-42")
        self.assertIs(self.stored_flag(), True)

    def test_reusing_the_temporary_password_is_refused_and_keeps_the_field(self):
        response = self.change_password(new=OLD_PASSWORD)
        self.assertContains(response, "must be different from your current one")
        self.assertIs(self.stored_flag(), True)


class PasswordChangePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("sample-casey")

    def test_uses_the_site_layout_not_the_admin_template(self):
        self.client.force_login(self.user)
        response = self.client.get(password_change_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/base.html")
        self.assertTemplateNotUsed(response, "admin/base.html")

    def test_the_top_bar_links_to_the_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, f'href="{password_change_url()}"')

    def test_the_top_bar_has_no_link_when_logged_out(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, f'href="{password_change_url()}"')

    def test_the_generating_poll_reloads_when_a_password_change_is_required(self):
        """Reloading lets the middleware redirect the page, remembering where they were."""
        course = Course.objects.create(name="CS 4501, Fall 2026")
        Enrollment.objects.create(user=self.user, course=course, role=Role.INSTRUCTOR)
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:course_overview", args=[course.id]))

        self.assertContains(response, 'status.error === "password_change_required"')

    def test_a_user_not_required_to_change_is_not_redirected_elsewhere(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_requires_login(self):
        response = self.client.get(password_change_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url.split("?")[0], reverse("login"))

    def test_changing_a_password_when_not_required_keeps_the_field_false(self):
        self.client.force_login(self.user)
        self.client.post(
            password_change_url(),
            {
                "old_password": OLD_PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
        )
        user = User.objects.get(pk=self.user.pk)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertIs(user.must_change_password, False)


class MustChangePasswordAdminTests(TestCase):
    def test_the_admin_user_form_has_the_field(self):
        """Refusals send staff accounts to the admin, which must be able to require a change."""
        account = make_user("sample-ta")
        self.client.force_login(User.objects.create_superuser(username="root", github_login="root"))
        response = self.client.get(reverse("admin:core_user_change", args=[account.pk]))
        self.assertContains(response, 'name="must_change_password"')
