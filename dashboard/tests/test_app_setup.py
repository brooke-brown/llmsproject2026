"""Tests for T-10 (#41): the dashboard app, base template and login.

URLs are reversed per test rather than at module level, so each criterion passes
or fails on its own instead of the whole module failing on a single
NoReverseMatch.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

PASSWORD = "not-a-real-password"
PICO_CDN = "https://cdn.jsdelivr.net/npm/@picocss/pico@"


def make_user(login="sample-instructor"):
    return get_user_model().objects.create_user(
        username=login, password=PASSWORD, github_login=login
    )


def home_url():
    return reverse("dashboard:home")


def login_url():
    return reverse("login")


class AppSetupTests(SimpleTestCase):
    def test_dashboard_is_installed(self):
        self.assertIn("dashboard", settings.INSTALLED_APPS)

    def test_login_settings_point_at_login_and_dashboard_home(self):
        self.assertEqual(resolve_url(settings.LOGIN_URL), login_url())
        self.assertEqual(resolve_url(settings.LOGIN_REDIRECT_URL), home_url())


class BaseTemplateTests(TestCase):
    def test_login_page_loads_pico_from_cdn(self):
        response = self.client.get(login_url())

        self.assertTemplateUsed(response, "dashboard/base.html")
        self.assertContains(response, PICO_CDN)

    def test_dashboard_page_uses_base_template(self):
        self.client.force_login(make_user())

        response = self.client.get(home_url())

        self.assertTemplateUsed(response, "dashboard/base.html")
        self.assertContains(response, PICO_CDN)

    def test_nav_shows_logged_in_user(self):
        self.client.force_login(make_user("sample-instructor"))

        response = self.client.get(home_url())

        self.assertContains(response, "sample-instructor")

    def test_nav_has_logout_button(self):
        self.client.force_login(make_user())

        response = self.client.get(home_url())

        # Django's LogoutView only accepts POST, so logout must be a form, not a link.
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, 'method="post"')

    def test_nav_has_no_logout_button_when_logged_out(self):
        response = self.client.get(login_url())

        self.assertNotContains(response, f'action="{reverse("logout")}"')


class LoginTests(TestCase):
    def test_login_page_uses_registration_template(self):
        response = self.client.get(login_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")

    def test_logged_out_visitor_is_redirected_to_login(self):
        response = self.client.get(home_url())

        self.assertRedirects(
            response, f"{login_url()}?next={home_url()}", fetch_redirect_response=False
        )

    def test_login_returns_visitor_to_requested_page(self):
        make_user("sample-instructor")

        response = self.client.post(
            f"{login_url()}?next={home_url()}",
            {"username": "sample-instructor", "password": PASSWORD, "next": home_url()},
        )

        self.assertRedirects(response, home_url(), fetch_redirect_response=False)

    def test_login_without_next_lands_on_dashboard_home(self):
        make_user("sample-instructor")

        response = self.client.post(
            login_url(), {"username": "sample-instructor", "password": PASSWORD}
        )

        self.assertRedirects(response, home_url(), fetch_redirect_response=False)

    def test_logged_in_user_can_view_dashboard_home(self):
        self.client.force_login(make_user())

        response = self.client.get(home_url())

        self.assertEqual(response.status_code, 200)

    def test_failed_login_shows_error(self):
        make_user("sample-instructor")

        response = self.client.post(
            login_url(), {"username": "sample-instructor", "password": "wrong-password"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failed_login_does_not_reveal_whether_username_exists(self):
        make_user("sample-instructor")

        wrong_password = self.client.post(
            login_url(), {"username": "sample-instructor", "password": "wrong-password"}
        )
        unknown_user = self.client.post(
            login_url(), {"username": "sample-nobody", "password": "wrong-password"}
        )

        self.assertEqual(
            wrong_password.context["form"].non_field_errors(),
            unknown_user.context["form"].non_field_errors(),
        )

    def test_logout_logs_user_out(self):
        self.client.force_login(make_user())

        self.client.post(reverse("logout"))

        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.get(home_url())
        self.assertEqual(response.status_code, 302)
