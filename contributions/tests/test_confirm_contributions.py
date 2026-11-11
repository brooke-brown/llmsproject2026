"""Tests for US-34 (#111): confirm my teammates' offline work.

These drive the real pages, so they check what a student sees. URLs are reversed per test
rather than at module level, so each criterion passes or fails on its own.

The issue leaves some names and wording open. These tests expect:
- URLs named `contributions:team_list` taking a project ID, and `contributions:confirm` and
  `contributions:unconfirm` taking a contribution ID.
- Each contribution rendered in an element with `id="contribution-<pk>"`, as in US-33.
- "Newest first" meaning by the date of the work, then most recently added, as in US-33.
- The count worded "Confirmed by N teammate(s)", as in US-33.
- The teammate's name being `User.display_name`.
- The guidance worded "Confirm only work you saw or took part in".
- Confirming or undoing returning to the team's list, and a GET to either being a 405.
- Confirming twice, or undoing a confirmation you haven't made, changing nothing.
- A student on another team in the project opening the page and seeing only their own team.
- The confirm form carrying the contribution's `updated_at` in a hidden `updated_at` field, and
  a confirmation refused with a message including "read it again" when it no longer matches.
- `services.confirm_contribution(contribution, teammate, seen_updated_at)`, raising
  `PermissionDenied` when the model's rules refuse the confirmation, and the view fetching the
  contribution with `services.get_team_contribution`.
"""

import itertools
import re
from datetime import date, timedelta
from decimal import Decimal

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from contributions import services
from contributions.models import Contribution, Corroboration
from core.models import TeamMembership

from .test_offline_contributions import (
    MISSING_ID,
    NOW,
    VALID,
    OfflineContributionsTestCase,
    delete_url,
    edit_url,
    list_url,
    page_text,
)

User = get_user_model()

CONFIRM_LABEL = "I can confirm this"
CONFIRMED_LABEL = "You confirmed this"
UNDO_LABEL = "Undo"
GUIDANCE = "Confirm only work you saw or took part in"


def team_list_url(project_id):
    return reverse("contributions:team_list", args=[project_id])


def confirm_url(contribution_id):
    return reverse("contributions:confirm", args=[contribution_id])


def unconfirm_url(contribution_id):
    return reverse("contributions:unconfirm", args=[contribution_id])


class ConfirmContributionsTestCase(OfflineContributionsTestCase):
    """Student A is logged in. A, B and F share a team, C is on another team in the project."""

    def teammates_contribution(self, **fields):
        return self.make_contribution(student=self.student_b, **fields)

    def team_page(self):
        return self.client.get(team_list_url(self.project.id))

    def seen_version(self, contribution):
        """The version of the contribution that the team page's confirm form carries."""
        row = self.row(self.team_page(), contribution)
        field = re.search(r'<input\b[^>]*\bname="updated_at"[^>]*>', row)
        self.assertIsNotNone(field, "the confirm form has no updated_at field")
        return re.search(r'\bvalue="([^"]*)"', field.group(0)).group(1)

    def post_confirm(self, contribution, **extra):
        """Confirm as the browser would, sending what the page showed."""
        return self.client.post(
            confirm_url(contribution.pk),
            {"updated_at": self.seen_version(contribution)},
            **extra,
        )

    def assert_form_posts_to(self, row, url):
        form = re.search(rf'<form\b[^>]*\baction="{re.escape(url)}"[^>]*>', row)
        self.assertIsNotNone(form, f"no form posting to {url}")
        self.assertRegex(form.group(0).lower(), r'method="post"')


