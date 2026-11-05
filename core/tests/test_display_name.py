"""Tests for US-23 (#87): show students by name, without their GitHub username repeated.

Covers the parts that live in `core`: the `User.display_name` fallback, names on seeded
accounts, and name fields on the admin "Add user" form. The pages are tested in
`dashboard/tests/test_display_name.py`.
"""

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Enrollment

User = get_user_model()


def unsaved_user(first_name="", last_name="", github_login="sample-ada"):
    return User(username=github_login, github_login=github_login,
                first_name=first_name, last_name=last_name)


class DisplayNameTests(SimpleTestCase):
    def test_a_full_name_is_shown_as_is(self):
        self.assertEqual(unsaved_user("Ada", "Sample").display_name, "Ada Sample")

    def test_no_name_falls_back_to_the_github_login(self):
        self.assertEqual(unsaved_user().display_name, "sample-ada")

    def test_only_a_first_name_is_shown_not_the_login(self):
        self.assertEqual(unsaved_user(first_name="Ada").display_name, "Ada")

    def test_only_a_last_name_is_shown_not_the_login(self):
        self.assertEqual(unsaved_user(last_name="Sample").display_name, "Sample")

    def test_blank_spaces_count_as_no_name(self):
        self.assertEqual(unsaved_user(first_name=" ", last_name=" ").display_name, "sample-ada")


def seed():
    with mock.patch.dict(os.environ, {"SEED_PASSWORD": "test-only-seed-password"}):
        call_command("seed", stdout=mock.MagicMock(), stderr=mock.MagicMock())


class SeededNamesTests(TestCase):
    def test_everyone_the_seed_command_creates_has_a_full_name(self):
        seed()

        self.assertTrue(User.objects.exists())
        for user in User.objects.all():
            self.assertTrue(user.first_name, f"{user.github_login} has no first name")
            self.assertTrue(user.last_name, f"{user.github_login} has no last name")

    def test_accounts_seeded_before_names_existed_get_names_on_the_next_run(self):
        seed()
        User.objects.update(first_name="", last_name="")

        seed()

        for user in User.objects.all():
            self.assertTrue(user.get_full_name(), f"{user.github_login} still has no name")

    def test_a_name_edited_in_admin_survives_a_rerun(self):
        seed()
        user = User.objects.filter(enrollments__role=Enrollment.Role.STUDENT).first()
        User.objects.filter(pk=user.pk).update(first_name="Edited", last_name="Name")

        seed()

        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Edited Name")

    def test_a_one_part_name_edited_in_admin_survives_a_rerun(self):
        seed()
        user = User.objects.filter(enrollments__role=Enrollment.Role.STUDENT).first()
        User.objects.filter(pk=user.pk).update(first_name="Edited", last_name="")

        seed()

        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Edited")

    def test_a_name_of_only_spaces_counts_as_blank(self):
        seed()
        user = User.objects.filter(enrollments__role=Enrollment.Role.STUDENT).first()
        User.objects.filter(pk=user.pk).update(first_name=" ", last_name=" ")

        seed()

        user.refresh_from_db()
        self.assertTrue(user.get_full_name())


class AdminAddUserTests(TestCase):
    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser(
                username="admin",
                password="not-a-real-password",
                email="admin@example.com",
                github_login="course-admin",
            )
        )

    def form(self, **fields):
        return {
            "username": "new-student",
            "github_login": "new-login",
            "usable_password": "false",
            "password1": "",
            "password2": "",
            **fields,
        }

    def test_the_add_user_form_has_first_and_last_name_fields(self):
        response = self.client.get(reverse("admin:core_user_add"))

        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')

    def test_names_entered_on_the_add_user_form_are_saved(self):
        response = self.client.post(
            reverse("admin:core_user_add"), self.form(first_name="Ada", last_name="Sample")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username="new-student").display_name, "Ada Sample")
