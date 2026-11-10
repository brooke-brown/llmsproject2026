"""Tests for US-31 (#108): a student disputes a flag or warning about them.

Until US-41 (#123) moves flags onto sprints, a flag and its dispute are matched by week, and
the page labels the flag by its week.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import date, timedelta
from unittest import mock

from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape

from core.models import Enrollment
from dashboard import services
from dashboard.flags import compute_flags
from dashboard.models import Flag, FlagDispute

from .test_course_overview import enroll, make_user
from .test_student_home import StudentHomeFixture, make_flag, student_home_url

Role = Enrollment.Role
WEEK = date(2026, 11, 2)
REASON = "Casey Sample did 10% of the team's requirement-tied work in the week of Nov 2."


def dispute_url(flag):
    return reverse("dashboard:flag_dispute", args=[flag.id])


class DisputeFixture(StudentHomeFixture):
    def setUp(self):
        self.flag = make_flag(self.team, self.student, WEEK, REASON)
        self.client.force_login(self.student)

    def submit(self, explanation, flag=None):
        return self.client.post(dispute_url(flag or self.flag), {"explanation": explanation})

    def make_dispute(self, status=FlagDispute.Status.OPEN, **fields):
        return FlagDispute.objects.create(
            team=self.team,
            student=self.student,
            rule=self.flag.rule,
            week_start=self.flag.week_start,
            flag_reason=self.flag.reason,
            severity=self.flag.severity,
            explanation=fields.pop("explanation", "I paired on most of it."),
            status=status,
            **fields,
        )


class OpeningADisputeTests(DisputeFixture):
    def test_the_student_home_links_each_undisputed_flag_to_its_dispute_page(self):
        response = self.client.get(student_home_url())
        self.assertContains(response, f'href="{dispute_url(self.flag)}"')
        self.assertContains(response, ">Dispute<")

    def test_the_page_shows_the_flags_severity_week_and_reason_and_a_text_box(self):
        response = self.client.get(dispute_url(self.flag))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flag")
        self.assertContains(response, "Week of Nov 2, 2026")
        self.assertContains(response, escape(REASON))
        self.assertContains(response, 'name="explanation"')

    def test_the_page_says_who_reads_it_and_that_the_flag_stays_visible(self):
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, "Your instructor and TAs will read this")
        self.assertContains(response, "stays visible to them until they decide")

    def test_submitting_saves_the_dispute_with_the_flag_as_it_was(self):
        self.submit("I paired with Riley on PR 12.")
        dispute = FlagDispute.objects.get()
        self.assertEqual(dispute.team, self.team)
        self.assertEqual(dispute.student, self.student)
        self.assertEqual(dispute.rule, self.flag.rule)
        self.assertEqual(dispute.week_start, WEEK)
        self.assertEqual(dispute.flag_reason, REASON)
        self.assertEqual(dispute.severity, Flag.Severity.FLAG)
        self.assertEqual(dispute.explanation, "I paired with Riley on PR 12.")
        self.assertEqual(dispute.status, FlagDispute.Status.OPEN)

    def test_the_explanation_is_stored_exactly_as_typed(self):
        text = "  Line one <b>not bold</b>\n\nLine two  "
        self.submit(text)
        self.assertEqual(FlagDispute.objects.get().explanation, text)

    def test_after_submitting_im_back_on_the_student_home_with_the_flag_waiting(self):
        response = self.submit("I paired with Riley on PR 12.")
        self.assertRedirects(response, student_home_url())
        response = self.client.get(student_home_url())
        self.assertContains(response, "Disputed, waiting for your instructor")
        self.assertNotContains(response, ">Dispute<")

    def test_a_blank_explanation_is_rejected_and_nothing_is_saved(self):
        for text in ("", "   \n  "):
            with self.subTest(text=text):
                response = self.submit(text)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Write your explanation")
                self.assertFalse(FlagDispute.objects.exists())

    def test_a_too_long_explanation_is_rejected_and_what_i_typed_is_kept(self):
        text = "x" * 2001
        response = self.submit(text)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "up to 2,000 characters")
        self.assertContains(response, text)
        self.assertFalse(FlagDispute.objects.exists())

    def test_an_explanation_of_exactly_2000_characters_is_accepted(self):
        self.submit("x" * 2000)
        self.assertEqual(len(FlagDispute.objects.get().explanation), 2000)

    def test_a_warning_can_be_disputed_too(self):
        warning = make_flag(
            self.team, self.student, WEEK - timedelta(days=7), "A warning.",
            Flag.Severity.WARNING,
        )
        self.submit("My explanation.", flag=warning)
        self.assertEqual(FlagDispute.objects.get().severity, Flag.Severity.WARNING)

    def test_opening_dispute_again_shows_my_dispute_instead_of_a_new_form(self):
        self.make_dispute(explanation="What I wrote before.")
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, "What I wrote before.")
        self.assertContains(response, "Disputed, waiting for your instructor")

    def test_submitting_again_edits_the_one_dispute_instead_of_adding_another(self):
        self.submit("First.")
        self.submit("Second.")
        self.assertEqual(FlagDispute.objects.count(), 1)
        self.assertEqual(FlagDispute.objects.get().explanation, "Second.")


class ChangingADisputeTests(DisputeFixture):
    def test_while_open_i_can_edit_my_explanation(self):
        dispute = self.make_dispute()
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, 'name="explanation"')
        self.assertContains(response, "I paired on most of it.")

        self.submit("Edited explanation.")
        dispute.refresh_from_db()
        self.assertEqual(dispute.explanation, "Edited explanation.")
        self.assertEqual(dispute.status, FlagDispute.Status.OPEN)

    def test_editing_keeps_the_flag_as_it_was_when_i_disputed_it(self):
        self.make_dispute()
        Flag.objects.filter(pk=self.flag.pk).update(reason="Reworded.")
        self.submit("Edited explanation.")
        self.assertEqual(FlagDispute.objects.get().flag_reason, REASON)

    def test_the_page_shows_when_i_last_edited_it(self):
        dispute = self.make_dispute()
        edited = timezone.now() + timedelta(days=1)
        FlagDispute.objects.filter(pk=dispute.pk).update(updated_at=edited)
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, "Last edited")
        self.assertContains(response, date_format(timezone.localtime(edited), "M j, Y"))

    def test_an_unedited_dispute_says_when_it_was_submitted_instead(self):
        self.make_dispute()
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, "Submitted")
        self.assertNotContains(response, "Last edited")

    def test_a_too_long_edit_is_rejected_and_the_dispute_is_unchanged(self):
        self.make_dispute()
        response = self.submit("y" * 2001)
        self.assertContains(response, "up to 2,000 characters")
        self.assertContains(response, "y" * 2001)
        self.assertEqual(FlagDispute.objects.get().explanation, "I paired on most of it.")

    def test_once_resolved_i_can_read_it_and_the_decision_and_note(self):
        instructor = make_user("sample-prof", "Pat", "Sample")
        self.make_dispute(
            status=FlagDispute.Status.FLAG_KEPT,
            resolution_note="PR 12 was mostly Riley's.",
            resolved_by=instructor,
            resolved_at=timezone.now(),
        )
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, "I paired on most of it.")
        self.assertContains(response, "Flag kept")
        self.assertContains(response, escape("PR 12 was mostly Riley's."))
        self.assertNotContains(response, 'name="explanation"')

    def test_once_resolved_i_cant_change_it(self):
        for status in (FlagDispute.Status.FLAG_KEPT, FlagDispute.Status.FLAG_WITHDRAWN):
            with self.subTest(status=status):
                FlagDispute.objects.all().delete()
                self.make_dispute(status=status)
                response = self.submit("Trying to change it.")
                self.assertEqual(FlagDispute.objects.get().explanation, "I paired on most of it.")
                self.assertEqual(FlagDispute.objects.get().status, status)
                self.assertRedirects(response, dispute_url(self.flag))

    def test_the_student_home_shows_a_resolved_disputes_decision(self):
        self.make_dispute(status=FlagDispute.Status.FLAG_WITHDRAWN)
        response = self.client.get(student_home_url())
        self.assertContains(response, "Flag withdrawn")
        self.assertNotContains(response, ">Dispute<")


class AfterRegeneratingTests(DisputeFixture):
    def test_a_flag_raised_again_with_new_wording_and_severity_keeps_my_dispute(self):
        self.make_dispute(explanation="My side.")
        self.flag.delete()
        again = make_flag(
            self.team, self.student, WEEK, "New wording.", Flag.Severity.WARNING
        )
        response = self.client.get(dispute_url(again))
        self.assertContains(response, "My side.")
        self.assertContains(self.client.get(student_home_url()), "Disputed, waiting")

    def test_a_flag_no_longer_raised_keeps_its_dispute_on_my_list(self):
        self.make_dispute(explanation="My side.")
        compute_flags(self.team)  # No summaries, so no flags are raised.
        response = self.client.get(student_home_url())
        self.assertContains(response, "Flag no longer raised")
        self.assertContains(response, "Disputed · Open")
        # The flag is gone, so there is no dispute page to open or edit.
        self.assertNotContains(response, dispute_url(self.flag))
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 404)


class DisputeAccessTests(DisputeFixture):
    def test_teammates_and_others_get_403(self):
        instructor = make_user("sample-prof")
        enroll(instructor, self.course, Role.INSTRUCTOR)
        for user in (self.teammate, self.stranger, instructor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)
                self.assertEqual(self.submit("Not mine.").status_code, 403)
        self.assertFalse(FlagDispute.objects.exists())

    def test_a_flag_about_the_whole_team_gets_403(self):
        team_flag = make_flag(self.team, None, WEEK, "About the whole team.")
        self.assertEqual(self.client.get(dispute_url(team_flag)).status_code, 403)

    def test_a_flag_on_a_team_im_no_longer_on_gets_403(self):
        self.student.team_memberships.all().delete()
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)

    def test_a_flag_in_a_course_im_not_a_student_in_gets_403(self):
        Enrollment.objects.filter(user=self.student).update(role=Role.TA)
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)

    def test_a_flag_that_doesnt_exist_gets_404(self):
        url = reverse("dashboard:flag_dispute", args=[self.flag.id + 1000])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_posting_without_a_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(dispute_url(self.flag), {"explanation": "No token."})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FlagDispute.objects.exists())

    def test_the_form_carries_a_csrf_token_and_posts(self):
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_other_methods_are_refused(self):
        self.assertEqual(self.client.put(dispute_url(self.flag)).status_code, 405)

    def test_logged_out_visitors_are_sent_to_login(self):
        self.client.logout()
        response = self.client.get(dispute_url(self.flag))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class SubmitDisputeServiceTests(DisputeFixture):
    """The cases the page never reaches on its own."""

    def test_a_decided_dispute_is_refused_even_without_the_views_check(self):
        self.make_dispute(status=FlagDispute.Status.FLAG_KEPT)
        with self.assertRaises(services.DisputeError):
            services.submit_dispute(self.flag, "Changed.")
        self.assertEqual(FlagDispute.objects.get().explanation, "I paired on most of it.")

    def test_two_submissions_at_once_leave_one_dispute(self):
        # The other request files the dispute between this one's lookup and its insert.
        existing = self.make_dispute(explanation="From the other request.")
        with mock.patch.object(services, "dispute_for", side_effect=[None, existing]):
            services.submit_dispute(self.flag, "From this request.")
        self.assertEqual(FlagDispute.objects.count(), 1)
        self.assertEqual(FlagDispute.objects.get().explanation, "From this request.")

    def test_a_browser_line_break_counts_as_one_character(self):
        text = "x" * 1998 + "\r\n" + "x"
        services.submit_dispute(self.flag, text)
        self.assertEqual(FlagDispute.objects.get().explanation, text)
