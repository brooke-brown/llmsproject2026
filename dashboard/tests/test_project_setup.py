"""Tests for US-26 (#90): set up a project and its requirements from my account.

These drive the real pages, so they check what an instructor sees. The PDFs are real files,
and only the LLM is replaced, so nothing makes a network call.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Requirement, Team
from core.tests.test_requirements_pdf import TEXT, scanned_pdf, text_pdf
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, overview_url

Role = Enrollment.Role

PASTED = "Users can log in.\nInstructors can export grades.\nThe app sends reminders."
SPLIT = ["Users can log in.", "Instructors can export grades.", "The app sends reminders."]
PDF_SPLIT = ["Instructors can export grades.", "The app sends a weekly reminder."]


def create_url(course_id):
    return reverse("dashboard:project_create", args=[course_id])


def rename_url(project_id):
    return reverse("dashboard:project_rename", args=[project_id])


def requirements_url(project_id):
    return reverse("dashboard:project_requirements", args=[project_id])


def import_url(project_id):
    return reverse("dashboard:requirements_import", args=[project_id])


def add_url(project_id):
    return reverse("dashboard:requirement_add", args=[project_id])


def edit_url(requirement_id):
    return reverse("dashboard:requirement_edit", args=[requirement_id])


def delete_url(requirement_id):
    return reverse("dashboard:requirement_delete", args=[requirement_id])


def llm_returns(requirements):
    """Replace the LLM behind import_requirements with one that returns this split."""
    return mock.patch(
        "core.services.generate_structured",
        return_value=SimpleNamespace(requirements=requirements),
    )


class SetupFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        # Deliberately not is_staff: the dashboard goes by Enrollment.
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.empty_project = Project.objects.create(course=cls.course, name="Empty Project")
        cls.project = Project.objects.create(course=cls.course, name="Full Project")
        cls.first = Requirement.objects.create(project=cls.project, number=1, text="Log in")
        cls.second = Requirement.objects.create(project=cls.project, number=2, text="Log out")
        cls.fifth = Requirement.objects.create(project=cls.project, number=5, text="Export")

        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.other_project = Project.objects.create(course=cls.other_course, name="Other Project")

    def setUp(self):
        self.client.force_login(self.instructor)

    def numbers(self, project=None):
        project = project or self.project
        return list(project.requirements.order_by("number").values_list("number", flat=True))

    def project_names(self, course=None):
        return sorted((course or self.course).projects.values_list("name", flat=True))

    def cite(self, project, numbers, status=Claim.Status.VERIFIED):
        """Store a claim citing these requirement numbers, on a team in this project."""
        team = Team.objects.create(project=project, name=f"Team {Team.objects.count()}")
        student = make_user(f"sample-student-{get_user_model().objects.count()}")
        summary = Summary.objects.create(
            team=team, user=student, week_start=date(2026, 10, 26), artifact_count=1
        )
        return Claim.objects.create(
            summary=summary,
            text="Added the login form.",
            category="feature",
            artifact_ids=["acme/app:pr:1"],
            requirement_ids=numbers,
            significance="moderate",
            status=status,
        )


class NewProjectTests(SetupFixture):
    def create(self, name):
        return self.client.post(create_url(self.course.id), {"name": name}, follow=True)

    def test_overview_offers_a_new_project_form(self):
        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, "New project")
        self.assertContains(response, f'action="{create_url(self.course.id)}"')

    def test_overview_offers_it_when_the_course_has_no_projects(self):
        Project.objects.filter(course=self.course).delete()

        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, f'action="{create_url(self.course.id)}"')

    def test_new_project_appears_on_the_overview(self):
        response = self.create("Project Three")

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertTrue(Project.objects.filter(course=self.course, name="Project Three").exists())
        self.assertContains(response, "Project Three")

    def test_surrounding_whitespace_is_removed(self):
        self.create("  Project Three  ")

        self.assertTrue(Project.objects.filter(course=self.course, name="Project Three").exists())

    def test_blank_names_are_rejected_with_a_message(self):
        for name in ["", "   "]:
            with self.subTest(name=name):
                before = self.project_names()

                response = self.create(name)

                self.assertContains(response, "Enter a project name.")
                self.assertEqual(self.project_names(), before)

    def test_missing_name_is_rejected(self):
        response = self.client.post(create_url(self.course.id), {}, follow=True)

        self.assertContains(response, "Enter a project name.")

    def test_a_name_used_in_this_course_is_rejected_regardless_of_case(self):
        for name in ["Full Project", "FULL project", "  full project "]:
            with self.subTest(name=name):
                before = self.project_names()

                response = self.create(name)

                self.assertContains(response, "already has a project with this name")
                self.assertEqual(self.project_names(), before)

    def test_case_is_ignored_beyond_ascii_too(self):
        Project.objects.create(course=self.course, name="Étude")

        response = self.create("étude")

        self.assertContains(response, "already has a project with this name")
        self.assertEqual(self.course.projects.filter(name="étude").count(), 0)

    def test_a_name_used_only_in_another_course_is_allowed(self):
        self.create("Other Project")

        self.assertTrue(Project.objects.filter(course=self.course, name="Other Project").exists())

    def test_a_name_longer_than_the_field_is_rejected_with_a_message(self):
        before = self.project_names()

        response = self.create("x" * 201)

        self.assertContains(response, "200 characters or fewer")
        self.assertEqual(self.project_names(), before)


class RenameProjectTests(SetupFixture):
    def rename(self, name, project=None):
        project = project or self.project
        return self.client.post(rename_url(project.id), {"name": name}, follow=True)

    def stored_name(self):
        return Project.objects.get(pk=self.project.pk).name

    def test_overview_offers_a_rename_form_per_project(self):
        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, f'action="{rename_url(self.project.id)}"')
        self.assertContains(response, f'action="{rename_url(self.empty_project.id)}"')

    def test_rename_changes_the_name_and_keeps_the_requirements(self):
        response = self.rename("Renamed Project")

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertEqual(self.stored_name(), "Renamed Project")
        self.assertContains(response, "Renamed Project")
        self.assertEqual(self.numbers(), [1, 2, 5])

    def test_a_project_can_change_the_case_of_its_own_name(self):
        self.rename("FULL PROJECT")

        self.assertEqual(self.stored_name(), "FULL PROJECT")

    def test_blank_names_are_rejected_with_a_message(self):
        for name in ["", "   "]:
            with self.subTest(name=name):
                response = self.rename(name)

                self.assertContains(response, "Enter a project name.")
                self.assertEqual(self.stored_name(), "Full Project")

    def test_a_name_used_by_another_project_is_rejected_regardless_of_case(self):
        for name in ["Empty Project", "EMPTY project"]:
            with self.subTest(name=name):
                response = self.rename(name)

                self.assertContains(response, "already has a project with this name")
                self.assertEqual(self.stored_name(), "Full Project")

    def test_a_name_used_only_in_another_course_is_allowed(self):
        self.rename("Other Project")

        self.assertEqual(self.stored_name(), "Other Project")

    def test_a_name_longer_than_the_field_is_rejected_with_a_message(self):
        response = self.rename("x" * 201)

        self.assertContains(response, "200 characters or fewer")
        self.assertEqual(self.stored_name(), "Full Project")


class RequirementsPageTests(SetupFixture):
    def test_overview_links_to_each_projects_requirements_page(self):
        response = self.client.get(overview_url(self.course.id))

        self.assertContains(response, f'href="{requirements_url(self.project.id)}"')
        self.assertContains(response, f'href="{requirements_url(self.empty_project.id)}"')

    def test_project_with_no_requirements_offers_a_pdf_upload_and_pasted_text(self):
        response = self.client.get(requirements_url(self.empty_project.id))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{import_url(self.empty_project.id)}"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'type="file"')
        self.assertContains(response, "<textarea")

    def test_shows_the_numbered_requirements_of_this_project_only(self):
        Requirement.objects.create(project=self.empty_project, number=9, text="Not this project")

        response = self.client.get(requirements_url(self.project.id))

        self.assertContains(response, "Log in")
        self.assertContains(response, "Export")
        self.assertEqual(
            [requirement.number for requirement in response.context["requirements"]], [1, 2, 5]
        )
        self.assertNotContains(response, "Not this project")

    def test_project_that_has_requirements_no_longer_offers_an_import(self):
        response = self.client.get(requirements_url(self.project.id))

        self.assertNotContains(response, f'action="{import_url(self.project.id)}"')
        self.assertNotContains(response, 'type="file"')

    def test_page_offers_edit_delete_and_add_forms(self):
        response = self.client.get(requirements_url(self.project.id))

        self.assertContains(response, f'action="{edit_url(self.first.id)}"')
        self.assertContains(response, f'action="{delete_url(self.first.id)}"')
        self.assertContains(response, f'action="{add_url(self.project.id)}"')

    def test_a_project_with_no_requirements_can_also_add_one_by_hand(self):
        response = self.client.get(requirements_url(self.empty_project.id))

        self.assertContains(response, f'action="{add_url(self.empty_project.id)}"')


class ImportRequirementsTests(SetupFixture):
    def post(self, project=None, **data):
        project = project or self.empty_project
        # A browser always submits the empty textarea along with the file.
        data.setdefault("text", "")
        return self.client.post(import_url(project.id), data, follow=True)

    def stored(self):
        return list(
            self.empty_project.requirements.order_by("number").values_list("number", "text")
        )

    def test_pasted_text_shows_numbered_requirements_and_how_many_were_imported(self):
        with llm_returns(SPLIT):
            response = self.post(text=PASTED)

        self.assertRedirects(response, requirements_url(self.empty_project.id))
        self.assertEqual(self.stored(), list(enumerate(SPLIT, start=1)))
        self.assertContains(response, "Imported 3 requirements.")
        for text in SPLIT:
            self.assertContains(response, text)

    def test_message_is_singular_for_one_requirement(self):
        with llm_returns(SPLIT[:1]):
            response = self.post(text=PASTED)

        self.assertContains(response, "Imported 1 requirement.")

    def test_uploaded_pdf_is_imported(self):
        pdf = SimpleUploadedFile("requirements.pdf", text_pdf(), "application/pdf")

        with llm_returns(PDF_SPLIT) as llm:
            response = self.post(pdf=pdf)

        self.assertContains(response, "Imported 2 requirements.")
        self.assertEqual(self.stored(), list(enumerate(PDF_SPLIT, start=1)))
        self.assertIn(TEXT, llm.call_args.args[0])

    def test_pdf_and_text_together_are_rejected_with_a_message(self):
        pdf = SimpleUploadedFile("requirements.pdf", text_pdf(), "application/pdf")

        with llm_returns(SPLIT) as llm:
            response = self.post(pdf=pdf, text=PASTED)

        self.assertContains(response, "Upload a PDF or paste text, not both.")
        self.assertEqual(self.stored(), [])
        llm.assert_not_called()

    def test_submitting_neither_is_rejected_with_a_message(self):
        for text in ["", "   \n "]:
            with self.subTest(text=text), llm_returns(SPLIT) as llm:
                response = self.post(text=text)

                self.assertContains(response, "Upload a PDF or paste the project")
                self.assertEqual(self.stored(), [])
                llm.assert_not_called()

    def test_a_scanned_pdf_shows_the_reason_and_lets_me_try_again(self):
        pdf = SimpleUploadedFile("scan.pdf", scanned_pdf(), "application/pdf")

        with llm_returns(SPLIT) as llm:
            response = self.post(pdf=pdf)

        self.assertContains(response, "may be a scanned copy")
        self.assertEqual(self.stored(), [])
        llm.assert_not_called()
        self.assertContains(response, f'action="{import_url(self.empty_project.id)}"')

    def test_an_llm_error_shows_the_reason_saves_nothing_and_keeps_the_text(self):
        with mock.patch(
            "core.services.generate_structured", side_effect=LLMError("The LLM timed out.")
        ):
            response = self.post(text=PASTED)

        self.assertContains(response, "The LLM timed out.")
        self.assertEqual(self.stored(), [])
        self.assertContains(response, f'action="{import_url(self.empty_project.id)}"')
        self.assertContains(response, "Instructors can export grades.")

    def test_a_requirement_not_in_the_source_shows_the_reason_and_saves_nothing(self):
        with llm_returns([*SPLIT, "The app supports single sign-on."]):
            response = self.post(text=PASTED)

        self.assertContains(response, "isn&#x27;t in your source text")
        self.assertEqual(self.stored(), [])

    def test_trying_again_after_a_failure_works(self):
        with mock.patch(
            "core.services.generate_structured", side_effect=LLMError("The LLM timed out.")
        ):
            self.post(text=PASTED)

        with llm_returns(SPLIT):
            response = self.post(text=PASTED)

        self.assertContains(response, "Imported 3 requirements.")
        self.assertEqual(len(self.stored()), 3)

    def test_a_submitted_import_is_refused_when_the_project_has_requirements(self):
        with llm_returns(SPLIT) as llm:
            response = self.post(project=self.project, text=PASTED)

        self.assertContains(response, "already has requirements")
        self.assertEqual(self.numbers(), [1, 2, 5])
        self.assertEqual(Requirement.objects.get(pk=self.first.pk).text, "Log in")
        llm.assert_not_called()

    def test_an_import_that_loses_a_race_shows_the_refusal(self):
        """Requirements arrive while the LLM runs, so the page no longer has an import form."""

        def another_import_finishes(*args, **kwargs):
            Requirement.objects.create(project=self.empty_project, number=1, text="Won the race")
            return SimpleNamespace(requirements=SPLIT)

        with mock.patch(
            "core.services.generate_structured", side_effect=another_import_finishes
        ):
            response = self.post(text=PASTED)

        self.assertContains(response, "already has requirements")
        self.assertEqual(self.stored(), [(1, "Won the race")])


class EditRequirementTests(SetupFixture):
    def edit(self, requirement=None, **data):
        return self.client.post(edit_url((requirement or self.second).id), data, follow=True)

    def test_edit_changes_the_text(self):
        response = self.edit(text="Log out of every device")

        self.assertRedirects(response, requirements_url(self.project.id))
        self.assertEqual(Requirement.objects.get(pk=self.second.pk).text, "Log out of every device")
        self.assertContains(response, "Log out of every device")

    def test_edit_form_has_no_number_field(self):
        response = self.client.get(requirements_url(self.project.id))

        self.assertNotContains(response, 'name="number"')

    def test_a_submitted_number_is_ignored(self):
        self.edit(text="Log out of every device", number="7", project=self.empty_project.id)

        stored = Requirement.objects.get(pk=self.second.pk)
        self.assertEqual(stored.number, 2)
        self.assertEqual(stored.project, self.project)

    def test_a_requirement_a_claim_cites_can_still_be_edited(self):
        self.cite(self.project, [2])

        self.edit(text="Log out of every device")

        self.assertEqual(Requirement.objects.get(pk=self.second.pk).text, "Log out of every device")

    def test_blank_text_is_rejected_with_a_message(self):
        for text in ["", "  \n "]:
            with self.subTest(text=text):
                response = self.edit(text=text)

                self.assertContains(response, "Enter the requirement")
                self.assertEqual(Requirement.objects.get(pk=self.second.pk).text, "Log out")


class AddRequirementTests(SetupFixture):
    def add(self, project=None, **data):
        return self.client.post(add_url((project or self.project).id), data, follow=True)

    def test_add_takes_the_next_number_after_the_projects_highest(self):
        response = self.add(text="Reset a password")

        self.assertRedirects(response, requirements_url(self.project.id))
        self.assertEqual(self.numbers(), [1, 2, 5, 6])
        self.assertEqual(self.project.requirements.get(number=6).text, "Reset a password")
        self.assertContains(response, "Reset a password")

    def test_first_requirement_in_a_project_is_number_one(self):
        self.add(project=self.empty_project, text="Reset a password")

        self.assertEqual(self.numbers(self.empty_project), [1])

    def test_numbers_are_counted_within_the_project(self):
        Requirement.objects.create(project=self.other_project, number=40, text="Elsewhere")

        self.add(project=self.empty_project, text="Reset a password")

        self.assertEqual(self.numbers(self.empty_project), [1])

    def test_a_submitted_number_is_ignored(self):
        self.add(text="Reset a password", number="3")

        self.assertEqual(self.numbers(), [1, 2, 5, 6])

    def test_blank_text_is_rejected_with_a_message(self):
        for text in ["", "  \n "]:
            with self.subTest(text=text):
                response = self.add(text=text)

                self.assertContains(response, "Enter the requirement")
                self.assertEqual(self.numbers(), [1, 2, 5])

    def test_adding_after_a_number_was_taken_at_the_same_moment_shows_a_message(self):
        """Two adds can pick the same number, and the unique constraint stops the second."""
        with mock.patch("core.services._next_requirement_number", return_value=5):
            response = self.add(text="Reset a password")

        self.assertContains(response, "try again")
        self.assertEqual(self.numbers(), [1, 2, 5])
        self.assertEqual(self.project.requirements.get(number=5).text, "Export")


class DeleteRequirementTests(SetupFixture):
    def delete(self, requirement=None):
        return self.client.post(delete_url((requirement or self.second).id), follow=True)

    def test_a_requirement_no_claim_cites_is_deleted(self):
        response = self.delete()

        self.assertRedirects(response, requirements_url(self.project.id))
        self.assertEqual(self.numbers(), [1, 5])
        # Matched with its tag, since the top bar has a "Log out" button.
        self.assertNotContains(response, "Log out</textarea>")
        self.assertContains(response, "Log in</textarea>")

    def test_deleting_one_doesnt_renumber_the_others(self):
        self.delete(self.first)

        stored = dict(self.project.requirements.values_list("number", "text"))
        self.assertEqual(stored, {2: "Log out", 5: "Export"})

    def test_a_deleted_number_in_the_middle_is_never_reassigned(self):
        self.delete()

        self.client.post(add_url(self.project.id), {"text": "Reset a password"})

        self.assertEqual(self.numbers(), [1, 5, 6])

    def test_a_deleted_top_number_returns_because_no_claim_cites_it(self):
        """A known limit: without a model change, nothing stores that the top number was used.

        Only a requirement no claim cites can be deleted here, so no claim can pick up the
        new requirement.
        """
        self.delete(self.fifth)

        self.client.post(add_url(self.project.id), {"text": "Reset a password"})

        self.assertEqual(self.numbers(), [1, 2, 3])

    def test_deleting_a_verified_claims_requirement_is_refused_with_a_message(self):
        self.cite(self.project, [1, 2])

        response = self.delete()

        self.assertContains(response, "A claim cites this requirement")
        self.assertEqual(self.numbers(), [1, 2, 5])

    def test_a_dropped_claim_also_counts_as_citing(self):
        self.cite(self.project, [2], status=Claim.Status.COULDNT_VERIFY)

        response = self.delete()

        self.assertContains(response, "A claim cites this requirement")
        self.assertEqual(self.numbers(), [1, 2, 5])

    def test_a_claim_on_another_number_doesnt_block_the_delete(self):
        self.cite(self.project, [1, 5])

        self.delete()

        self.assertEqual(self.numbers(), [1, 5])

    def test_a_claim_in_another_project_with_the_same_number_doesnt_block_the_delete(self):
        self.cite(self.empty_project, [2])

        self.delete()

        self.assertEqual(self.numbers(), [1, 5])

    def test_deleting_every_requirement_offers_the_import_again(self):
        for requirement in (self.first, self.second, self.fifth):
            response = self.delete(requirement)

        self.assertContains(response, f'action="{import_url(self.project.id)}"')


class AccessTests(SetupFixture):
    def get_requests(self):
        """Every setup request, as (description, method, URL, data)."""
        return [
            ("page", "get", requirements_url(self.project.id), {}),
            ("create", "post", create_url(self.course.id), {"name": "New"}),
            ("rename", "post", rename_url(self.project.id), {"name": "New"}),
            ("import", "post", import_url(self.empty_project.id), {"text": PASTED}),
            ("add", "post", add_url(self.project.id), {"text": "New"}),
            ("edit", "post", edit_url(self.second.id), {"text": "New"}),
            ("delete", "post", delete_url(self.second.id), {}),
        ]

    def assert_nothing_changed(self):
        self.assertEqual(self.project_names(), ["Empty Project", "Full Project"])
        self.assertEqual(self.numbers(), [1, 2, 5])
        self.assertEqual(self.numbers(self.empty_project), [])
        self.assertEqual(Requirement.objects.get(pk=self.second.pk).text, "Log out")

    def assert_refused(self, user):
        self.client.force_login(user)
        with llm_returns(SPLIT) as llm:
            for description, method, url, data in self.get_requests():
                with self.subTest(request=description):
                    response = getattr(self.client, method)(url, data)
                    self.assertEqual(response.status_code, 403)
        llm.assert_not_called()
        self.assert_nothing_changed()

    def test_instructor_without_is_staff_can_do_all_of_it(self):
        self.assertFalse(self.instructor.is_staff)
        with llm_returns(SPLIT):
            for description, method, url, data in self.get_requests():
                with self.subTest(request=description):
                    response = getattr(self.client, method)(url, data)
                    self.assertIn(response.status_code, (200, 302))
                    self.assertNotEqual(response.get("Location", ""), "/admin/")

        self.assertIn("New", self.project_names())
        self.assertEqual(self.numbers(self.empty_project), [1, 2, 3])
        self.assertEqual(self.numbers(), [1, 5, 6])

    def test_ta_is_refused(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        self.assert_refused(ta)

    def test_student_is_refused(self):
        student = make_user("sample-student")
        enroll(student, self.course, Role.STUDENT)

        self.assert_refused(student)

    def test_another_courses_instructor_is_refused(self):
        other = make_user("sample-other")
        enroll(other, self.other_course, Role.INSTRUCTOR)

        self.assert_refused(other)

    def test_being_an_instructor_elsewhere_doesnt_help_a_ta(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        enroll(ta, self.other_course, Role.INSTRUCTOR)

        self.assert_refused(ta)

    def test_unenrolled_user_is_refused(self):
        self.assert_refused(make_user("sample-unenrolled"))

    def test_unenrolled_superuser_is_refused(self):
        self.assert_refused(
            get_user_model().objects.create_superuser(
                username="sample-admin", password="not-a-real-password", github_login="sample-admin"
            )
        )

    def test_anonymous_user_is_sent_to_log_in(self):
        self.client.logout()
        for description, method, url, data in self.get_requests():
            with self.subTest(request=description):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])
        self.assert_nothing_changed()

    def test_ta_doesnt_see_these_actions_on_the_overview(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        self.client.force_login(ta)

        response = self.client.get(overview_url(self.course.id))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, create_url(self.course.id))
        self.assertNotContains(response, rename_url(self.project.id))
        self.assertNotContains(response, requirements_url(self.project.id))
        self.assertNotContains(response, "New project")
        # The read-only table is still theirs to see.
        self.assertContains(response, "Log in")

    def test_missing_course_project_or_requirement_is_a_404(self):
        missing = 999999
        requests = [
            ("page", "get", requirements_url(missing), {}),
            ("create", "post", create_url(missing), {"name": "New"}),
            ("rename", "post", rename_url(missing), {"name": "New"}),
            ("import", "post", import_url(missing), {"text": PASTED}),
            ("add", "post", add_url(missing), {"text": "New"}),
            ("edit", "post", edit_url(missing), {"text": "New"}),
            ("delete", "post", delete_url(missing), {}),
        ]
        for description, method, url, data in requests:
            with self.subTest(request=description):
                self.assertEqual(getattr(self.client, method)(url, data).status_code, 404)

    def test_changes_refuse_get(self):
        for description, method, url, _data in self.get_requests():
            if method == "get":
                continue
            with self.subTest(request=description):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assert_nothing_changed()

    def test_changes_need_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        for description, method, url, data in self.get_requests():
            if method == "get":
                continue
            with self.subTest(request=description):
                self.assertEqual(client.post(url, data).status_code, 403)
        self.assert_nothing_changed()

    def test_every_setup_form_posts_with_a_csrf_token(self):
        pages = [
            overview_url(self.course.id),
            requirements_url(self.project.id),
            requirements_url(self.empty_project.id),
        ]
        for page in pages:
            with self.subTest(page=page):
                content = self.client.get(page).content.decode()
                forms = content.split("<form")[1:]
                self.assertGreater(len(forms), 1)
                for form in forms:
                    self.assertIn('method="post"', form.split(">")[0])
                    self.assertIn("csrfmiddlewaretoken", form)
