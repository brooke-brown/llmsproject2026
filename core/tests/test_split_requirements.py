"""Tests for US-9 (#25): pasting a sprint's requirements and getting them split by the LLM.

Requirements belonged to a project until T-21 (#125), so these now import into a sprint.

The admin tests drive the real pages, so they check what an instructor sees. The source
check's edge cases call the service directly, which US-10's PDF upload will also use.

The service is imported inside each test rather than at the top of the file, so each
criterion passes or fails on its own instead of the whole module failing to import.

Nothing here makes a network call: anthropic.Anthropic is replaced in every test. That is
the only LLM seam these tests rely on, so they hold whether or not the client ends up
shared with summaries/llm.py.
"""

import json
import os
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from django.db import IntegrityError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape
from pydantic import BaseModel

from datetime import date, timedelta

from core.models import Course, Project, Requirement, Sprint, User

SOURCE = (
    "Project requirements\n"
    "1. Users can log in\n"
    "   with their email address.\n"
    "2. Instructors can\texport grades.\n"
    "3. The app sends a weekly reminder.\n"
)
SPLIT = [
    "Users can log in with their email address.",
    "Instructors can export grades.",
    "The app sends a weekly reminder.",
]
INVENTED = "The app supports single sign-on."


def import_requirements(*args, **kwargs):
    from core.services import import_sprint_requirements

    return import_sprint_requirements(*args, **kwargs)


def import_error():
    from core.services import RequirementImportError

    return RequirementImportError


def whitespace_normalized(text):
    return " ".join(text.split())


def api_request():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def api_status_error(error_class, status):
    response = httpx2.Response(status, request=api_request())
    return error_class("failed", response=response, body=None)


class FakeLLM:
    """Stands in for an anthropic.Anthropic client.

    The reply is checked against the `output_format` the code under test asked for, and a
    reply that doesn't fit raises pydantic's ValidationError, as the real SDK does.
    """

    def __init__(self):
        self.reply = json.dumps({"requirements": SPLIT})
        self.stop_reason = "end_turn"
        self.error = None
        self.during_call = None  # Runs mid-call, to simulate something changing meanwhile.
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)

    def replies_with(self, requirements):
        self.reply = json.dumps({"requirements": requirements})

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.during_call is not None:
            self.during_call()
        if self.error is not None:
            raise self.error
        parsed = kwargs["output_format"].model_validate_json(self.reply)
        return SimpleNamespace(parsed_output=parsed, stop_reason=self.stop_reason)


