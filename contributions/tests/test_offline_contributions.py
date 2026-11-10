"""Tests for US-33 (#110): record work I did outside the code.

These drive the real pages, so they check what a student sees. URLs are reversed per test
rather than at module level, so each criterion passes or fails on its own instead of the
whole module failing on a single NoReverseMatch.

The issue leaves some names and wording open. These tests expect:
- URLs named `contributions:list` and `contributions:add` taking a project ID, and
  `contributions:edit` and `contributions:delete` taking a contribution ID.
- Each contribution in the list rendered in an element with `id="contribution-<pk>"`.
- "Newest first" meaning by the date of the work, then most recently added.
- The count worded "Confirmed by N teammate(s)".
- A locked contribution's reason including "a teammate has confirmed".
"""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from html import unescape
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contributions.forms import ContributionForm
from contributions.models import Contribution, Corroboration
from core.models import Course, Enrollment, Project, Sprint, Team, TeamMembership
from dashboard.models import Flag
from ingest.models import Artifact

Role = Enrollment.Role
User = get_user_model()

# 10pm on Sep 3 in New York, but already Sep 4 in UTC, so using UTC's date gets caught.
NOW = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)
TODAY = date(2026, 9, 3)
TOMORROW = date(2026, 9, 4)

LOCKED_REASON = "a teammate has confirmed"
MISSING_ID = 999_999

VALID = {
    "date": "2026-08-12",
    "category": "meeting",
    "description": "Ran the planning meeting.",
    "hours": "1.5",
}


def list_url(project_id):
    return reverse("contributions:list", args=[project_id])


def add_url(project_id):
    return reverse("contributions:add", args=[project_id])


def edit_url(contribution_id):
    return reverse("contributions:edit", args=[contribution_id])


def delete_url(contribution_id):
    return reverse("contributions:delete", args=[contribution_id])


def make_user(login, **extra):
    return User.objects.create_user(
        username=login, password="not-a-real-password", github_login=login, **extra
    )


def page_text(response):
    """The page's HTML with entities decoded, so "can't" can be matched as written."""
    return unescape(response.content.decode())


def without(data, field):
    return {key: value for key, value in data.items() if key != field}