class TeamListTests(ConfirmContributionsTestCase):
    def test_a_project_on_the_student_home_links_to_the_teams_contributions(self):
        response = self.client.get(reverse("dashboard:student_home"))

        self.assertIn("Team's offline contributions", page_text(response))
        self.assertContains(response, f'href="{team_list_url(self.project.id)}"')

    def test_the_page_is_titled_for_the_team(self):
        response = self.team_page()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Team's offline contributions", page_text(response))

    def test_each_row_shows_who_did_what_and_when(self):
        User.objects.filter(pk=self.student_b.pk).update(
            first_name="Sample", last_name="Teammate"
        )
        contribution = self.teammates_contribution(
            category=Contribution.Category.USER_RESEARCH,
            description="Interviewed two users.",
            hours=Decimal("2.50"),
        )

        row = self.row(self.team_page(), contribution)

        self.assertIn("Sample Teammate", row)
        self.assertIn("Aug 12, 2026", row)
        self.assertIn("User research", row)
        self.assertIn("Interviewed two users.", row)
        self.assertIn("2.50 hours", row)

    def test_a_teammate_with_no_name_shows_their_github_login(self):
        contribution = self.teammates_contribution()

        row = self.row(self.team_page(), contribution)

        self.assertIn(self.student_b.github_login, row)

    def test_a_contribution_without_hours_does_not_show_any(self):
        contribution = self.teammates_contribution(hours=None)

        row = self.row(self.team_page(), contribution)

        self.assertNotIn("None", row)
        self.assertNotRegex(row, r"\bhours?\b")

    def test_newest_work_comes_first_across_teammates(self):
        middle = self.teammates_contribution(date=date(2026, 8, 12))
        oldest = self.make_contribution(student=self.student_f, date=date(2026, 8, 5))
        newest = self.make_contribution(student=self.student_f, date=date(2026, 8, 20))

        self.assertEqual(self.listed_ids(self.team_page()), [newest.pk, middle.pk, oldest.pk])

    def test_work_on_the_same_day_lists_the_latest_added_first(self):
        first = self.teammates_contribution()
        second = self.make_contribution(student=self.student_f)

        self.assertEqual(self.listed_ids(self.team_page()), [second.pk, first.pk])

    def test_my_own_contributions_are_not_listed(self):
        self.make_contribution(description="My own meeting.")
        theirs = self.teammates_contribution()

        response = self.team_page()

        self.assertEqual(self.listed_ids(response), [theirs.pk])
        self.assertNotContains(response, "My own meeting.")

    def test_another_teams_contributions_are_not_listed(self):
        self.make_contribution(
            student=self.student_c, team=self.rival_team, description="Rival team's meeting."
        )

        response = self.team_page()

        self.assertEqual(self.listed_ids(response), [])
        self.assertNotContains(response, "Rival team's meeting.")

    def test_only_this_projects_contributions_are_listed(self):
        TeamMembership.objects.create(user=self.student_a, team=self.far_team)
        TeamMembership.objects.create(user=self.student_b, team=self.far_team)
        here = self.teammates_contribution()
        self.make_contribution(student=self.student_b, team=self.far_team)

        self.assertEqual(self.listed_ids(self.team_page()), [here.pk])

    def test_the_page_says_to_confirm_only_what_i_saw(self):
        self.teammates_contribution()

        self.assertIn(GUIDANCE, page_text(self.team_page()))

    def test_a_teammates_row_has_no_edit_or_delete(self):
        contribution = self.teammates_contribution()

        row = self.row(self.team_page(), contribution)

        self.assertNotIn(edit_url(contribution.pk), row)
        self.assertNotIn(delete_url(contribution.pk), row)


