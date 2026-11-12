"""Tests for US-39 (#121): set up sprints and each sprint's requirements.

These drive the real pages. Only the LLM is replaced. "Today" is pinned by patching
django.utils.timezone.now. Every name here is made up.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Requirement, Sprint, Team
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user
from .test_generate_summary import page_messages
from .test_project_setup import llm_returns

Role = Enrollment.Role
NY = ZoneInfo("America/New_York")
# 9:00pm on Mon Nov 9 in New York is already Nov 10 in UTC: "today" must be the New York date.
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=NY)


def url(name, *args):
    return reverse(f"dashboard:{name}", args=args)


class SprintFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.other_project = Project.objects.create(course=cls.other_course, name="Other Project")

    def setUp(self):
        self.client.force_login(self.instructor)
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sprint(self, name="Sprint 1", start=date(2026, 10, 1), end=date(2026, 10, 7), project=None):
        return Sprint.objects.create(
            project=project or self.project, name=name, start_date=start, end_date=end
        )

    def requirement(self, sprint, number, text="Users can log in"):
        return Requirement.objects.create(sprint=sprint, number=number, text=text)

    def summary(self, sprint):
        team = Team.objects.create(project=sprint.project, name=f"Team {Team.objects.count()}")
        student = make_user(f"sample-student-{get_user_model().objects.count()}")
        enroll(student, self.course, Role.STUDENT)
        return Summary.objects.create(team=team, user=student, sprint=sprint)

    def cite(self, sprint, number):
        summary = self.summary(sprint)
        return Claim.objects.create(
            summary=summary, text="Did it.", category="feature", significance="minor",
            artifact_ids=[], requirement_ids=[number], status=Claim.Status.VERIFIED,
        )

    def create(self, name="Sprint 1", start="2026-11-09", end="2026-11-15", follow=True):
        return self.client.post(
            url("sprint_create", self.project.id),
            {"name": name, "start_date": start, "end_date": end},
            follow=follow,
        )


class SprintsPanelTests(SprintFixture):
    def page(self, project=None):
        return self.client.get(url("project_setup", (project or self.project).id))

    def test_the_sprints_panel_replaces_the_requirements_panel(self):
        page = self.page()
        self.assertContains(page, "Sprints")
        self.assertNotContains(page, "Add requirements")
        self.assertNotContains(page, "Edit requirements")

    def test_it_says_when_there_are_no_sprints_and_has_a_new_sprint_button(self):
        page = self.page()
        self.assertContains(page, "No sprints yet")
        self.assertContains(page, "New sprint")
        self.assertContains(page, url("sprint_create", self.project.id))

    def test_it_lists_sprints_in_date_order_with_dates_and_requirement_counts(self):
        late = self.sprint("Sprint 2", date(2026, 10, 8), date(2026, 10, 14))
        early = self.sprint("Sprint 1")
        self.requirement(early, 1)
        self.requirement(early, 2)
        html = self.page().content.decode()
        self.assertLess(html.index("Sprint 1"), html.index("Sprint 2"))
        self.assertIn("Oct 1", html)
        self.assertIn("Oct 7", html)
        self.assertIn("2 requirements", html)
        self.assertIn("No requirements yet", html)
        self.assertIn(url("sprint", early.id), html)
        self.assertIn(url("sprint", late.id), html)


class NewSprintPageTests(SprintFixture):
    def page(self):
        return self.client.get(url("sprint_create", self.project.id))

    def test_it_asks_for_a_name_and_two_date_fields(self):
        page = self.page()
        self.assertContains(page, 'name="name"')
        self.assertContains(page, 'type="date"', count=2)

    def test_a_first_sprint_defaults_to_today_in_new_york_through_six_days_later(self):
        page = self.page()
        self.assertContains(page, 'value="2026-11-09"')  # Not Nov 10, which is today in UTC.
        self.assertContains(page, 'value="2026-11-15"')

    def test_later_sprints_start_on_the_latest_sprints_end_date_by_default(self):
        self.sprint("Sprint 1", date(2026, 10, 1), date(2026, 10, 7))
        self.sprint("Sprint 2", date(2026, 10, 7), date(2026, 10, 20))
        page = self.page()
        self.assertContains(page, 'value="2026-10-20"')
        self.assertContains(page, 'value="2026-10-26"')

    def test_any_valid_range_can_be_saved_starting_on_any_weekday(self):
        response = self.create(start="2026-11-12", end="2026-12-20")  # A Thursday, 39 days.
        sprint = Sprint.objects.get()
        self.assertEqual((sprint.start_date, sprint.end_date), (date(2026, 11, 12), date(2026, 12, 20)))
        self.assertRedirects(response, url("sprint", sprint.id))

    def test_the_new_sprint_gets_no_requirements_and_says_so(self):
        response = self.create()
        self.assertContains(response, "No requirements yet")

    def test_one_sprints_last_day_can_be_the_next_ones_first_day(self):
        self.sprint("Sprint 1", date(2026, 11, 2), date(2026, 11, 9))
        self.create(name="Sprint 2", start="2026-11-09", end="2026-11-16")
        self.assertEqual(Sprint.objects.count(), 2)

    def test_bad_input_is_rejected_with_a_message_and_what_was_typed_is_kept(self):
        self.sprint("Sprint 1", date(2026, 11, 2), date(2026, 11, 9))
        cases = [
            ("", "2026-11-12", "2026-11-19", "name"),
            ("sprint 1", "2026-11-12", "2026-11-19", "already has a sprint named"),
            ("Sprint 2", "2026-11-19", "2026-11-12", "end before it starts"),
            ("Sprint 2", "2026-11-05", "2026-11-12", "overlap"),
            ("Sprint 2", "2026-11-02", "2026-11-12", "overlap"),
        ]
        for name, start, end, expected in cases:
            response = self.create(name=name, start=start, end=end, follow=False)
            self.assertEqual(response.status_code, 200, (name, start))
            self.assertContains(response, expected, msg_prefix=str((name, start)))
            self.assertContains(response, f'value="{start}"')
            self.assertContains(response, f'value="{end}"')
            if name:
                self.assertContains(response, f'value="{name}"')
        self.assertEqual(Sprint.objects.count(), 1)

    def test_a_missing_or_malformed_date_is_rejected(self):
        response = self.create(start="", end="not-a-date", follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Sprint.objects.count(), 0)


class EditSprintTests(SprintFixture):
    def setUp(self):
        super().setUp()
        self.target = self.sprint("Sprint 1", date(2026, 10, 1), date(2026, 10, 7))

    def edit(self, **data):
        data.setdefault("name", self.target.name)
        data.setdefault("start_date", "2026-10-01")
        data.setdefault("end_date", "2026-10-07")
        return self.client.post(url("sprint_edit", self.target.id), data, follow=True)

    def test_the_sprint_page_has_an_edit_link_that_shows_the_form(self):
        page = self.client.get(url("sprint", self.target.id))
        self.assertContains(page, "?edit=1")
        form = self.client.get(url("sprint", self.target.id) + "?edit=1")
        self.assertContains(form, 'name="start_date"')

    def test_it_can_be_renamed_and_its_dates_changed_before_it_has_summaries(self):
        self.edit(name="Kickoff", start_date="2026-10-02", end_date="2026-10-09")
        self.target.refresh_from_db()
        self.assertEqual(
            (self.target.name, self.target.start_date, self.target.end_date),
            ("Kickoff", date(2026, 10, 2), date(2026, 10, 9)),
        )

    def test_the_same_dates_and_name_can_be_resaved(self):
        self.edit()
        self.assertEqual(Sprint.objects.count(), 1)

    def test_once_it_has_summaries_it_can_be_renamed_but_its_dates_cannot_change(self):
        self.summary(self.target)
        page = self.client.get(url("sprint", self.target.id) + "?edit=1")
        self.assertContains(page, "has summaries")
        self.edit(name="Kickoff", start_date="2026-10-02", end_date="2026-10-20")
        self.target.refresh_from_db()
        self.assertEqual(self.target.name, "Kickoff")
        self.assertEqual((self.target.start_date, self.target.end_date), (date(2026, 10, 1), date(2026, 10, 7)))

    def test_edits_are_checked_like_new_sprints_and_keep_what_was_typed(self):
        self.sprint("Sprint 2", date(2026, 10, 7), date(2026, 10, 14))
        response = self.client.post(
            url("sprint_edit", self.target.id),
            {"name": "sprint 2", "start_date": "2026-10-01", "end_date": "2026-10-10"},
        )
        self.assertContains(response, "already has a sprint named")
        self.assertContains(response, "overlap")
        self.assertContains(response, 'value="2026-10-10"')
        self.target.refresh_from_db()
        self.assertEqual(self.target.name, "Sprint 1")


class DeleteSprintTests(SprintFixture):
    def setUp(self):
        super().setUp()
        self.target = self.sprint("Sprint 1")
        self.kept = self.sprint("Sprint 2", date(2026, 10, 8), date(2026, 10, 14))

    def test_the_button_asks_to_confirm_and_says_what_goes_with_the_sprint(self):
        page = self.client.get(url("sprint", self.target.id))
        html = page.content.decode()
        self.assertIn("confirm(", html)
        for text in ("requirements", "summaries", "flags"):
            self.assertIn(text, html[html.index("confirm("):][:400])

    def test_deleting_removes_its_requirements_and_summaries_and_nothing_else(self):
        self.requirement(self.target, 1)
        self.requirement(self.kept, 1, "Keep me")
        self.summary(self.target)
        kept_summary = self.summary(self.kept)
        response = self.client.post(url("sprint_delete", self.target.id), follow=True)
        self.assertRedirects(response, url("project_setup", self.project.id))
        self.assertFalse(Sprint.objects.filter(pk=self.target.pk).exists())
        self.assertFalse(Requirement.objects.filter(text="Users can log in").exists())
        self.assertEqual(Summary.objects.get(), kept_summary)
        self.assertEqual(self.kept.requirements.count(), 1)
        self.assertIn("Deleted Sprint 1", page_messages(response))


class SprintRequirementsTests(SprintFixture):
    def setUp(self):
        super().setUp()
        self.target = self.sprint("Sprint 2", date(2026, 10, 8), date(2026, 10, 14))
        self.earlier = self.sprint("Sprint 1")

    def numbers(self, sprint=None):
        return list((sprint or self.target).requirements.order_by("number").values_list("number", flat=True))

    def test_requirements_live_on_the_sprint_page_with_import_add_and_edit(self):
        self.requirement(self.target, 1, "Log in")
        page = self.client.get(url("sprint", self.target.id))
        self.assertContains(page, "Log in")
        self.assertContains(page, f"?edit_requirement={self.target.requirements.get().id}")
        self.assertContains(page, url("sprint_requirement_add", self.target.id))

    def test_with_none_it_offers_import_of_a_pdf_or_pasted_text(self):
        page = self.client.get(url("sprint", self.target.id))
        self.assertContains(page, url("sprint_requirements_import", self.target.id))
        self.assertContains(page, 'name="pdf"')
        self.assertContains(page, 'name="text"')

    def test_pasted_text_is_split_and_stored_on_the_sprint_numbered_from_one(self):
        with llm_returns(["Users can log in.", "The app sends reminders."]):
            response = self.client.post(
                url("sprint_requirements_import", self.target.id),
                {"text": "Users can log in.\nThe app sends reminders."},
                follow=True,
            )
        self.assertRedirects(response, url("sprint", self.target.id))
        self.assertContains(response, "Imported 2 requirements.")
        self.assertEqual(self.numbers(), [1, 2])
        self.assertTrue(all(r.sprint_id == self.target.id for r in self.target.requirements.all()))
        self.assertEqual(self.numbers(self.earlier), [])

    def test_importing_is_refused_while_the_sprint_has_requirements(self):
        self.requirement(self.target, 1)
        with llm_returns(["x"]) as llm:
            response = self.client.post(
                url("sprint_requirements_import", self.target.id), {"text": "x"}, follow=True
            )
        llm.assert_not_called()
        self.assertContains(response, "already has requirements")

    def test_adding_takes_the_next_number_after_the_sprints_highest(self):
        self.requirement(self.target, 1)
        self.requirement(self.target, 5)
        self.requirement(self.earlier, 9)
        self.client.post(url("sprint_requirement_add", self.target.id), {"text": "Reminders"})
        self.assertEqual(self.numbers(), [1, 5, 6])

    def test_editing_and_deleting_return_to_the_sprint_page(self):
        requirement = self.requirement(self.target, 1)
        response = self.client.post(url("requirement_edit", requirement.id), {"text": "Sign in"})
        self.assertRedirects(response, url("sprint", self.target.id))
        requirement.refresh_from_db()
        self.assertEqual(requirement.text, "Sign in")
        response = self.client.post(url("requirement_delete", requirement.id))
        self.assertRedirects(response, url("sprint", self.target.id))
        self.assertEqual(self.numbers(), [])

    def test_a_requirement_cited_by_this_sprints_claims_cannot_be_deleted(self):
        requirement = self.requirement(self.target, 1)
        self.cite(self.target, 1)
        response = self.client.post(url("requirement_delete", requirement.id), follow=True)
        self.assertContains(response, "A claim cites this requirement")
        self.assertEqual(self.numbers(), [1])

    def test_a_claim_in_another_sprint_citing_the_same_number_does_not_block_deleting(self):
        requirement = self.requirement(self.target, 1)
        self.requirement(self.earlier, 1)
        self.cite(self.earlier, 1)
        self.client.post(url("requirement_delete", requirement.id))
        self.assertEqual(self.numbers(), [])

    def test_requirements_can_be_copied_from_an_earlier_sprint_with_their_numbers(self):
        self.requirement(self.earlier, 1, "Log in")
        self.requirement(self.earlier, 4, "Export")
        page = self.client.get(url("sprint", self.target.id))
        self.assertContains(page, "Copy requirements from an earlier sprint")
        response = self.client.post(
            url("sprint_requirements_copy", self.target.id), {"source": self.earlier.id}, follow=True
        )
        self.assertRedirects(response, url("sprint", self.target.id))
        self.assertEqual(
            list(self.target.requirements.order_by("number").values_list("number", "text")),
            [(1, "Log in"), (4, "Export")],
        )
        self.assertEqual(self.numbers(self.earlier), [1, 4])  # The source is untouched.
        self.assertContains(response, "Copied 2 requirements from Sprint 1")

    def test_copying_is_not_offered_once_the_sprint_has_requirements_and_is_refused(self):
        self.requirement(self.earlier, 1)
        self.requirement(self.target, 7, "Mine")
        page = self.client.get(url("sprint", self.target.id))
        self.assertNotContains(page, "Copy requirements from an earlier sprint")
        response = self.client.post(
            url("sprint_requirements_copy", self.target.id), {"source": self.earlier.id}, follow=True
        )
        self.assertEqual(self.numbers(), [7])
        self.assertContains(response, "already has requirements")

    def test_copying_is_not_offered_when_no_other_sprint_has_requirements(self):
        page = self.client.get(url("sprint", self.target.id))
        self.assertNotContains(page, "Copy requirements from an earlier sprint")

    def test_a_sprint_from_another_project_cannot_be_the_source(self):
        foreign = self.sprint("Sprint 1", project=self.other_project)
        self.requirement(foreign, 1, "Not yours")
        response = self.client.post(
            url("sprint_requirements_copy", self.target.id), {"source": foreign.id}, follow=True
        )
        self.assertEqual(self.numbers(), [])
        self.assertEqual(response.status_code, 200)


class SprintAccessTests(SprintFixture):
    def setUp(self):
        super().setUp()
        self.target = self.sprint()
        self.requirement(self.target, 1)

    def targets(self):
        sprint, requirement = self.target, self.target.requirements.get()
        return [
            ("get", url("sprint_create", self.project.id)),
            ("post", url("sprint_create", self.project.id)),
            ("get", url("sprint", sprint.id)),
            ("post", url("sprint_edit", sprint.id)),
            ("post", url("sprint_delete", sprint.id)),
            ("post", url("sprint_requirements_import", sprint.id)),
            ("post", url("sprint_requirement_add", sprint.id)),
            ("post", url("sprint_requirements_copy", sprint.id)),
            ("post", url("requirement_edit", requirement.id)),
            ("post", url("requirement_delete", requirement.id)),
        ]

    def test_tas_students_and_others_get_a_403_and_nothing_changes(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        student = make_user("sample-student")
        enroll(student, self.course, Role.STUDENT)
        for user in (ta, student, make_user("sample-outsider")):
            client = Client()
            client.force_login(user)
            for method, path in self.targets():
                self.assertEqual(getattr(client, method)(path, {"name": "x"}).status_code, 403, (user, path))
        self.assertEqual(Sprint.objects.count(), 1)
        self.assertEqual(self.target.requirements.count(), 1)

    def test_a_missing_sprint_is_a_404(self):
        for name in ("sprint", "sprint_edit", "sprint_delete", "sprint_requirements_import",
                     "sprint_requirement_add", "sprint_requirements_copy"):
            self.assertIn(self.client.post(url(name, 99999), {}).status_code, (404, 405), name)
        self.assertEqual(self.client.get(url("sprint", 99999)).status_code, 404)
        self.assertEqual(self.client.get(url("sprint_create", 99999)).status_code, 404)

    def test_changes_need_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        for method, path in self.targets():
            if method == "post":
                self.assertEqual(client.post(path, {"name": "x"}).status_code, 403, path)
        self.assertEqual(Sprint.objects.count(), 1)

    def test_changes_only_accept_post(self):
        for name in ("sprint_edit", "sprint_delete", "sprint_requirements_import",
                     "sprint_requirement_add", "sprint_requirements_copy"):
            self.assertEqual(self.client.get(url(name, self.target.id)).status_code, 405, name)