class OfflineContributionsTestCase(TestCase):
    """Made-up students: A, B and F share a team, C is on another team in the same project,
    D is on a team in a different project and E is enrolled but on no team."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="Course 1")
        cls.project = Project.objects.create(course=cls.course, name="Project 1")
        cls.other_project = Project.objects.create(course=cls.course, name="Project 2")
        cls.team = Team.objects.create(project=cls.project, name="Team A")
        cls.rival_team = Team.objects.create(project=cls.project, name="Team B")
        cls.far_team = Team.objects.create(project=cls.other_project, name="Team C")

        cls.student_a = cls.make_student("student-a", cls.team)
        cls.student_b = cls.make_student("student-b", cls.team)
        cls.student_f = cls.make_student("student-f", cls.team)
        cls.student_c = cls.make_student("student-c", cls.rival_team)
        cls.student_d = cls.make_student("student-d", cls.far_team)
        cls.student_e = cls.make_student("student-e")

        cls.instructor = make_user("instructor-1")
        Enrollment.objects.create(user=cls.instructor, course=cls.course, role=Role.INSTRUCTOR)
        cls.ta = make_user("ta-1")
        Enrollment.objects.create(user=cls.ta, course=cls.course, role=Role.TA)
        cls.superuser = User.objects.create_superuser(
            username="admin-1", password="not-a-real-password", github_login="admin-1"
        )
        cls.outsider = make_user("outsider-1")

        # Sprint 1 and Sprint 2 share Aug 17. All of August is before NOW, so these have ended.
        Sprint.objects.create(
            project=cls.project,
            name="Sprint 1",
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 17),
        )
        Sprint.objects.create(
            project=cls.project,
            name="Sprint 2",
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 24),
        )
        Sprint.objects.create(
            project=cls.other_project,
            name="Far sprint",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
        )

    @classmethod
    def make_student(cls, login, team=None):
        student = make_user(login)
        Enrollment.objects.create(user=student, course=cls.course, role=Role.STUDENT)
        if team is not None:
            TeamMembership.objects.create(user=student, team=team)
        return student

    def setUp(self):
        frozen = mock.patch("django.utils.timezone.now", return_value=NOW)
        frozen.start()
        self.addCleanup(frozen.stop)
        self.client.force_login(self.student_a)

    def login(self, user):
        self.client.force_login(user)

    def make_contribution(self, student=None, team=None, **fields):
        values = {
            "date": date(2026, 8, 12),
            "category": Contribution.Category.MEETING,
            "description": "Ran the planning meeting.",
            "hours": Decimal("1.50"),
        }
        values.update(fields)
        return Contribution.objects.create(
            student=student or self.student_a, team=team or self.team, **values
        )

    def confirm(self, contribution, teammate=None):
        return Corroboration.objects.create(
            contribution=contribution, teammate=teammate or self.student_b
        )

    def row(self, response, contribution):
        """The HTML of one contribution in the list, with whitespace collapsed."""
        match = re.search(
            rf'<(\w+)\b[^>]*\bid="contribution-{contribution.pk}"[^>]*>(.*?)</\1>',
            page_text(response),
            re.DOTALL,
        )
        self.assertIsNotNone(match, f"contribution {contribution.pk} isn't listed")
        return " ".join(match.group(2).split())

    def listed_ids(self, response):
        return [int(pk) for pk in re.findall(r'id="contribution-(\d+)"', page_text(response))]


class ContributionListTests(OfflineContributionsTestCase):
    def test_the_list_links_to_add_contribution(self):
        response = self.client.get(list_url(self.project.id))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My offline contributions")
        self.assertContains(response, "Add contribution")
        self.assertContains(response, f'href="{add_url(self.project.id)}"')

    def test_each_row_shows_what_was_recorded(self):
        contribution = self.make_contribution(
            category=Contribution.Category.USER_RESEARCH, description="Interviewed two users."
        )

        row = self.row(self.client.get(list_url(self.project.id)), contribution)

        self.assertIn("Interviewed two users.", row)
        self.assertIn("User research", row)
        self.assertIn("Aug 12, 2026", row)

    def test_newest_work_comes_first(self):
        middle = self.make_contribution(date=date(2026, 8, 12))
        oldest = self.make_contribution(date=date(2026, 8, 5))
        newest = self.make_contribution(date=date(2026, 8, 20))

        response = self.client.get(list_url(self.project.id))

        self.assertEqual(self.listed_ids(response), [newest.pk, middle.pk, oldest.pk])

    def test_work_on_the_same_day_lists_the_latest_added_first(self):
        first = self.make_contribution()
        second = self.make_contribution()

        response = self.client.get(list_url(self.project.id))

        self.assertEqual(self.listed_ids(response), [second.pk, first.pk])

    def test_only_this_projects_contributions_are_listed(self):
        TeamMembership.objects.create(user=self.student_a, team=self.far_team)
        here = self.make_contribution()
        elsewhere = self.make_contribution(team=self.far_team, description="Work on Project 2.")

        response = self.client.get(list_url(self.project.id))

        self.assertEqual(self.listed_ids(response), [here.pk])
        self.assertNotIn(f"contribution-{elsewhere.pk}", page_text(response))
        self.assertNotContains(response, "Work on Project 2.")

    def test_a_row_shows_the_sprint_its_date_falls_in(self):
        contribution = self.make_contribution(date=date(2026, 8, 20))

        row = self.row(self.client.get(list_url(self.project.id)), contribution)

        self.assertIn("Sprint 2", row)
        self.assertNotIn("Sprint 1", row)
        self.assertNotIn("No sprint", row)

    def test_a_day_shared_by_two_sprints_shows_both(self):
        contribution = self.make_contribution(date=date(2026, 8, 17))

        row = self.row(self.client.get(list_url(self.project.id)), contribution)

        self.assertIn("Sprint 1", row)
        self.assertIn("Sprint 2", row)

    def test_a_date_outside_every_sprint_shows_no_sprint(self):
        contribution = self.make_contribution(date=date(2026, 8, 3))

        row = self.row(self.client.get(list_url(self.project.id)), contribution)

        self.assertIn("No sprint", row)
        # Another project's sprint covers Aug 3, and mustn't be picked up.
        self.assertNotIn("Far sprint", row)

    def test_a_row_shows_how_many_teammates_have_confirmed_it(self):
        none_yet = self.make_contribution(description="Nobody confirmed this.")
        one = self.make_contribution(description="One teammate confirmed this.")
        self.confirm(one)
        two = self.make_contribution(description="Two teammates confirmed this.")
        self.confirm(two)
        self.confirm(two, self.student_f)

        response = self.client.get(list_url(self.project.id))

        self.assertIn("Confirmed by 0 teammates", self.row(response, none_yet))
        self.assertIn("Confirmed by 1 teammate", self.row(response, one))
        self.assertNotIn("1 teammates", self.row(response, one))
        self.assertIn("Confirmed by 2 teammates", self.row(response, two))

    def test_descriptions_are_escaped(self):
        self.make_contribution(description="<script>alert(1)</script>")

        response = self.client.get(list_url(self.project.id))

        self.assertNotContains(response, "<script>alert(1)</script>")


class AddContributionTests(OfflineContributionsTestCase):
    def assert_rejected(self, data, field, message):
        """Nothing is stored, and the page shows an error on `field` matching `message`."""
        response = self.client.post(add_url(self.project.id), data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contribution.objects.exists())
        errors = " ".join(response.context["form"].errors.get(field, []))
        self.assertRegex(errors, message)
        self.assertIn(errors, page_text(response))

    def test_the_form_offers_every_category(self):
        response = self.client.get(add_url(self.project.id))

        self.assertEqual(response.status_code, 200)
        for value, label in Contribution.Category.choices:
            with self.subTest(category=value):
                self.assertContains(response, f'value="{value}"')
                self.assertContains(response, label)

    def test_a_valid_contribution_is_stored_for_me_and_my_team(self):
        response = self.client.post(add_url(self.project.id), VALID)

        self.assertRedirects(response, list_url(self.project.id))
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.student, self.student_a)
        self.assertEqual(contribution.team, self.team)
        self.assertEqual(contribution.date, date(2026, 8, 12))
        self.assertEqual(contribution.category, Contribution.Category.MEETING)
        self.assertEqual(contribution.description, "Ran the planning meeting.")
        self.assertEqual(contribution.hours, Decimal("1.5"))

    def test_hours_are_optional(self):
        for data in (without(VALID, "hours"), {**VALID, "hours": ""}):
            with self.subTest(data=data):
                Contribution.objects.all().delete()

                response = self.client.post(add_url(self.project.id), data)

                self.assertRedirects(response, list_url(self.project.id))
                self.assertIsNone(Contribution.objects.get().hours)

    def test_date_category_and_description_are_required(self):
        for field in ("date", "category", "description"):
            with self.subTest(field=field):
                self.assert_rejected(without(VALID, field), field, "required")

    def test_a_blank_description_is_rejected(self):
        self.assert_rejected({**VALID, "description": "   \n "}, "description", "required")

    def test_an_unknown_category_is_rejected(self):
        self.assert_rejected({**VALID, "category": "napping"}, "category", "valid choice")

    def test_a_description_can_be_1000_characters(self):
        response = self.client.post(
            add_url(self.project.id), {**VALID, "description": "x" * 1000}
        )

        self.assertRedirects(response, list_url(self.project.id))
        self.assertEqual(len(Contribution.objects.get().description), 1000)

    def test_a_description_over_1000_characters_is_rejected(self):
        self.assert_rejected({**VALID, "description": "x" * 1001}, "description", r"1,?000")

    def test_a_line_break_counts_as_one_character(self):
        # Browsers count a line break as one character toward maxlength but post it as two.
        response = self.client.post(
            add_url(self.project.id), {**VALID, "description": "x" * 499 + "\r\n" + "x" * 500}
        )

        self.assertRedirects(response, list_url(self.project.id))
        self.assertEqual(Contribution.objects.get().description, "x" * 499 + "\n" + "x" * 500)

    def test_line_breaks_still_count_toward_the_limit(self):
        self.assert_rejected(
            {**VALID, "description": "x" * 500 + "\r\n" + "x" * 500}, "description", r"1,?000"
        )

    def test_the_date_picker_stops_at_today_in_new_york(self):
        # Sep 4 in Tokyo and in UTC at NOW, but still Sep 3 for the reporting dates.
        with timezone.override("Asia/Tokyo"):
            form = ContributionForm(instance=Contribution(team=self.team, student=self.student_a))

        self.assertEqual(form.fields["date"].widget.attrs["max"], TODAY.isoformat())

    def test_a_date_in_the_future_is_rejected(self):
        # Sep 4 is already today in UTC, but it's still tomorrow in New York.
        self.assert_rejected(
            {**VALID, "date": TOMORROW.isoformat()}, "date", "can't be in the future"
        )

    def test_today_is_accepted(self):
        response = self.client.post(add_url(self.project.id), {**VALID, "date": TODAY.isoformat()})

        self.assertRedirects(response, list_url(self.project.id))
        self.assertEqual(Contribution.objects.get().date, TODAY)

    def test_work_can_be_logged_after_its_sprint_has_ended(self):
        self.client.post(add_url(self.project.id), {**VALID, "date": "2026-08-20"})

        contribution = Contribution.objects.get()
        row = self.row(self.client.get(list_url(self.project.id)), contribution)
        self.assertIn("Sprint 2", row)

    def test_hours_outside_the_limits_are_rejected(self):
        for hours, limit in (("0.24", "0.25"), ("0", "0.25"), ("-1", "0.25"), ("24.25", "24")):
            with self.subTest(hours=hours):
                self.assert_rejected({**VALID, "hours": hours}, "hours", limit)

    def test_hours_on_the_limits_are_accepted(self):
        for hours in ("0.25", "24"):
            with self.subTest(hours=hours):
                Contribution.objects.all().delete()

                response = self.client.post(add_url(self.project.id), {**VALID, "hours": hours})

                self.assertRedirects(response, list_url(self.project.id))
                self.assertEqual(Contribution.objects.get().hours, Decimal(hours))

    def test_what_i_typed_is_kept_when_rejected(self):
        response = self.client.post(
            add_url(self.project.id),
            {
                "date": TOMORROW.isoformat(),
                "category": "design",
                "description": "Sketched the new layout.",
                "hours": "2.75",
            },
        )

        content = page_text(response)
        self.assertIn('value="2026-09-04"', content)
        self.assertRegex(content, r'value="design"[^>]*\bselected')
        self.assertIn("Sketched the new layout.", content)
        self.assertIn('value="2.75"', content)

    def test_i_cannot_add_for_someone_else_or_another_team(self):
        response = self.client.post(
            add_url(self.project.id),
            {**VALID, "student": self.student_b.pk, "team": self.rival_team.pk},
        )

        self.assertRedirects(response, list_url(self.project.id))
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.student, self.student_a)
        self.assertEqual(contribution.team, self.team)

    def test_the_add_page_says_what_contributions_are_for(self):
        content = page_text(self.client.get(add_url(self.project.id))).lower()

        self.assertIn("instructor", content)
        self.assertIn("self-reported", content)
        self.assertIn("summary", content)
        self.assertIn("flags", content)

    def test_adding_creates_no_evidence_or_flags(self):
        self.client.post(add_url(self.project.id), VALID)

        self.assertEqual(Contribution.objects.count(), 1)
        self.assertFalse(Artifact.objects.exists())
        self.assertFalse(Flag.objects.exists())


class EditContributionTests(OfflineContributionsTestCase):
    def test_the_form_starts_with_the_current_values(self):
        contribution = self.make_contribution(
            category=Contribution.Category.DESIGN, description="Sketched the new layout."
        )

        response = self.client.get(edit_url(contribution.pk))

        self.assertEqual(response.status_code, 200)
        content = page_text(response)
        self.assertIn('value="2026-08-12"', content)
        self.assertRegex(content, r'value="design"[^>]*\bselected')
        self.assertIn("Sketched the new layout.", content)
        self.assertRegex(content, r'value="1\.50?"')

    def test_opening_the_form_changes_nothing(self):
        contribution = self.make_contribution()
        before = Contribution.objects.filter(pk=contribution.pk).values().get()

        self.client.get(edit_url(contribution.pk))

        self.assertEqual(Contribution.objects.filter(pk=contribution.pk).values().get(), before)

    def test_i_can_edit_my_unconfirmed_contribution(self):
        contribution = self.make_contribution()

        response = self.client.post(
            edit_url(contribution.pk),
            {
                "date": "2026-08-20",
                "category": "planning",
                "description": "Planned the next sprint.",
                "hours": "3",
            },
        )

        self.assertRedirects(response, list_url(self.project.id))
        contribution.refresh_from_db()
        self.assertEqual(contribution.date, date(2026, 8, 20))
        self.assertEqual(contribution.category, Contribution.Category.PLANNING)
        self.assertEqual(contribution.description, "Planned the next sprint.")
        self.assertEqual(contribution.hours, Decimal("3"))

    def test_editing_uses_the_same_rules_as_adding(self):
        contribution = self.make_contribution()
        cases = (
            ({**VALID, "date": TOMORROW.isoformat()}, "date", "can't be in the future"),
            ({**VALID, "description": "   "}, "description", "required"),
            ({**VALID, "description": "x" * 1001}, "description", r"1,?000"),
            ({**VALID, "hours": "24.25"}, "hours", "24"),
        )

        for data, field, message in cases:
            with self.subTest(field=field, message=message):
                response = self.client.post(edit_url(contribution.pk), data)

                self.assertEqual(response.status_code, 200)
                self.assertRegex(" ".join(response.context["form"].errors[field]), message)
                contribution.refresh_from_db()
                self.assertEqual(contribution.description, "Ran the planning meeting.")
                self.assertEqual(contribution.date, date(2026, 8, 12))

    def test_what_i_typed_is_kept_when_an_edit_is_rejected(self):
        contribution = self.make_contribution()

        response = self.client.post(
            edit_url(contribution.pk),
            {**VALID, "date": TOMORROW.isoformat(), "description": "My rewritten description."},
        )

        content = page_text(response)
        self.assertIn('value="2026-09-04"', content)
        self.assertIn("My rewritten description.", content)

    def test_an_edit_cannot_move_it_to_someone_else_or_another_team(self):
        contribution = self.make_contribution()

        self.client.post(
            edit_url(contribution.pk),
            {**VALID, "student": self.student_b.pk, "team": self.rival_team.pk},
        )

        contribution.refresh_from_db()
        self.assertEqual(contribution.student, self.student_a)
        self.assertEqual(contribution.team, self.team)

    def test_an_unconfirmed_row_links_to_edit_and_delete(self):
        contribution = self.make_contribution()

        row = self.row(self.client.get(list_url(self.project.id)), contribution)

        self.assertIn(f'href="{edit_url(contribution.pk)}"', row)
        self.assertIn(f'href="{delete_url(contribution.pk)}"', row)
        self.assertNotIn(LOCKED_REASON, row.lower())


class DeleteContributionTests(OfflineContributionsTestCase):
    def test_deleting_asks_for_confirmation_first(self):
        contribution = self.make_contribution(description="Interviewed two users.")

        response = self.client.get(delete_url(contribution.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Interviewed two users.")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_confirming_deletes_my_unconfirmed_contribution(self):
        contribution = self.make_contribution()
        kept = self.make_contribution(description="Another one.")

        response = self.client.post(delete_url(contribution.pk))

        self.assertRedirects(response, list_url(self.project.id))
        self.assertEqual(list(Contribution.objects.all()), [kept])


class ConfirmedContributionTests(OfflineContributionsTestCase):
    """Once a teammate confirms a contribution, what they read has to stay as it was."""

    def setUp(self):
        super().setUp()
        self.contribution = self.make_contribution()
        self.confirm(self.contribution)
        self.before = Contribution.objects.filter(pk=self.contribution.pk).values().get()

    def assert_unchanged(self):
        self.assertEqual(
            Contribution.objects.filter(pk=self.contribution.pk).values().get(), self.before
        )

    def test_the_row_has_no_edit_or_delete_and_says_why(self):
        row = self.row(self.client.get(list_url(self.project.id)), self.contribution)

        self.assertNotIn(edit_url(self.contribution.pk), row)
        self.assertNotIn(delete_url(self.contribution.pk), row)
        self.assertIn(LOCKED_REASON, row.lower())

    def test_opening_edit_or_delete_says_why(self):
        for url in (edit_url(self.contribution.pk), delete_url(self.contribution.pk)):
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)

                self.assertIn(LOCKED_REASON, page_text(response).lower())
                self.assert_unchanged()

    def test_an_edit_is_refused_and_says_why(self):
        response = self.client.post(
            edit_url(self.contribution.pk),
            {**VALID, "description": "Quietly rewritten."},
            follow=True,
        )

        self.assertIn(LOCKED_REASON, page_text(response).lower())
        self.assert_unchanged()

    def test_a_delete_is_refused_and_says_why(self):
        response = self.client.post(delete_url(self.contribution.pk), follow=True)

        self.assertIn(LOCKED_REASON, page_text(response).lower())
        self.assert_unchanged()


class ConfirmationDuringAChangeTests(OfflineContributionsTestCase):
    """A teammate confirms just after the page's first check that nobody has."""

    def confirmed_after_the_check(self, contribution):
        def confirm_then_say_unlocked(_):
            Corroboration.objects.get_or_create(
                contribution=contribution, teammate=self.student_b
            )
            return False

        return mock.patch(
            "contributions.services.is_locked", side_effect=confirm_then_say_unlocked
        )

    def test_the_edit_is_refused(self):
        contribution = self.make_contribution()

        with self.confirmed_after_the_check(contribution):
            response = self.client.post(
                edit_url(contribution.pk),
                {**VALID, "description": "Rewritten just after being confirmed."},
                follow=True,
            )

        self.assertIn(LOCKED_REASON, page_text(response).lower())
        contribution.refresh_from_db()
        self.assertEqual(contribution.description, "Ran the planning meeting.")

    def test_the_delete_is_refused_and_the_confirmation_survives(self):
        contribution = self.make_contribution()

        with self.confirmed_after_the_check(contribution):
            response = self.client.post(delete_url(contribution.pk), follow=True)

        self.assertIn(LOCKED_REASON, page_text(response).lower())
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())
        self.assertEqual(Corroboration.objects.count(), 1)


