"""Tests for US-43 (#126): the pages for importing a project's teams from a CSV.

These drive the real pages. GitHub is mocked, and every student is made up.
"""

import tempfile
import time
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from github import UnknownObjectException

from core.models import Enrollment, Repo, Team, TeamMembership

from .test_course_overview import enroll, make_user
from .test_generate_summary import page_messages
from .test_team_setup import TeamSetupFixture

User = get_user_model()
HEADER = "team,name,github_login,email,repo_url"
GOOD = (
    f"{HEADER}\n"
    "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/gamma\n"
    "Team Gamma,Bo Byte,bo-dev,,\n"
    "Team Alpha,Cy Code,cy-dev,,\n"
)


def import_url(project_id):
    return reverse("dashboard:team_import", args=[project_id])


def confirm_url(project_id):
    return reverse("dashboard:team_import_confirm", args=[project_id])


def template_url(project_id):
    return reverse("dashboard:team_import_template", args=[project_id])


def upload(text=GOOD, name="teams.csv"):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return {"file": SimpleUploadedFile(name, data, content_type="text/csv")}


class ImportPageTests(TeamSetupFixture):
    def test_the_edit_project_teams_panel_has_an_import_button(self):
        page = self.client.get(reverse("dashboard:project_setup", args=[self.project.id]))
        self.assertContains(page, "Import from CSV")
        self.assertContains(page, import_url(self.project.id))

    def test_the_upload_page_shows_the_expected_columns_and_a_template(self):
        page = self.client.get(import_url(self.project.id))
        for column in ("team", "name", "github_login", "email", "repo_url"):
            self.assertContains(page, column)
        self.assertContains(page, template_url(self.project.id))

    def test_the_template_downloads_as_a_csv_with_the_columns(self):
        response = self.client.get(template_url(self.project.id))
        self.assertEqual(response["Content-Type"].split(";")[0], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.decode().startswith(HEADER))

    def test_the_template_has_only_made_up_people(self):
        body = self.client.get(template_url(self.project.id)).content.decode()
        self.assertIn("example", body)