class ConfirmTests(ConfirmContributionsTestCase):
    def test_an_unconfirmed_row_offers_to_confirm(self):
        contribution = self.teammates_contribution()

        row = self.row(self.team_page(), contribution)

        self.assertIn(CONFIRM_LABEL, row)
        self.assert_form_posts_to(row, confirm_url(contribution.pk))
        self.assertNotIn(CONFIRMED_LABEL, row)
        self.assertNotIn(UNDO_LABEL, row)

    def test_confirming_records_my_confirmation_and_returns_to_the_list(self):
        contribution = self.teammates_contribution()

        response = self.post_confirm(contribution)

        self.assertRedirects(response, team_list_url(self.project.id))
        self.assertEqual(
            list(Corroboration.objects.values_list("contribution_id", "teammate_id")),
            [(contribution.pk, self.student_a.pk)],
        )

    def test_once_confirmed_the_row_says_so_and_offers_undo(self):
        contribution = self.teammates_contribution()
        self.post_confirm(contribution)

        row = self.row(self.team_page(), contribution)

        self.assertIn(CONFIRMED_LABEL, row)
        self.assertIn(UNDO_LABEL, row)
        self.assert_form_posts_to(row, unconfirm_url(contribution.pk))
        self.assertNotIn(CONFIRM_LABEL, row)

    def test_someone_elses_confirmation_still_leaves_mine_to_give(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_f)

        row = self.row(self.team_page(), contribution)

        self.assertIn(CONFIRM_LABEL, row)
        self.assertNotIn(CONFIRMED_LABEL, row)
        self.assertNotIn(UNDO_LABEL, row)

    def test_confirming_twice_records_it_once(self):
        contribution = self.teammates_contribution()

        seen = self.seen_version(contribution)

        self.client.post(confirm_url(contribution.pk), {"updated_at": seen})
        response = self.client.post(confirm_url(contribution.pk), {"updated_at": seen})

        self.assertRedirects(response, team_list_url(self.project.id))
        self.assertEqual(Corroboration.objects.count(), 1)

    def test_i_cannot_confirm_my_own_contribution(self):
        mine = self.make_contribution()

        response = self.client.post(
            confirm_url(mine.pk), {"updated_at": mine.updated_at.isoformat()}
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Corroboration.objects.exists())

    def test_confirming_locks_the_contribution_for_its_author(self):
        contribution = self.teammates_contribution()
        self.post_confirm(contribution)
        self.login(self.student_b)

        self.client.post(edit_url(contribution.pk), {**VALID, "description": "Rewritten."})

        contribution.refresh_from_db()
        self.assertEqual(contribution.description, "Ran the planning meeting.")


class UndoTests(ConfirmContributionsTestCase):
    def setUp(self):
        super().setUp()
        self.contribution = self.teammates_contribution()
        self.confirm(self.contribution, teammate=self.student_a)

    def test_undoing_removes_my_confirmation_and_returns_to_the_list(self):
        response = self.client.post(unconfirm_url(self.contribution.pk))

        self.assertRedirects(response, team_list_url(self.project.id))
        self.assertFalse(Corroboration.objects.exists())

    def test_after_undoing_i_can_confirm_again(self):
        self.client.post(unconfirm_url(self.contribution.pk))

        row = self.row(self.team_page(), self.contribution)

        self.assertIn(CONFIRM_LABEL, row)
        self.assertNotIn(CONFIRMED_LABEL, row)

    def test_undoing_leaves_other_teammates_confirmations(self):
        other = self.confirm(self.contribution, teammate=self.student_f)

        self.client.post(unconfirm_url(self.contribution.pk))

        self.assertEqual(list(Corroboration.objects.all()), [other])

    def test_undoing_a_confirmation_i_have_not_made_changes_nothing(self):
        other_contribution = self.make_contribution(student=self.student_f)
        other = self.confirm(other_contribution, teammate=self.student_b)

        response = self.client.post(unconfirm_url(other_contribution.pk))

        self.assertRedirects(response, team_list_url(self.project.id))
        self.assertTrue(Corroboration.objects.filter(pk=other.pk).exists())

    def test_the_author_can_edit_again_once_nobody_has_confirmed(self):
        self.client.post(unconfirm_url(self.contribution.pk))
        self.login(self.student_b)

        self.client.post(edit_url(self.contribution.pk), {**VALID, "description": "Corrected."})

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.description, "Corrected.")

    def test_the_author_can_delete_again_once_nobody_has_confirmed(self):
        self.client.post(unconfirm_url(self.contribution.pk))
        self.login(self.student_b)

        self.client.post(delete_url(self.contribution.pk))

        self.assertFalse(Contribution.objects.filter(pk=self.contribution.pk).exists())

    def test_the_author_stays_locked_while_another_teammate_has_confirmed(self):
        self.confirm(self.contribution, teammate=self.student_f)
        self.client.post(unconfirm_url(self.contribution.pk))
        self.login(self.student_b)

        self.client.post(edit_url(self.contribution.pk), {**VALID, "description": "Rewritten."})
        self.client.post(delete_url(self.contribution.pk))

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.description, "Ran the planning meeting.")