class SplitRequirementsTestCase(TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"LLM_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)

        self.llm = FakeLLM()
        patcher = mock.patch("anthropic.Anthropic", return_value=self.llm)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.admin = User.objects.create_superuser(
            username="admin",
            password="not-a-real-password",
            email="admin@example.com",
            github_login="course-admin",
        )
        self.client.force_login(self.admin)
        self.course = Course.objects.create(name="CS 3240, Fall 2026")
        self.project = Project.objects.create(course=self.course, name="Project 1")
        self.sprint = self.new_sprint("Sprint 1")

    def new_sprint(self, name):
        """A sprint of the project, each a week after the last so none overlap."""
        weeks = self.project.sprints.count()
        start = date(2026, 9, 1) + timedelta(days=7 * weeks)
        return Sprint.objects.create(
            project=self.project, name=name, start_date=start, end_date=start + timedelta(days=6)
        )

    def import_url(self, sprint=None):
        sprint = sprint or self.sprint
        try:
            return reverse("admin:core_sprint_import_requirements", args=[sprint.pk])
        except NoReverseMatch:
            self.fail("Sprint has no admin page for importing requirements")

    def requirement_url(self, action, *args):
        try:
            return reverse(f"admin:core_requirement_{action}", args=args)
        except NoReverseMatch:
            self.fail(f"Requirement has no admin {action} page")

    def paste(self, text=SOURCE, sprint=None):
        return self.client.post(self.import_url(sprint), {"text": text})

    def stored(self, sprint=None):
        requirements = Requirement.objects.filter(sprint=sprint or self.sprint)
        return list(requirements.order_by("number").values_list("number", "text"))

    def assert_import_error(self, response, expected_text=""):
        """The page came back showing an error, rather than redirecting as if it worked."""
        self.assertEqual(response.status_code, 200, "Expected the page to show an error")
        errors = response.context["form"].non_field_errors()
        self.assertTrue(errors, "Expected the page to show an error")
        for error in errors:
            self.assertContains(response, escape(error))
        self.assertIn(expected_text, " ".join(errors).lower())


class ImportPageTests(SplitRequirementsTestCase):
    def test_the_sprint_page_links_to_the_import_page(self):
        response = self.client.get(reverse("admin:core_sprint_change", args=[self.sprint.pk]))

        self.assertContains(response, self.import_url())

    def test_the_page_for_adding_a_sprint_still_loads(self):
        # It shares the template that holds the link, but has no sprint to link to yet.
        response = self.client.get(reverse("admin:core_sprint_add"))

        self.assertEqual(response.status_code, 200)

    def test_the_page_has_a_form_to_paste_into_and_splits_nothing_yet(self):
        response = self.client.get(self.import_url())

        self.assertContains(response, 'name="text"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.stored(), [])

    def test_someone_who_is_not_logged_in_is_sent_to_log_in(self):
        self.client.logout()

        response = self.paste()

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.stored(), [])

    def test_staff_without_permission_to_add_requirements_are_refused(self):
        staff = User.objects.create_user(
            username="ta", password="not-a-real-password", github_login="a-ta", is_staff=True
        )
        self.client.force_login(staff)

        response = self.paste()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.stored(), [])

    def test_an_unknown_sprint_is_not_found(self):
        url = self.import_url().replace(f"/{self.sprint.pk}/", "/999999/")

        response = self.client.post(url, {"text": SOURCE})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.llm.calls, [])

    def test_blank_text_is_rejected_without_calling_the_llm(self):
        for text in ("", "   \n\t "):
            with self.subTest(text=text):
                response = self.paste(text)

                self.assertEqual(response.status_code, 200)
                self.assertIn("text", response.context["form"].errors)
                self.assertEqual(self.llm.calls, [])
                self.assertEqual(self.stored(), [])


class SplitTests(SplitRequirementsTestCase):
    """Pasted text produces requirements numbered 1 to N, which I can edit in admin."""

    def test_pasted_text_becomes_requirements_numbered_from_1_in_order(self):
        response = self.paste()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.stored(),
            [
                (1, "Users can log in with their email address."),
                (2, "Instructors can export grades."),
                (3, "The app sends a weekly reminder."),
            ],
        )

    def test_numbers_come_from_the_order_not_from_the_source(self):
        self.llm.replies_with(["Users can log in.", "Users can log out."])

        self.paste("7. Users can log in.\n12. Users can log out.\n")

        self.assertEqual([number for number, _ in self.stored()], [1, 2])

    def test_the_instructor_is_told_how_many_were_created(self):
        response = self.client.post(self.import_url(), {"text": SOURCE}, follow=True)

        self.assertContains(response, "3 requirements")

    def test_the_pasted_text_is_sent_to_the_llm(self):
        self.paste()

        self.assertEqual(len(self.llm.calls), 1)
        self.assertIn(SOURCE, self.llm.calls[0]["messages"][0]["content"])

    def test_the_reply_is_checked_against_a_pydantic_schema(self):
        self.paste()

        self.assertTrue(issubclass(self.llm.calls[0]["output_format"], BaseModel))

    def test_requirements_are_added_only_to_this_sprint(self):
        other = self.new_sprint("Sprint 2")

        self.paste()

        self.assertEqual(self.stored(other), [])

    def test_the_service_returns_the_new_requirements_in_order(self):
        requirements = import_requirements(self.sprint, SOURCE)

        self.assertEqual([requirement.number for requirement in requirements], [1, 2, 3])
        self.assertTrue(all(requirement.pk for requirement in requirements))

    def test_requirements_are_listed_in_admin(self):
        self.paste()

        response = self.client.get(self.requirement_url("changelist"))

        self.assertContains(response, "Instructors can export grades.")

    def test_a_requirement_can_be_edited_in_admin(self):
        self.paste()
        requirement = Requirement.objects.get(sprint=self.sprint, number=2)

        response = self.client.post(
            self.requirement_url("change", requirement.pk),
            {"sprint": self.sprint.pk, "number": 2, "text": "Instructors can export a CSV."},
        )

        self.assertEqual(response.status_code, 302)
        requirement.refresh_from_db()
        self.assertEqual(requirement.text, "Instructors can export a CSV.")