class ModelLimitTests(OfflineContributionsTestCase):
    """The admin and scripts don't go through the students' form."""

    def contribution_with_description(self, description):
        return Contribution(
            team=self.team,
            student=self.student_a,
            date=date(2026, 8, 12),
            category=Contribution.Category.MEETING,
            description=description,
        )

    def test_the_model_accepts_1000_characters(self):
        self.contribution_with_description("x" * 1000).full_clean()

    def test_the_model_rejects_more_than_1000_characters(self):
        with self.assertRaises(ValidationError) as caught:
            self.contribution_with_description("x" * 1001).full_clean()

        self.assertIn("description", caught.exception.message_dict)


class AccessTests(OfflineContributionsTestCase):
    def project_urls(self):
        return list_url(self.project.id), add_url(self.project.id)

    def contribution_urls(self, contribution):
        return edit_url(contribution.pk), delete_url(contribution.pk)

    def test_someone_logged_out_is_sent_to_login(self):
        contribution = self.make_contribution()
        self.client.logout()

        for url in (*self.project_urls(), *self.contribution_urls(contribution)):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_people_who_are_not_students_on_the_project_get_a_403(self):
        people = {
            "instructor": self.instructor,
            "ta": self.ta,
            "superuser": self.superuser,
            "not enrolled": self.outsider,
            "student on no team": self.student_e,
            "student on another project": self.student_d,
        }

        for label, person in people.items():
            self.login(person)
            for url in self.project_urls():
                with self.subTest(person=label, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            with self.subTest(person=label, action="add"):
                response = self.client.post(add_url(self.project.id), VALID)

                self.assertEqual(response.status_code, 403)
                self.assertFalse(Contribution.objects.exists())

    def test_course_staff_on_a_team_are_still_not_students(self):
        TeamMembership.objects.create(user=self.ta, team=self.rival_team)
        self.login(self.ta)

        for url in self.project_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_students_on_any_team_in_the_project_can_use_it(self):
        self.login(self.student_c)

        for url in self.project_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_someone_elses_contribution_is_not_listed(self):
        mine = self.make_contribution()
        self.make_contribution(student=self.student_b, description="Student B's meeting.")

        response = self.client.get(list_url(self.project.id))

        self.assertEqual(self.listed_ids(response), [mine.pk])
        self.assertNotContains(response, "Student B's meeting.")

    def test_i_cannot_open_someone_elses_contribution(self):
        theirs = self.make_contribution(student=self.student_b, description="Student B's meeting.")

        for url in self.contribution_urls(theirs):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 403)
                self.assertNotContains(
                    response, "Student B's meeting.", status_code=403
                )

    def test_i_cannot_edit_someone_elses_contribution(self):
        theirs = self.make_contribution(student=self.student_b)

        response = self.client.post(
            edit_url(theirs.pk), {**VALID, "description": "Rewritten by a teammate."}
        )

        self.assertEqual(response.status_code, 403)
        theirs.refresh_from_db()
        self.assertEqual(theirs.description, "Ran the planning meeting.")

    def test_i_cannot_delete_someone_elses_contribution(self):
        theirs = self.make_contribution(student=self.student_b)

        response = self.client.post(delete_url(theirs.pk))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Contribution.objects.filter(pk=theirs.pk).exists())

    def test_course_staff_cannot_change_a_students_contribution(self):
        contribution = self.make_contribution()

        for person in (self.instructor, self.ta, self.superuser):
            self.login(person)
            for url in self.contribution_urls(contribution):
                with self.subTest(person=person.username, url=url):
                    self.assertEqual(self.client.post(url, VALID).status_code, 403)
                    contribution.refresh_from_db()
                    self.assertEqual(contribution.description, "Ran the planning meeting.")

    def test_a_project_that_does_not_exist_is_a_404(self):
        for url in (list_url(MISSING_ID), add_url(MISSING_ID)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_contribution_that_does_not_exist_is_a_404(self):
        for url in (edit_url(MISSING_ID), delete_url(MISSING_ID)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, VALID).status_code, 404)