class ConfirmationCountTests(ConfirmContributionsTestCase):
    def test_the_count_starts_at_zero(self):
        contribution = self.teammates_contribution()

        self.assertRegex(self.row(self.team_page(), contribution), r"Confirmed by 0 teammates\b")

    def test_one_confirmation_is_singular(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_f)

        self.assertRegex(self.row(self.team_page(), contribution), r"Confirmed by 1 teammate\b")

    def test_the_count_includes_mine_and_everyone_elses(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_a)
        self.confirm(contribution, teammate=self.student_f)

        self.assertRegex(self.row(self.team_page(), contribution), r"Confirmed by 2 teammates\b")

    def test_each_row_counts_only_its_own_confirmations(self):
        confirmed = self.teammates_contribution()
        unconfirmed = self.teammates_contribution()
        self.confirm(confirmed, teammate=self.student_f)

        response = self.team_page()

        self.assertRegex(self.row(response, confirmed), r"Confirmed by 1 teammate\b")
        self.assertRegex(self.row(response, unconfirmed), r"Confirmed by 0 teammates\b")

    def test_the_page_does_not_name_who_confirmed(self):
        User.objects.filter(pk=self.student_f.pk).update(
            first_name="Hidden", last_name="Confirmer"
        )
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_f)

        text = page_text(self.team_page())

        self.assertNotIn("Hidden Confirmer", text)
        self.assertNotIn(self.student_f.github_login, text)


class AccessTests(ConfirmContributionsTestCase):
    def change_urls(self, contribution):
        return confirm_url(contribution.pk), unconfirm_url(contribution.pk)

    def people_outside_the_team(self):
        return {
            "student on another team in the project": self.student_c,
            "student on another project": self.student_d,
            "student on no team": self.student_e,
            "instructor": self.instructor,
            "ta": self.ta,
            "superuser": self.superuser,
            "not enrolled": self.outsider,
        }

    def test_someone_logged_out_is_sent_to_login(self):
        contribution = self.teammates_contribution()
        self.client.logout()

        for url in self.change_urls(contribution):
            with self.subTest(url=url):
                response = self.client.post(url)

                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))
                self.assertFalse(Corroboration.objects.exists())

        with self.subTest(url="team list"):
            response = self.team_page()

            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_people_not_on_a_team_in_the_project_cannot_open_the_page(self):
        people = self.people_outside_the_team()
        del people["student on another team in the project"]

        for label, person in people.items():
            self.login(person)
            with self.subTest(person=label):
                self.assertEqual(self.team_page().status_code, 403)

    def test_a_student_on_another_team_sees_only_their_own_team(self):
        self.teammates_contribution(description="Team A's meeting.")
        rival_teammate = self.make_student("student-g", self.rival_team)
        theirs = self.make_contribution(student=rival_teammate, team=self.rival_team)
        self.login(self.student_c)

        response = self.team_page()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.listed_ids(response), [theirs.pk])
        self.assertNotContains(response, "Team A's meeting.")

    def test_people_outside_the_team_cannot_confirm(self):
        contribution = self.teammates_contribution()

        for label, person in self.people_outside_the_team().items():
            self.login(person)
            with self.subTest(person=label):
                response = self.client.post(confirm_url(contribution.pk))

                self.assertEqual(response.status_code, 403)
                self.assertFalse(Corroboration.objects.exists())

    def test_people_outside_the_team_cannot_undo(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_a)

        for label, person in self.people_outside_the_team().items():
            self.login(person)
            with self.subTest(person=label):
                response = self.client.post(unconfirm_url(contribution.pk))

                self.assertEqual(response.status_code, 403)
                self.assertEqual(Corroboration.objects.count(), 1)

    def test_course_staff_on_the_team_are_still_not_students(self):
        TeamMembership.objects.create(user=self.ta, team=self.team)
        contribution = self.teammates_contribution()
        self.login(self.ta)

        self.assertEqual(self.team_page().status_code, 403)
        for url in self.change_urls(contribution):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)
                self.assertFalse(Corroboration.objects.exists())

    def test_a_contribution_that_does_not_exist_is_a_404(self):
        for url in (confirm_url(MISSING_ID), unconfirm_url(MISSING_ID)):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_a_project_that_does_not_exist_is_a_404(self):
        self.assertEqual(self.client.get(team_list_url(MISSING_ID)).status_code, 404)

    def test_a_get_cannot_confirm(self):
        contribution = self.teammates_contribution()

        response = self.client.get(confirm_url(contribution.pk))

        self.assertEqual(response.status_code, 405)
        self.assertFalse(Corroboration.objects.exists())

    def test_a_get_cannot_undo(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_a)

        response = self.client.get(unconfirm_url(contribution.pk))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Corroboration.objects.count(), 1)


