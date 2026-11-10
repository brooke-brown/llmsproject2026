"""Tests for US-44 (#127): the pages for generating temporary passwords. Made-up students only."""

import csv
import io

from django.test import Client
from django.urls import reverse

from core.models import Enrollment
from core.tests.test_temp_passwords import PasswordFixture

from .test_course_overview import enroll, make_user
from .test_generate_summary import page_messages


def passwords_url(project_id):
    return reverse("dashboard:passwords", args=[project_id])


def generate_url(project_id):
    return reverse("dashboard:passwords_generate", args=[project_id])


def rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


class PasswordPageTests(PasswordFixture):
    def test_the_edit_project_teams_panel_has_a_button(self):
        page = self.client.get(reverse("dashboard:project_setup", args=[self.project.id]))
        self.assertContains(page, "Generate student passwords")
        self.assertContains(page, passwords_url(self.project.id))

    def test_the_page_says_how_many_students_will_get_one(self):
        page = self.client.get(passwords_url(self.project.id))
        self.assertContains(page, "2 students will get a temporary password")

    def test_it_warns_that_outstanding_temporary_passwords_are_replaced(self):
        page = self.client.get(passwords_url(self.project.id))
        self.assertContains(page, "replaced")

    def test_it_says_students_with_their_own_password_are_left_alone(self):
        page = self.client.get(passwords_url(self.project.id))
        self.assertContains(page, "left alone")

    def test_it_tells_the_instructor_to_share_privately_and_delete_the_file(self):
        page = self.client.get(passwords_url(self.project.id))
        self.assertContains(page, "privately")
        self.assertContains(page, "delete")

    def test_with_nobody_to_give_a_password_to_there_is_no_button(self):
        for user in (self.new, self.waiting):
            user.set_password("My-own-choice-1")
            user.must_change_password = False
            user.save()
        page = self.client.get(passwords_url(self.project.id))
        self.assertContains(page, "No students need a temporary password")
        self.assertNotContains(page, generate_url(self.project.id))


class GenerateDownloadTests(PasswordFixture):
    def test_confirming_downloads_a_csv_with_each_students_password(self):
        response = self.client.post(generate_url(self.project.id))
        self.assertEqual(response["Content-Type"].split(";")[0], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        body = {row["github_login"]: row for row in rows(response)}
        self.assertEqual(set(body), {"new-student", "waiting-student"})
        self.new.refresh_from_db()
        self.assertTrue(self.new.check_password(body["new-student"]["temporary_password"]))

    def test_the_response_must_not_be_cached(self):
        response = self.client.post(generate_url(self.project.id))
        self.assertIn("no-store", response["Cache-Control"])

    def test_the_passwords_can_not_be_downloaded_again(self):
        first = rows(self.client.post(generate_url(self.project.id)))
        self.assertEqual(self.client.get(generate_url(self.project.id)).status_code, 405)
        page = self.client.get(passwords_url(self.project.id))
        for row in first:
            self.assertNotContains(page, row["temporary_password"])

    def test_generating_again_gives_new_passwords(self):
        first = {r["github_login"]: r["temporary_password"] for r in rows(self.client.post(generate_url(self.project.id)))}
        second = {r["github_login"]: r["temporary_password"] for r in rows(self.client.post(generate_url(self.project.id)))}
        self.assertNotEqual(first["new-student"], second["new-student"])

    def test_a_blocked_account_appears_with_a_blank_password_and_a_note(self):
        ta = self.add_student("busy-student", self.team)
        enroll(ta, self.other_course, Enrollment.Role.TA)
        body = {r["github_login"]: r for r in rows(self.client.post(generate_url(self.project.id)))}
        self.assertEqual(body["busy-student"]["temporary_password"], "")
        self.assertIn("admin", body["busy-student"]["note"])

    def test_with_nobody_to_give_a_password_to_it_redirects_with_a_message(self):
        for user in (self.new, self.waiting):
            user.set_password("My-own-choice-1")
            user.must_change_password = False
            user.save()
        response = self.client.post(generate_url(self.project.id), follow=True)
        self.assertIn("No students need", page_messages(response))


class AccessTests(PasswordFixture):
    def test_tas_students_and_others_get_a_403(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Enrollment.Role.TA)
        for user in (ta, self.grace, make_user("sample-outsider")):
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(passwords_url(self.project.id)).status_code, 403, user)
            self.assertEqual(client.post(generate_url(self.project.id)).status_code, 403, user)
        self.new.refresh_from_db()
        self.assertFalse(self.new.has_usable_password())

    def test_a_missing_project_is_a_404(self):
        self.assertEqual(self.client.get(passwords_url(99999)).status_code, 404)
        self.assertEqual(self.client.post(generate_url(99999)).status_code, 404)

    def test_generating_needs_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        self.assertEqual(client.post(generate_url(self.project.id)).status_code, 403)
        self.new.refresh_from_db()
        self.assertFalse(self.new.has_usable_password())
