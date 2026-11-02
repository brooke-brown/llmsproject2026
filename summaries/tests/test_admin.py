"""Tests for the admin pages of Summary, Claim and ValidationRun.

The admin is for debugging and spot checks, not editing: everything is read-only, so
nobody can change claim text that has already passed the validator. Only a whole summary
can be deleted, which removes its claims and logs together. A single claim or run can't
be: deleting a claim would silently change a validated summary's "N couldn't be verified"
note, and deleting a run would change the first-pass and drop-rate metrics.

The helpers that build summaries, claims and runs are the ones the model tests use.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from summaries.models import Claim, Summary, ValidationRun
from summaries.tests.test_models import (
    make_claim,
    make_run,
    make_summary,
    make_team,
    make_user,
)

MODELS = {"summary": Summary, "claim": Claim, "validationrun": ValidationRun}


def admin_url(model_name, action, *args):
    return reverse(f"admin:summaries_{model_name}_{action}", args=args)


class AdminTestCase(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.com",
            password="not-a-real-password",
            github_login="sample-admin",
        )
        self.client.force_login(staff)

        self.team = make_team("Sample Team A")
        self.student = make_user("sample-student-a")
        self.summary = make_summary(team=self.team, user=self.student)
        self.claim = make_claim(self.summary, text="Added the task form.")
        self.run = make_run(self.summary)
        self.objects = {"summary": self.summary, "claim": self.claim, "validationrun": self.run}


class RegistrationTests(AdminTestCase):
    def test_every_summaries_model_is_in_the_admin(self):
        for name, model in MODELS.items():
            with self.subTest(model=name):
                self.assertTrue(admin.site.is_registered(model))

    def test_the_admin_index_links_to_each_list_page(self):
        response = self.client.get(reverse("admin:index"))

        for name in MODELS:
            with self.subTest(model=name):
                self.assertContains(response, admin_url(name, "changelist"))


class PagesLoadTests(AdminTestCase):
    def test_each_list_page_loads(self):
        for name in MODELS:
            with self.subTest(model=name):
                self.assertEqual(self.client.get(admin_url(name, "changelist")).status_code, 200)

    def test_each_detail_page_loads(self):
        for name, obj in self.objects.items():
            with self.subTest(model=name):
                self.assertEqual(self.client.get(admin_url(name, "change", obj.pk)).status_code, 200)


class ReadOnlyTests(AdminTestCase):
    def test_nothing_can_be_added(self):
        for name in MODELS:
            with self.subTest(model=name):
                self.assertEqual(self.client.get(admin_url(name, "add")).status_code, 403)

    def test_nothing_can_be_edited(self):
        for name, obj in self.objects.items():
            with self.subTest(model=name):
                response = self.client.post(admin_url(name, "change", obj.pk), {"text": "changed"})

                self.assertEqual(response.status_code, 403)

    def test_claim_text_is_unchanged_after_an_attempted_edit(self):
        self.client.post(
            admin_url("claim", "change", self.claim.pk),
            {"text": "Changed by hand.", "summary": self.summary.pk},
        )

        self.claim.refresh_from_db()
        self.assertEqual(self.claim.text, "Added the task form.")

    def test_a_summary_can_be_deleted(self):
        response = self.client.post(
            admin_url("summary", "delete", self.summary.pk), {"post": "yes"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Summary.objects.filter(pk=self.summary.pk).exists())


class DeleteTests(AdminTestCase):
    """Only a summary can be deleted, because it removes its claims and logs together."""

    def test_a_single_claim_or_run_cannot_be_deleted(self):
        for name in ["claim", "validationrun"]:
            obj = self.objects[name]
            with self.subTest(model=name, step="page"):
                self.assertEqual(
                    self.client.get(admin_url(name, "delete", obj.pk)).status_code, 403
                )
            with self.subTest(model=name, step="post"):
                response = self.client.post(admin_url(name, "delete", obj.pk), {"post": "yes"})

                self.assertEqual(response.status_code, 403)
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_the_bulk_delete_action_is_not_offered_for_claims_or_runs(self):
        for name in ["claim", "validationrun"]:
            with self.subTest(model=name):
                response = self.client.get(admin_url(name, "changelist"))

                self.assertNotContains(response, "Delete selected")

    def test_a_bulk_delete_request_for_claims_or_runs_deletes_nothing(self):
        for name in ["claim", "validationrun"]:
            obj = self.objects[name]
            with self.subTest(model=name):
                self.client.post(
                    admin_url(name, "changelist"),
                    {"action": "delete_selected", "_selected_action": [obj.pk], "post": "yes"},
                )

                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_the_bulk_delete_action_is_still_offered_for_summaries(self):
        response = self.client.get(admin_url("summary", "changelist"))

        self.assertContains(response, "Delete selected")

    def test_deleting_a_summary_removes_its_claims_and_runs_with_it(self):
        self.client.post(admin_url("summary", "delete", self.summary.pk), {"post": "yes"})

        self.assertEqual(Claim.objects.count(), 0)
        self.assertEqual(ValidationRun.objects.count(), 0)

    def test_claims_cannot_be_deleted_from_a_summarys_page_either(self):
        response = self.client.get(admin_url("summary", "change", self.summary.pk))

        self.assertNotContains(response, "inline-deletelink")


class SummaryPageTests(AdminTestCase):
    def test_the_list_shows_how_many_claims_could_not_be_verified(self):
        make_claim(self.summary, status="couldnt_verify", text="Dropped one.")

        response = self.client.get(admin_url("summary", "changelist"))

        self.assertContains(response, "Unverified count")

    def test_the_detail_page_lists_the_summarys_claims(self):
        make_claim(self.summary, text="Fixed the logout bug.")

        response = self.client.get(admin_url("summary", "change", self.summary.pk))

        self.assertContains(response, "Added the task form.")
        self.assertContains(response, "Fixed the logout bug.")

    def test_the_list_can_be_searched_by_the_students_github_login(self):
        make_summary(team=self.team, user=make_user("sample-student-b"))

        response = self.client.get(admin_url("summary", "changelist"), {"q": "sample-student-b"})

        self.assertContains(response, "sample-student-b")
        self.assertNotContains(response, "sample-student-a")

    def test_the_list_can_be_filtered_by_team(self):
        other_team = make_team("Sample Team B")
        make_summary(team=other_team, user=make_user("sample-student-c"))

        response = self.client.get(
            admin_url("summary", "changelist"), {"team__id__exact": self.team.pk}
        )

        self.assertContains(response, "sample-student-a")
        self.assertNotContains(response, "sample-student-c")


class ClaimPageTests(AdminTestCase):
    def test_a_claim_that_could_not_be_verified_shows_its_validator_errors(self):
        errors = [{"artifact_id": "a/b:pr:9", "check": "artifact_not_found", "message": "Missing."}]
        claim = make_claim(
            self.summary, status="couldnt_verify", validator_errors=errors, text="Made-up work."
        )

        response = self.client.get(admin_url("claim", "change", claim.pk))

        self.assertContains(response, "artifact_not_found")

    def test_the_list_can_be_filtered_by_status(self):
        make_claim(self.summary, status="couldnt_verify", text="Unverifiable work.")

        response = self.client.get(admin_url("claim", "changelist"), {"status__exact": "couldnt_verify"})

        self.assertContains(response, "Unverifiable work.")
        self.assertNotContains(response, "Added the task form.")

    def test_the_list_can_be_searched_by_text(self):
        make_claim(self.summary, text="Rewrote the settings page.")

        response = self.client.get(admin_url("claim", "changelist"), {"q": "settings page"})

        self.assertContains(response, "Rewrote the settings page.")
        self.assertNotContains(response, "Added the task form.")

    def test_a_long_claim_is_shortened_in_the_list(self):
        make_claim(self.summary, text="Long claim. " + "word " * 100 + "THE-END")

        response = self.client.get(admin_url("claim", "changelist"))

        self.assertNotContains(response, "THE-END")


class ValidationRunPageTests(AdminTestCase):
    def test_the_list_shows_the_rates_as_percentages(self):
        # setUp's run: 10 claims, 8 valid on the first pass, 1 dropped.
        response = self.client.get(admin_url("validationrun", "changelist"))

        self.assertContains(response, "80%")
        self.assertContains(response, "10%")

    def test_a_run_with_no_claims_shows_zero_percent(self):
        ValidationRun.objects.all().delete()
        make_run(self.summary, claims_total=0, claims_first_pass=0, claims_dropped=0)

        response = self.client.get(admin_url("validationrun", "changelist"))

        self.assertContains(response, "0%")