class CsrfTests(ConfirmContributionsTestCase):
    def setUp(self):
        super().setUp()
        self.strict_client = Client(enforce_csrf_checks=True)
        self.strict_client.force_login(self.student_a)

    def test_both_forms_post_with_a_csrf_token(self):
        unconfirmed = self.teammates_contribution()
        confirmed = self.teammates_contribution()
        self.confirm(confirmed, teammate=self.student_a)

        response = self.team_page()

        for contribution in (unconfirmed, confirmed):
            with self.subTest(contribution=contribution.pk):
                self.assertIn("csrfmiddlewaretoken", self.row(response, contribution))

    def test_confirming_without_a_token_is_refused(self):
        contribution = self.teammates_contribution()

        response = self.strict_client.post(confirm_url(contribution.pk))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Corroboration.objects.exists())

    def test_undoing_without_a_token_is_refused(self):
        contribution = self.teammates_contribution()
        self.confirm(contribution, teammate=self.student_a)

        response = self.strict_client.post(unconfirm_url(contribution.pk))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Corroboration.objects.count(), 1)


class StaleConfirmationTests(ConfirmContributionsTestCase):
    """A confirmation has to be of the text the teammate read, not an edit made since."""

    STALE_MESSAGE = "read it again"

    def setUp(self):
        """Tick the clock a second per reading, so an edit changes `updated_at` as it would
        in real use. The shared fixture freezes it, which would make every edit look unchanged.
        """
        super().setUp()
        ticks = itertools.count()
        ticking = mock.patch(
            "django.utils.timezone.now",
            side_effect=lambda: NOW + timedelta(seconds=next(ticks)),
        )
        ticking.start()
        self.addCleanup(ticking.stop)

    def test_the_confirm_form_carries_the_version_the_page_showed(self):
        contribution = self.teammates_contribution()

        row = self.row(self.team_page(), contribution)

        self.assertRegex(
            row, r'<input\b(?=[^>]*\btype="hidden")(?=[^>]*\bname="updated_at")[^>]*>'
        )

    def test_an_edit_after_the_page_loaded_refuses_the_confirmation(self):
        contribution = self.teammates_contribution(description="Met with the client.")
        seen = self.seen_version(contribution)
        self.login(self.student_b)
        self.client.post(
            edit_url(contribution.pk), {**VALID, "description": "Designed the whole UI."}
        )
        self.login(self.student_a)

        response = self.client.post(
            confirm_url(contribution.pk), {"updated_at": seen}, follow=True
        )

        self.assertFalse(Corroboration.objects.exists())
        self.assertIn(self.STALE_MESSAGE, page_text(response).lower())
        self.assertIn("Designed the whole UI.", self.row(response, contribution))

    def test_after_re_reading_i_can_confirm_the_new_text(self):
        contribution = self.teammates_contribution()
        seen = self.seen_version(contribution)
        contribution.description = "Edited after the page loaded."
        contribution.save()
        self.client.post(confirm_url(contribution.pk), {"updated_at": seen})

        response = self.post_confirm(contribution)

        self.assertRedirects(response, team_list_url(self.project.id))
        self.assertEqual(Corroboration.objects.count(), 1)

    def test_a_confirmation_without_the_version_is_refused(self):
        contribution = self.teammates_contribution()

        response = self.client.post(confirm_url(contribution.pk), follow=True)

        self.assertFalse(Corroboration.objects.exists())
        self.assertIn(self.STALE_MESSAGE, page_text(response).lower())

    def test_a_garbled_version_is_refused(self):
        contribution = self.teammates_contribution()

        response = self.client.post(
            confirm_url(contribution.pk), {"updated_at": "not-a-time"}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Corroboration.objects.exists())
        self.assertIn(self.STALE_MESSAGE, page_text(response).lower())

    def test_an_edit_after_the_access_check_is_still_caught(self):
        """The version is compared under the row lock, against the database, not against
        the copy the view fetched before the transaction started."""
        contribution = self.teammates_contribution()
        seen = self.seen_version(contribution)
        fetch = services.get_team_contribution

        def fetch_then_edit(user, contribution_id):
            fetched = fetch(user, contribution_id)
            Contribution.objects.get(pk=contribution_id).save()
            return fetched

        with mock.patch(
            "contributions.services.get_team_contribution", side_effect=fetch_then_edit
        ):
            self.client.post(confirm_url(contribution.pk), {"updated_at": seen})

        self.assertFalse(Corroboration.objects.exists())