class SourceCheckTests(SplitRequirementsTestCase):
    """Every requirement appears in my source text, ignoring whitespace differences."""

    def assert_accepted(self, source, requirement):
        sprint = self.new_sprint("Accepted")
        self.llm.replies_with([requirement])

        import_requirements(sprint, source)

        self.assertEqual(
            [whitespace_normalized(text) for _, text in self.stored(sprint)],
            [whitespace_normalized(requirement)],
        )

    def assert_refused(self, source, requirements):
        sprint = self.new_sprint("Refused")
        self.llm.replies_with(requirements)

        with self.assertRaises(import_error()) as raised:
            import_requirements(sprint, source)

        self.assertIn("source text", str(raised.exception).lower())
        self.assertEqual(self.stored(sprint), [])

    def test_whitespace_differences_are_ignored(self):
        cases = {
            "source wraps a line": ("Users can log\n   in.", "Users can log in."),
            "reply wraps a line": ("Users can log in.", "Users can log\nin."),
            "tab": ("Users can\tlog in.", "Users can log in."),
            "windows line ending": ("Users can\r\nlog in.", "Users can log in."),
            "non-breaking space": ("Users can log in.", "Users can log in."),
            "repeated spaces": ("Users  can   log in.", "Users can log in."),
            "padded reply": ("Users can log in.", "  Users can log in.\n"),
        }
        for name, (source, requirement) in cases.items():
            with self.subTest(name):
                self.assert_accepted(source, requirement)

    def test_an_invented_requirement_is_refused(self):
        self.assert_refused(SOURCE, [INVENTED])

    def test_one_invented_requirement_refuses_the_whole_import(self):
        # Numbers are what claims point at, so a gap or shift from dropping one would stick.
        self.assert_refused(SOURCE, [*SPLIT, INVENTED])
        self.assert_refused(SOURCE, [INVENTED, *SPLIT])

    def test_a_reworded_requirement_is_refused(self):
        self.assert_refused(SOURCE, ["Users can sign in with their email address."])

    def test_only_whitespace_is_ignored_not_letter_case(self):
        self.assert_refused(SOURCE, ["users can log in with their email address."])

    def test_only_whitespace_is_ignored_not_punctuation(self):
        self.assert_refused(SOURCE, ["Instructors can export grades!"])

    def test_adding_or_removing_a_word_break_is_a_different_requirement(self):
        self.assert_refused("Users can log in.", ["Users can login."])

    def test_a_blank_requirement_is_refused(self):
        # A blank string is technically found in any source text.
        for blank in ("", "  \n\t"):
            with self.subTest(blank=blank):
                self.assert_refused(SOURCE, [*SPLIT, blank])

    def test_the_instructor_sees_an_error_and_not_the_invented_requirement(self):
        self.llm.replies_with([*SPLIT, INVENTED])

        response = self.paste()

        self.assert_import_error(response, "source text")
        self.assertNotContains(response, INVENTED)
        self.assertEqual(self.stored(), [])