class PreviewPageTests(TeamSetupFixture):
    def preview(self, text=GOOD):
        return self.client.post(import_url(self.project.id), upload(text))

    def test_it_lists_teams_students_and_repos_and_saves_nothing(self):
        before = (Team.objects.count(), User.objects.count(), Repo.objects.count())
        page = self.preview()
        for text in ("Team Gamma", "Ada Lovelace", "ada-dev", "acme/gamma", "Team Alpha", "Cy Code"):
            self.assertContains(page, text)
        self.assertEqual((Team.objects.count(), User.objects.count(), Repo.objects.count()), before)

    def test_it_marks_what_is_new_and_what_is_already_there(self):
        page = self.preview(f"{HEADER}\nTeam Alpha,Grace Example,sample-grace,,\nTeam Gamma,Ada Lovelace,ada-dev,,\n")
        self.assertContains(page, "Already there")
        self.assertContains(page, "New")

    def test_confirm_is_available_when_every_row_is_fine(self):
        page = self.preview()
        self.assertContains(page, "Confirm import")
        self.assertNotRegex(page.content.decode(), r"<button[^>]*\bdisabled")

    def test_while_any_row_has_an_error_confirming_is_disabled_and_the_row_is_named(self):
        page = self.preview(f"{HEADER}\nTeam Gamma,Ada Lovelace,-bad-,,\nTeam Gamma,Bo Byte,bo-dev,,\n")
        self.assertContains(page, "Row 2")
        self.assertRegex(page.content.decode(), r"<button[^>]*\bdisabled")

    def test_the_csv_travels_in_the_confirm_form_and_is_not_saved_to_disk(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            page = self.preview()
            self.assertEqual(list(Path(media).rglob("*")), [])
        self.assertContains(page, 'name="csv"')
        self.assertContains(page, "Ada Lovelace")

    def test_the_confirm_form_is_multipart_so_punctuation_is_not_percent_encoded(self):
        # Form encoding can triple a comma-heavy file, past Django's 2.5 MB limit on a POST.
        self.assertRegex(
            self.preview().content.decode(),
            r'<form action="[^"]*/confirm[^"]*" method="post" enctype="multipart/form-data">',
        )

    def test_a_repo_the_team_already_has_is_not_called_missing(self):
        page = self.preview(f"{HEADER}\nTeam Alpha,Cy Code,cy-dev,,https://github.com/acme/existing\n")
        self.assertContains(page, "acme/existing")
        self.assertNotContains(page, "No repo given")

    def test_an_email_the_account_will_not_take_is_noted(self):
        page = self.preview(f"{HEADER}\nTeam Alpha,Grace Example,sample-grace,grace@new.example,\n")
        self.assertContains(page, "isn&#x27;t saved")

    def test_an_unreadable_repo_shows_no_success_tags_for_its_row(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        page = self.preview(f"{HEADER}\nTeam Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/private\n")
        self.assertContains(page, "Row 2: error", count=2)  # The repo and the student.
        self.assertNotContains(page, ">New<")
        self.assertNotContains(page, "New student")

    def test_a_bad_file_is_rejected_with_a_message_on_the_upload_page(self):
        for data, expected in (
            (b"%PDF-1.7\x00\x01binary", "CSV"),
            (b"team,name\nTeam Gamma,Ada\n", "github_login"),
            (f"{HEADER}\n".encode(), "no rows"),
            (b"a" * (1024 * 1024 + 1), "1 MB"),
        ):
            page = self.client.post(import_url(self.project.id), upload(data))
            self.assertContains(page, expected)
            self.assertContains(page, "Upload")

    def test_no_file_is_a_clear_message(self):
        page = self.client.post(import_url(self.project.id), {})
        self.assertContains(page, "Choose a CSV file")


class ConfirmTests(TeamSetupFixture):
    def confirm(self, text=GOOD, follow=True):
        return self.client.post(confirm_url(self.project.id), {"csv": text}, follow=follow)

    def test_confirming_imports_and_says_how_many_were_added(self):
        response = self.confirm()
        self.assertEqual(page_messages(response), "Added 1 team, 3 students and 1 repo.")
        self.assertTrue(Team.objects.filter(project=self.project, name="Team Gamma").exists())
        self.assertTrue(User.objects.filter(github_login="cy-dev").exists())

    def test_uploading_the_same_file_twice_changes_nothing_and_says_so(self):
        self.confirm()
        counts = (Team.objects.count(), User.objects.count(), TeamMembership.objects.count())
        response = self.confirm()
        self.assertEqual(
            counts, (Team.objects.count(), User.objects.count(), TeamMembership.objects.count())
        )
        self.assertIn("already", page_messages(response).lower())

    def test_a_file_with_errors_is_not_imported(self):
        response = self.confirm(f"{HEADER}\nTeam Gamma,Ada Lovelace,-bad-,,\n")
        self.assertFalse(Team.objects.filter(name="Team Gamma").exists())
        self.assertContains(response, "Row 2")

    def test_confirm_only_accepts_post(self):
        self.assertEqual(self.client.get(confirm_url(self.project.id)).status_code, 405)

    def preview_token(self, text=GOOD):
        page = self.client.post(import_url(self.project.id), upload(text))
        self.assertContains(page, 'name="checked_repos"')
        return page.context["checked_repos"]

    def confirm_with(self, token, text=GOOD):
        return self.client.post(
            confirm_url(self.project.id), {"csv": text, "checked_repos": token}, follow=True
        )

    def test_a_repo_is_checked_once_across_preview_and_confirm(self):
        self.confirm_with(self.preview_token())
        self.assertEqual(self.get_repo.call_count, 1)
        self.assertTrue(Repo.objects.filter(full_name="acme/gamma").exists())

    def test_github_failing_after_a_clean_preview_does_not_fail_the_confirm(self):
        token = self.preview_token()
        self.get_repo.side_effect = requests.ConnectionError()
        response = self.confirm_with(token)
        self.assertEqual(page_messages(response), "Added 1 team, 3 students and 1 repo.")

    def test_a_tampered_token_means_the_repo_is_checked_again(self):
        self.confirm_with(self.preview_token() + "x")
        self.assertEqual(self.get_repo.call_count, 2)
        self.assertTrue(Repo.objects.filter(full_name="acme/gamma").exists())

    def test_an_old_token_means_the_repo_is_checked_again(self):
        with mock.patch("django.core.signing.time.time", return_value=time.time() - 3600):
            token = self.preview_token()
        self.confirm_with(token)
        self.assertEqual(self.get_repo.call_count, 2)

    def test_a_repo_the_preview_did_not_check_is_still_checked(self):
        token = self.preview_token()
        changed = GOOD.replace("acme/gamma", "acme/swapped")
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        response = self.confirm_with(token, changed)
        self.assertContains(response, "Row 2")
        self.assertFalse(Repo.objects.filter(full_name="acme/swapped").exists())


class ImportAccessTests(TeamSetupFixture):
    def test_tas_students_and_others_get_a_403_everywhere(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Enrollment.Role.TA)
        outsider = make_user("sample-outsider")
        for user in (ta, self.grace, outsider):
            client = Client()
            client.force_login(user)
            for url in (import_url(self.project.id), template_url(self.project.id)):
                self.assertEqual(client.get(url).status_code, 403, (user, url))
            self.assertEqual(client.post(import_url(self.project.id), upload()).status_code, 403)
            self.assertEqual(client.post(confirm_url(self.project.id), {"csv": "x"}).status_code, 403)

    def test_a_missing_project_is_a_404(self):
        self.assertEqual(self.client.get(import_url(99999)).status_code, 404)

    def test_someone_not_logged_in_is_sent_to_log_in(self):
        response = Client().get(import_url(self.project.id))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_changes_need_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        self.assertEqual(client.post(import_url(self.project.id), upload()).status_code, 403)
        self.assertEqual(client.post(confirm_url(self.project.id), {"csv": GOOD}).status_code, 403)
        self.assertFalse(Team.objects.filter(name="Team Gamma").exists())