class ConfirmServiceRuleTests(ConfirmContributionsTestCase):
    """`confirm_contribution` enforces `Corroboration`'s own rules, whoever calls it."""

    def confirm_through_service(self, contribution, teammate):
        services.confirm_contribution(
            contribution, teammate, contribution.updated_at.isoformat()
        )

    def test_it_refuses_a_self_confirmation(self):
        mine = self.make_contribution()

        with self.assertRaises(PermissionDenied):
            self.confirm_through_service(mine, self.student_a)

        self.assertFalse(Corroboration.objects.exists())

    def test_it_refuses_someone_off_the_team(self):
        contribution = self.teammates_contribution()

        with self.assertRaises(PermissionDenied):
            self.confirm_through_service(contribution, self.student_c)

        self.assertFalse(Corroboration.objects.exists())

    def test_it_records_a_valid_confirmation_once(self):
        contribution = self.teammates_contribution()

        self.confirm_through_service(contribution, self.student_a)
        self.confirm_through_service(contribution, self.student_a)

        self.assertEqual(
            list(Corroboration.objects.values_list("contribution_id", "teammate_id")),
            [(contribution.pk, self.student_a.pk)],
        )

    def test_a_student_removed_after_the_access_check_cannot_confirm(self):
        """The team check runs again inside the transaction."""
        contribution = self.teammates_contribution()
        seen = self.seen_version(contribution)
        fetch = services.get_team_contribution

        def fetch_then_remove(user, contribution_id):
            fetched = fetch(user, contribution_id)
            TeamMembership.objects.filter(user=user, team=self.team).delete()
            return fetched

        with mock.patch(
            "contributions.services.get_team_contribution", side_effect=fetch_then_remove
        ):
            response = self.client.post(confirm_url(contribution.pk), {"updated_at": seen})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Corroboration.objects.exists())
