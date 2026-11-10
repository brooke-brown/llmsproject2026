"""Tests for US-31 (#108): a student disputes a flag or warning about them.

A flag and its dispute are matched by sprint (US-41), and the page labels the flag by its
sprint.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import timedelta
from unittest import mock

from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape

from core.models import Enrollment, Project
from dashboard import services
from dashboard.flags import compute_flags
from dashboard.models import Flag, FlagDispute

from .sprint_helpers import sprint_for
from .test_course_overview import enroll, make_user
from .test_student_home import StudentHomeFixture, make_flag, student_home_url

Role = Enrollment.Role
REASON = "Casey Sample did 10% of the team's requirement-tied work in Sprint 2."


def dispute_url(flag_or_dispute):
    """The dispute page for a flag or dispute: keyed on what survives a recompute, not Flag.pk."""
    return reverse(
        "dashboard:flag_dispute",
        args=[
            flag_or_dispute.team_id,
            flag_or_dispute.rule,
            flag_or_dispute.sprint_id,
        ],
    )


class DisputeFixture(StudentHomeFixture):
    def setUp(self):
        self.flag = make_flag(self.team, self.student, self.sprint2, REASON)
        self.client.force_login(self.student)

    def submit(self, explanation, flag=None):
        return self.client.post(dispute_url(flag or self.flag), {"explanation": explanation})

    def make_dispute(self, status=FlagDispute.Status.OPEN, **fields):
        return FlagDispute.objects.create(
            team=self.team,
            student=self.student,
            rule=self.flag.rule,
            sprint=self.flag.sprint,
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

    def test_the_page_shows_the_flags_severity_sprint_and_reason_and_a_text_box(self):
        response = self.client.get(dispute_url(self.flag))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flag")
        self.assertContains(response, "Sprint 2 (Nov 2 – Nov 8, 2026)")
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
        self.assertEqual(dispute.sprint, self.sprint2)
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
            self.team, self.student, self.sprint1, "A warning.",
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
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "has decided on this dispute")

    def test_a_form_sent_after_the_decision_keeps_what_i_typed(self):
        # Todd's #9: the page was open when the instructor decided, so the student still had
        # the form. What they typed is shown back rather than lost.
        self.make_dispute(status=FlagDispute.Status.FLAG_KEPT)
        response = self.submit("Typed before I saw the decision.")
        self.assertContains(response, "Typed before I saw the decision.")
        self.assertContains(response, "wasn't saved")
        self.assertNotContains(response, 'name="explanation"')

    def test_saving_without_changes_doesnt_count_as_an_edit(self):
        # Todd's #6.
        dispute = self.make_dispute()
        FlagDispute.objects.filter(pk=dispute.pk).update(
            created_at=timezone.now() - timedelta(days=1),
            updated_at=timezone.now() - timedelta(days=1),
        )
        dispute.refresh_from_db()
        response = self.submit("I paired on most of it.")
        self.assertRedirects(response, student_home_url())
        after = FlagDispute.objects.get()
        self.assertEqual(after.updated_at, dispute.updated_at)
        self.assertFalse(after.was_edited)

    def test_a_leading_line_break_survives_the_text_box(self):
        # Todd's #7: browsers drop one newline straight after <textarea>.
        self.make_dispute(explanation="\nStarts on line two.")
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, ">\n\nStarts on line two.</textarea>")

    def test_a_too_long_entry_keeps_its_leading_line_break(self):
        text = "\n" + "z" * 2001
        response = self.submit(text)
        self.assertContains(response, ">\n" + text + "</textarea>")

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
            self.team, self.student, self.sprint2, "New wording.", Flag.Severity.WARNING
        )
        response = self.client.get(dispute_url(again))
        self.assertContains(response, "My side.")
        self.assertContains(self.client.get(student_home_url()), "Disputed, waiting")

    def test_the_url_still_works_after_flags_are_recomputed(self):
        # Todd's #2: compute_flags deletes and recreates flags, so Flag.pk changes.
        url = dispute_url(self.flag)
        self.flag.delete()
        again = make_flag(self.team, self.student, self.sprint2, REASON)
        self.assertNotEqual(again.pk, self.flag.pk)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"explanation": "Typed while it was regenerating."})
        self.assertRedirects(response, student_home_url())
        self.assertEqual(
            FlagDispute.objects.get().explanation, "Typed while it was regenerating."
        )

    def test_the_page_shows_the_flag_as_i_disputed_it_not_as_recomputed(self):
        # Todd's #4.
        self.make_dispute(status=FlagDispute.Status.FLAG_KEPT, resolution_note="About the 10%.")
        self.flag.delete()
        make_flag(self.team, self.student, self.sprint2, "Did 30% this time.", Flag.Severity.WARNING)
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, escape(REASON))
        self.assertNotContains(response, "Did 30% this time.")
        self.assertContains(response, 'class="finding flag"')
        self.assertNotContains(response, 'class="finding warning"')

    def test_a_withdrawn_flag_looks_withdrawn_on_its_dispute_page(self):
        # Todd's #5.
        self.make_dispute(status=FlagDispute.Status.FLAG_WITHDRAWN)
        response = self.client.get(dispute_url(self.flag))
        self.assertContains(response, 'class="finding withdrawn"')
        self.assertContains(response, "Withdrawn after dispute")
        self.assertNotContains(response, 'class="finding flag"')

    def test_a_decided_dispute_stays_readable_after_its_flag_rolls_off(self):
        # Todd's #1: the student can still read their explanation, the decision and the note.
        instructor = make_user("sample-prof", "Pat", "Sample")
        dispute = self.make_dispute(
            explanation="My side.",
            status=FlagDispute.Status.FLAG_KEPT,
            resolution_note="PR 12 was mostly Riley's.",
            resolved_by=instructor,
            resolved_at=timezone.now(),
        )
        compute_flags(self.team)  # No summaries, so no flags are raised.
        home = self.client.get(student_home_url())
        self.assertContains(home, f'href="{dispute_url(dispute)}"')
        response = self.client.get(dispute_url(dispute))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flag no longer raised")
        self.assertContains(response, "My side.")
        self.assertContains(response, "Flag kept by Pat Sample")
        self.assertContains(response, escape("PR 12 was mostly Riley's."))
        self.assertNotContains(response, 'name="explanation"')

    def test_a_decided_dispute_whose_flag_rolled_off_cant_be_changed(self):
        dispute = self.make_dispute(status=FlagDispute.Status.FLAG_KEPT)
        self.flag.delete()
        response = self.client.post(dispute_url(dispute), {"explanation": "Changed."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FlagDispute.objects.get().explanation, "I paired on most of it.")

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
        # Another sprint, so the URL doesn't also name the flag about me.
        team_flag = make_flag(self.team, None, self.sprint1, "About the whole team.")
        self.assertEqual(self.client.get(dispute_url(team_flag)).status_code, 403)

    def test_a_flag_on_a_team_im_no_longer_on_gets_403(self):
        self.student.team_memberships.all().delete()
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)

    def test_a_flag_in_a_course_im_not_a_student_in_gets_403(self):
        Enrollment.objects.filter(user=self.student).update(role=Role.TA)
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)

    def test_a_flag_that_doesnt_exist_gets_404(self):
        other_project = Project.objects.create(course=self.course, name="Project Two")
        other_project_sprint = sprint_for(other_project)
        for args in (
            [self.team.id, self.flag.rule, self.sprint1.id],  # No flag that sprint.
            [self.team.id, "no-such-rule", self.sprint2.id],
            [self.team.id + 1000, self.flag.rule, self.sprint2.id],
            [self.team.id, self.flag.rule, self.sprint2.id + 1000],  # No such sprint.
            [self.team.id, self.flag.rule, other_project_sprint.id],
        ):
            with self.subTest(args=args):
                url = reverse("dashboard:flag_dispute", args=args)
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_another_teams_flag_gets_403_even_with_my_team_in_the_url(self):
        # The team in the URL decides access, so swapping it never reaches another team's flag.
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(dispute_url(self.flag)).status_code, 403)

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
            dispute, created = services.submit_dispute(self.flag, "From this request.")
        self.assertFalse(created)
        self.assertEqual(FlagDispute.objects.count(), 1)
        self.assertEqual(FlagDispute.objects.get().explanation, "From this request.")

    def test_a_new_dispute_says_it_was_created(self):
        dispute, created = services.submit_dispute(self.flag, "Mine.")
        self.assertTrue(created)
        self.assertEqual(dispute, FlagDispute.objects.get())

    def test_an_edit_that_lands_after_the_decision_is_refused(self):
        # Todd's #3: the instructor decides between this request's lookup and its save.
        stale = self.make_dispute()
        FlagDispute.objects.filter(pk=stale.pk).update(status=FlagDispute.Status.FLAG_KEPT)
        with mock.patch.object(services, "dispute_for", return_value=stale):
            with self.assertRaises(services.DisputeError):
                services.submit_dispute(self.flag, "Changed after the decision.")
        self.assertEqual(FlagDispute.objects.get().explanation, "I paired on most of it.")

    def test_an_integrity_error_with_no_dispute_to_edit_is_refused_not_a_crash(self):
        # Todd's #8: the insert failed for another reason, so there's nothing to edit.
        with mock.patch.object(services, "dispute_for", return_value=None), mock.patch.object(
            FlagDispute.objects, "create", side_effect=services.IntegrityError
        ):
            with self.assertRaises(services.DisputeError):
                services.submit_dispute(self.flag, "Mine.")

    def test_a_flag_no_longer_raised_cant_get_a_new_dispute(self):
        with self.assertRaises(services.DisputeError):
            services.submit_dispute(None, "Mine.")
        self.assertFalse(FlagDispute.objects.exists())

    def test_a_browser_line_break_counts_as_one_character(self):
        text = "x" * 1998 + "\r\n" + "x"
        services.submit_dispute(self.flag, text)
        self.assertEqual(FlagDispute.objects.get().explanation, text)