class FailedSplitTests(SplitRequirementsTestCase):
    """If the split fails, I see an error and my existing requirements are unchanged."""

    def assert_split_fails(self):
        response = self.paste()

        self.assert_import_error(response)
        self.assertEqual(self.stored(), [])

    def test_an_llm_request_failure_shows_an_error(self):
        errors = {
            "timeout": anthropic.APITimeoutError(request=api_request()),
            "connection": anthropic.APIConnectionError(request=api_request()),
            "rate limit": api_status_error(anthropic.RateLimitError, 429),
            "bad key": api_status_error(anthropic.AuthenticationError, 401),
            "server error": api_status_error(anthropic.InternalServerError, 500),
        }
        for name, error in errors.items():
            with self.subTest(name):
                self.llm.error = error
                self.assert_split_fails()

    def test_a_reply_that_breaks_the_schema_shows_an_error(self):
        replies = {
            "not json": "Here are your requirements!",
            "wrong key": json.dumps({"items": SPLIT}),
            "not a list": json.dumps({"requirements": "Users can log in."}),
            "not strings": json.dumps({"requirements": [{"unexpected": 1}]}),
        }
        for name, reply in replies.items():
            with self.subTest(name):
                self.llm.reply = reply
                self.assert_split_fails()

    def test_a_reply_with_no_requirements_shows_an_error(self):
        self.llm.replies_with([])

        self.assert_split_fails()

    def test_a_refusal_shows_an_error(self):
        self.llm.stop_reason = "refusal"

        self.assert_split_fails()

    def test_a_reply_cut_off_at_the_token_limit_shows_an_error(self):
        # What arrived may be valid, but later requirements would be silently missing.
        self.llm.stop_reason = "max_tokens"

        self.assert_split_fails()

    def test_a_missing_api_key_shows_an_error(self):
        del os.environ["LLM_API_KEY"]

        self.assert_split_fails()

    def test_the_service_raises_its_own_error_when_the_split_fails(self):
        self.llm.error = anthropic.APITimeoutError(request=api_request())

        with self.assertRaises(import_error()):
            import_requirements(self.sprint, SOURCE)

    def test_the_pasted_text_is_kept_so_it_can_be_retried(self):
        self.llm.error = anthropic.APITimeoutError(request=api_request())

        response = self.paste("Users can log in with their email address.")

        self.assertContains(response, "Users can log in with their email address.")

    def test_a_failure_leaves_requirements_that_appeared_during_the_call_alone(self):
        def someone_else_adds_a_requirement():
            Requirement.objects.create(sprint=self.sprint, number=1, text="Added meanwhile")

        self.llm.during_call = someone_else_adds_a_requirement
        self.llm.error = anthropic.APITimeoutError(request=api_request())

        self.assert_import_error(self.paste())
        self.assertEqual(self.stored(), [(1, "Added meanwhile")])

    def test_a_failure_leaves_other_sprints_alone(self):
        other = self.new_sprint("Sprint 2")
        Requirement.objects.create(sprint=other, number=1, text="Users can log out")
        self.llm.error = anthropic.APITimeoutError(request=api_request())

        self.paste()

        self.assertEqual(self.stored(other), [(1, "Users can log out")])


class ExistingRequirementsTests(SplitRequirementsTestCase):
    """If the sprint already has requirements, the import is refused."""

    def setUp(self):
        super().setUp()
        self.existing = Requirement.objects.create(
            sprint=self.sprint, number=1, text="Users can log out"
        )

    def test_the_import_is_refused_with_an_error(self):
        self.assert_import_error(self.paste(), "already has requirements")

    def test_the_existing_requirements_are_unchanged(self):
        self.paste()

        self.assertEqual(self.stored(), [(1, "Users can log out")])
        self.assertTrue(Requirement.objects.filter(pk=self.existing.pk).exists())

    def test_the_llm_is_not_called(self):
        self.paste()

        self.assertEqual(self.llm.calls, [])

    def test_the_service_refuses_too(self):
        # US-10's upload goes through the service, so the refusal can't live only in the page.
        with self.assertRaises(import_error()) as raised:
            import_requirements(self.sprint, SOURCE)

        self.assertIn("already has requirements", str(raised.exception).lower())
        self.assertEqual(self.stored(), [(1, "Users can log out")])

    def test_requirements_in_another_sprint_do_not_block_the_import(self):
        other = self.new_sprint("Sprint 2")

        response = self.paste(sprint=other)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.stored(other)), 3)

    def test_an_import_that_collides_at_the_last_moment_is_refused(self):
        # Two overlapping imports can both pass the check, and then number 1 collides.
        other = self.new_sprint("Sprint 2")

        with mock.patch(
            "django.db.models.query.QuerySet.bulk_create", side_effect=IntegrityError
        ):
            response = self.paste(sprint=other)

        self.assert_import_error(response, "already has requirements")
        self.assertEqual(self.stored(other), [])

    def test_requirements_that_appear_during_the_call_also_refuse_the_import(self):
        # Two imports can overlap, and the LLM call is long enough for that to happen.
        sprint = self.new_sprint("Sprint 2")

        def someone_else_adds_a_requirement():
            Requirement.objects.create(sprint=sprint, number=1, text="Added meanwhile")

        self.llm.during_call = someone_else_adds_a_requirement

        response = self.paste(sprint=sprint)

        self.assert_import_error(response, "already has requirements")
        self.assertEqual(self.stored(sprint), [(1, "Added meanwhile")])