class CsrfTests(OfflineContributionsTestCase):
    def setUp(self):
        super().setUp()
        self.strict_client = Client(enforce_csrf_checks=True)
        self.strict_client.force_login(self.student_a)

    def test_every_form_posts_with_a_csrf_token(self):
        contribution = self.make_contribution()

        for url in (
            add_url(self.project.id),
            edit_url(contribution.pk),
            delete_url(contribution.pk),
        ):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "csrfmiddlewaretoken")

    def test_adding_without_a_token_is_refused(self):
        response = self.strict_client.post(add_url(self.project.id), VALID)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Contribution.objects.exists())

    def test_editing_without_a_token_is_refused(self):
        contribution = self.make_contribution()

        response = self.strict_client.post(
            edit_url(contribution.pk), {**VALID, "description": "Changed."}
        )

        self.assertEqual(response.status_code, 403)
        contribution.refresh_from_db()
        self.assertEqual(contribution.description, "Ran the planning meeting.")

    def test_deleting_without_a_token_is_refused(self):
        contribution = self.make_contribution()

        response = self.strict_client.post(delete_url(contribution.pk))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())


class StudentHomeLinkTests(OfflineContributionsTestCase):
    def test_a_project_on_the_student_home_links_to_my_offline_contributions(self):
        response = self.client.get(reverse("dashboard:student_home"))

        self.assertContains(response, "My offline contributions")
        self.assertContains(response, f'href="{list_url(self.project.id)}"')
