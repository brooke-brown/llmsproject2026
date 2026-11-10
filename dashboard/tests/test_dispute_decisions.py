"""Tests for US-32 (#109): see and decide on students' disputes.

Students can't open disputes from a page yet (US-31), so these create FlagDispute rows
directly, as the flag tests create Flag rows. Each team and each student is rendered in its
own <article>, so the tests check what appears inside a given team's or student's article.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag, FlagDispute

from .sprint_helpers import flaggable_sprint, sprint_for
from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_show_flags import article_containing

Role = Enrollment.Role
Severity = Flag.Severity
Status = FlagDispute.Status
RULE = "low-requirement-share"
REASON = "Ada Sample did 10% of the team's requirement-tied work in Sprint 1."
NOW = datetime(2026, 11, 5, 14, 30, tzinfo=ZoneInfo("America/New_York"))


def decide_url(dispute_id):
    return reverse("dashboard:dispute_decide", args=[dispute_id])


def make_flag(team, user, severity=Severity.FLAG, reason=REASON, **fields):
    values = {"rule": RULE, **fields}
    values.setdefault("sprint", flaggable_sprint(team))
    return Flag.objects.create(team=team, user=user, severity=severity, reason=reason, **values)


def make_dispute(team, student, **fields):
    values = {
        "rule": RULE,
        "sprint": sprint_for(team.project),
        "flag_reason": REASON,
        "severity": "flag",
        "explanation": "I ran our user interviews that week, which aren't in the repo.",
        **fields,
    }
    return FlagDispute.objects.create(team=team, student=student, **values)


class DisputeFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor", "Iris", "Teacher")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.alpha = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.beta = Team.objects.create(project=cls.project, name="Team Beta")
        cls.charlie = Team.objects.create(project=cls.project, name="Team Charlie")

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in [cls.ada, cls.grace]:
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=cls.alpha)
        # Only a team with students can raise flags.
        for team in [cls.beta, cls.charlie]:
            student = make_user(f"sample-{team.name.split()[-1].lower()}")
            enroll(student, cls.course, Role.STUDENT)
            TeamMembership.objects.create(user=student, team=team)

    def setUp(self):
        self.client.force_login(self.instructor)

    def overview(self):
        return self.client.get(overview_url(self.course.id)).content.decode()

    def team_page(self, team=None):
        return self.client.get(team_url((team or self.alpha).id)).content.decode()

    def student_article(self, user, team=None):
        content = self.team_page(team)
        # The name links to the student's latest summary once they have one.
        linked = f"{user.display_name}</a></strong>"
        if linked in content:
            return article_containing(content, linked)
        return article_containing(content, f"<strong>{user.display_name}")

    def decide(self, dispute, decision, note=""):
        return self.client.post(
            decide_url(dispute.id), {"decision": decision, "note": note}, follow=True
        )


class OverviewTests(DisputeFixture):
    def test_a_team_with_an_open_dispute_is_labelled(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertIn("Dispute open", card)

    def test_a_resolved_dispute_isnt_labelled_open(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_KEPT)

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertNotIn("Dispute open", card)

    def test_the_label_appears_only_under_its_own_team(self):
        make_dispute(self.alpha, self.ada)

        card = article_containing(self.overview(), "Team Beta</strong>")

        self.assertNotIn("Dispute open", card)

    def test_open_disputes_come_right_after_flagged_teams(self):
        # Beta is flagged, Charlie only has an open dispute whose flag is gone, Alpha has neither,
        # so going by name alone would put Alpha first and Charlie last.
        make_flag(self.beta, None, reason="Team-wide flag.")
        TeamMembership.objects.create(user=self.ada, team=self.charlie)
        make_dispute(self.charlie, self.ada)

        content = self.overview()

        order = [content.index(f"{name}</strong>") for name in ["Beta", "Charlie", "Alpha"]]
        self.assertEqual(order, sorted(order))

    def test_open_disputes_come_after_warned_teams_too(self):
        make_flag(self.charlie, None, severity=Severity.WARNING, reason="Team-wide warning.")
        make_dispute(self.alpha, self.ada)

        content = self.overview()

        self.assertLess(content.index("Charlie</strong>"), content.index("Alpha</strong>"))

    def test_a_flagged_team_with_an_open_dispute_stays_with_the_flagged_teams(self):
        make_flag(self.beta, None, severity=Severity.WARNING, reason="Team-wide warning.")
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        content = self.overview()

        self.assertLess(content.index("Alpha</strong>"), content.index("Beta</strong>"))
        card = article_containing(content, "Team Alpha</strong>")
        self.assertIn("<mark>Flag</mark>", card)
        self.assertIn("Dispute open", card)

    def test_a_withdrawn_flag_doesnt_make_the_team_flagged(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertNotIn("<mark>Flag</mark>", card)
        self.assertNotIn('class="card flag"', card)

    def test_a_withdrawn_flag_doesnt_sort_the_team_first(self):
        # Charlie would be first if its withdrawn flag still counted.
        make_flag(self.charlie, self.ada)
        make_dispute(self.charlie, self.ada, status=Status.FLAG_WITHDRAWN)

        content = self.overview()

        self.assertLess(content.index("Beta</strong>"), content.index("Charlie</strong>"))
        self.assertNotIn("<mark>", article_containing(content, "Team Charlie</strong>"))

    def test_another_flag_still_counts_when_one_is_withdrawn(self):
        make_flag(self.alpha, self.ada)
        make_flag(self.alpha, self.grace, severity=Severity.WARNING, reason="Grace's warning.")
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertIn("<mark>Warning</mark>", card)

    def test_a_kept_flag_still_counts(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_KEPT)

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertIn("<mark>Flag</mark>", card)

    def test_the_course_picker_doesnt_count_withdrawn_flags(self):
        from dashboard.services import course_cards

        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        (card,) = course_cards(self.instructor)

        self.assertEqual(card.flagged_count, 0)
        self.assertEqual(card.warned_count, 0)

    def test_a_dispute_from_a_student_no_longer_on_the_team_isnt_labelled(self):
        # Removing a membership in admin leaves the dispute, but the team page no longer shows
        # it, so nobody could resolve it and clear the label.
        make_dispute(self.alpha, self.ada)
        TeamMembership.objects.filter(user=self.ada, team=self.alpha).delete()

        card = article_containing(self.overview(), "Team Alpha</strong>")

        self.assertNotIn("Dispute open", card)

    def test_a_dispute_from_a_student_no_longer_on_the_team_doesnt_sort_it_early(self):
        # Charlie would come before Beta if Ada's dispute still counted.
        make_dispute(self.charlie, self.ada)

        content = self.overview()

        self.assertLess(content.index("Beta</strong>"), content.index("Charlie</strong>"))

    def test_the_pill_isnt_styled_as_the_dispute_panel(self):
        # A rule for a bare .dispute class would also style <span class="tag dispute">.
        make_dispute(self.alpha, self.ada)

        self.assertNotRegex(self.overview(), r"(^|[\s,}])\.dispute[\s{,.:]")


class TeamPageTests(DisputeFixture):
    def test_a_disputed_flag_shows_the_explanation_and_status(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        article = self.student_article(self.ada)

        self.assertIn(escape(REASON), article)
        self.assertIn("user interviews", article)
        self.assertIn("Dispute open", article)

    def test_it_shows_when_the_dispute_was_opened_and_last_edited(self):
        make_flag(self.alpha, self.ada)
        with mock.patch("django.utils.timezone.now", return_value=NOW):
            dispute = make_dispute(self.alpha, self.ada)
        later = datetime(2026, 11, 6, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        with mock.patch("django.utils.timezone.now", return_value=later):
            dispute.explanation = "An edited explanation."
            dispute.save()

        article = self.student_article(self.ada)

        self.assertIn("Opened Nov 5", article)
        self.assertIn("edited Nov 6", article)

    def test_an_unedited_dispute_doesnt_say_edited(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        self.assertNotIn("edited", self.student_article(self.ada))

    def test_the_explanation_is_escaped(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, explanation="<script>alert(1)</script>")

        article = self.student_article(self.ada)

        self.assertNotIn("<script>alert(1)</script>", article)
        self.assertIn("&lt;script&gt;", article)

    def test_an_undisputed_flag_shows_no_dispute(self):
        make_flag(self.alpha, self.ada)

        self.assertNotIn("Dispute", self.student_article(self.ada))

    def test_a_dispute_appears_only_under_its_student(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        self.assertNotIn("user interviews", self.student_article(self.grace))

    def test_a_dispute_whose_flag_is_gone_is_still_shown(self):
        make_dispute(self.alpha, self.ada)

        article = self.student_article(self.ada)

        self.assertIn("Flag no longer raised", article)
        self.assertIn(escape(REASON), article)
        self.assertIn("user interviews", article)

    def test_a_withdrawn_dispute_whose_flag_is_gone_still_hides_its_reason(self):
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        article = self.student_article(self.ada)

        self.assertIn("Flag no longer raised", article)
        self.assertNotIn(escape(REASON), article)
        self.assertIn("user interviews", article)

    def test_a_kept_dispute_whose_flag_is_gone_shows_its_reason(self):
        make_dispute(self.alpha, self.ada, status=Status.FLAG_KEPT)

        article = self.student_article(self.ada)

        self.assertIn("Flag no longer raised", article)
        self.assertIn(escape(REASON), article)

    def test_the_dispute_panel_has_its_own_class(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        article = self.student_article(self.ada)

        self.assertIn('<div class="dispute-panel">', article)
        self.assertIn('<span class="tag dispute">', article)
        self.assertNotRegex(self.team_page(), r"(^|[\s,}])\.dispute[\s{,.:]")

    def test_a_raised_flags_dispute_isnt_marked_gone(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada)

        self.assertNotIn("no longer raised", self.student_article(self.ada))

    def test_a_withdrawn_flag_hides_its_reason(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        article = self.student_article(self.ada)

        self.assertIn("Withdrawn after dispute", article)
        self.assertNotIn(escape(REASON), article)
        self.assertNotIn('class="finding flag"', article)

    def test_a_withdrawn_flag_doesnt_make_the_team_status_a_flag(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        content = self.team_page()

        self.assertNotIn('<span class="tag flag">Flag</span>', content)

    def test_a_withdrawn_flag_doesnt_colour_the_pie(self):
        from dashboard import services

        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_WITHDRAWN)

        memberships = list(services.team_memberships_with_summaries(self.alpha))
        memberships, _ = services.add_contribution_shares(self.alpha, memberships)

        self.assertTrue(all(m.chart_flag is None for m in memberships))

    def test_a_kept_flag_still_shows_its_reason(self):
        make_flag(self.alpha, self.ada)
        make_dispute(self.alpha, self.ada, status=Status.FLAG_KEPT)

        article = self.student_article(self.ada)

        self.assertIn(escape(REASON), article)
        self.assertIn("Flag kept", article)

    def test_a_resolved_dispute_shows_who_decided_when_and_the_note(self):
        make_flag(self.alpha, self.ada)
        make_dispute(
            self.alpha,
            self.ada,
            status=Status.FLAG_KEPT,
            resolved_by=self.instructor,
            resolved_at=NOW,
            resolution_note="The interviews were the week before.",
        )

        article = self.student_article(self.ada)

        self.assertIn("Iris Teacher", article)
        self.assertIn("Nov 5, 2026", article)
        self.assertIn("The interviews were the week before.", article)

    def test_a_ta_sees_disputes_but_no_decision_controls(self):
        make_flag(self.alpha, self.ada)
        dispute = make_dispute(self.alpha, self.ada)
        self.client.force_login(self.ta)

        article = self.student_article(self.ada)

        self.assertIn("user interviews", article)
        self.assertNotIn(decide_url(dispute.id), article)

    def test_an_instructor_sees_keep_and_withdraw_controls(self):
        make_flag(self.alpha, self.ada)
        dispute = make_dispute(self.alpha, self.ada)

        article = self.student_article(self.ada)

        self.assertIn(f'action="{decide_url(dispute.id)}"', article)
        self.assertIn("Keep flag", article)
        self.assertIn("Withdraw flag", article)
        self.assertIn('maxlength="1000"', article)

    def test_a_resolved_dispute_still_offers_changing_the_decision(self):
        make_flag(self.alpha, self.ada)
        dispute = make_dispute(self.alpha, self.ada, status=Status.FLAG_KEPT)

        article = self.student_article(self.ada)

        self.assertIn("Change decision", article)
        self.assertIn(f'action="{decide_url(dispute.id)}"', article)


class DecideTests(DisputeFixture):
    def setUp(self):
        super().setUp()
        self.flag = make_flag(self.alpha, self.ada)
        self.dispute = make_dispute(self.alpha, self.ada)

    def test_keeping_the_flag_resolves_the_dispute(self):
        with mock.patch("django.utils.timezone.now", return_value=NOW):
            response = self.decide(self.dispute, "keep", "The interviews were the week before.")
        self.dispute.refresh_from_db()

        self.assertRedirects(response, team_url(self.alpha.id))
        self.assertEqual(self.dispute.status, Status.FLAG_KEPT)
        self.assertEqual(self.dispute.resolution_note, "The interviews were the week before.")
        self.assertEqual(self.dispute.resolved_by, self.instructor)
        self.assertEqual(self.dispute.resolved_at, NOW)
        self.assertTrue(Flag.objects.filter(pk=self.flag.pk).exists())

    def test_withdrawing_the_flag_resolves_the_dispute(self):
        self.decide(self.dispute, "withdraw")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.FLAG_WITHDRAWN)
        self.assertEqual(self.dispute.resolved_by, self.instructor)
        self.assertEqual(self.dispute.resolution_note, "")

    def test_a_success_message_names_the_student_and_decision(self):
        response = self.decide(self.dispute, "withdraw")

        self.assertContains(response, "Withdrew the flag about Ada Sample")

    def test_the_note_is_trimmed(self):
        self.decide(self.dispute, "keep", "  A note.  ")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.resolution_note, "A note.")

    def test_a_note_over_1000_characters_is_refused(self):
        response = self.decide(self.dispute, "keep", "x" * 1001)
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.OPEN)
        self.assertContains(response, "1,000 characters")

    def test_a_note_of_exactly_1000_characters_is_accepted(self):
        self.decide(self.dispute, "keep", "x" * 1000)
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.FLAG_KEPT)

    def test_an_unknown_decision_is_refused(self):
        response = self.decide(self.dispute, "ignore")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.OPEN)
        self.assertContains(response, "Keep flag or Withdraw flag")

    def test_a_decision_can_be_changed(self):
        self.decide(self.dispute, "keep", "First note.")
        other = make_user("sample-coteacher", "Cole", "Teacher")
        enroll(other, self.course, Role.INSTRUCTOR)
        self.client.force_login(other)

        self.decide(self.dispute, "withdraw", "Second note.")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.FLAG_WITHDRAWN)
        self.assertEqual(self.dispute.resolution_note, "Second note.")
        self.assertEqual(self.dispute.resolved_by, other)

    def test_a_dispute_whose_flag_is_gone_can_still_be_decided(self):
        self.flag.delete()

        self.decide(self.dispute, "keep")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.status, Status.FLAG_KEPT)

    def test_a_withdrawn_flag_stays_withdrawn_after_flags_are_recomputed(self):
        from dashboard import services

        self.decide(self.dispute, "withdraw")
        # A rerun raises the same rule for the same student and sprint, reworded.
        with mock.patch(
            "dashboard.flags.RULES",
            [lambda team, sprint: [Flag(team=team, user=self.ada, rule=RULE, reason="Reworded.",
                                        severity=Severity.WARNING, sprint=sprint)]],
        ):
            from dashboard.flags import compute_flags

            compute_flags(self.alpha)

        self.assertEqual(services.team_status(self.alpha), "ok")
        self.assertIn("Withdrawn after dispute", self.student_article(self.ada))
        self.assertNotIn("Reworded.", self.student_article(self.ada))

    def test_deciding_doesnt_count_as_the_student_editing(self):
        # Opened days ago, so a decision now would look like an edit if it touched updated_at.
        FlagDispute.objects.filter(pk=self.dispute.pk).update(created_at=NOW, updated_at=NOW)

        self.decide(self.dispute, "keep")

        self.assertNotIn("edited", self.student_article(self.ada))

    def test_deciding_doesnt_change_the_snapshot_or_explanation(self):
        self.decide(self.dispute, "withdraw")
        self.dispute.refresh_from_db()

        self.assertEqual(self.dispute.flag_reason, REASON)
        self.assertIn("user interviews", self.dispute.explanation)


class AccessTests(DisputeFixture):
    def setUp(self):
        super().setUp()
        self.dispute = make_dispute(self.alpha, self.ada)

    def test_a_ta_cant_decide(self):
        self.client.force_login(self.ta)

        response = self.decide(self.dispute, "withdraw")
        self.dispute.refresh_from_db()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.dispute.status, Status.OPEN)

    def test_the_disputing_student_cant_decide(self):
        self.client.force_login(self.ada)

        self.assertEqual(self.decide(self.dispute, "withdraw").status_code, 403)

    def test_another_courses_instructor_cant_decide(self):
        outsider = make_user("sample-outsider")
        enroll(outsider, Course.objects.create(name="CS 3240, Fall 2026"), Role.INSTRUCTOR)
        self.client.force_login(outsider)

        self.assertEqual(self.decide(self.dispute, "withdraw").status_code, 403)

    def test_an_unenrolled_user_cant_decide(self):
        self.client.force_login(make_user("sample-stranger"))

        self.assertEqual(self.decide(self.dispute, "withdraw").status_code, 403)

    def test_a_missing_dispute_is_a_404(self):
        response = self.client.post(decide_url(self.dispute.id + 100), {"decision": "keep"})

        self.assertEqual(response.status_code, 404)

    def test_deciding_only_accepts_post(self):
        response = self.client.get(decide_url(self.dispute.id))

        self.assertEqual(response.status_code, 405)

    def test_deciding_needs_a_csrf_token(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)

        response = client.post(decide_url(self.dispute.id), {"decision": "keep"})

        self.assertEqual(response.status_code, 403)

    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        self.client.logout()

        response = self.client.post(decide_url(self.dispute.id), {"decision": "keep"})

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
