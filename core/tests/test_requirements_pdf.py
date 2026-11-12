"""Tests for US-10 (#26): uploading a requirements PDF instead of pasting its text.

The PDFs are real files built here, and nothing replaces pypdf, so these tests also catch
files that pypdf reads differently than expected. Only the LLM is faked, as in US-9's tests.

A browser always submits the empty textarea along with the file, so uploads post `text` too.
"""

import zlib

import anthropic
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils.html import escape

from core.models import Enrollment, Requirement, Team
from core.tests.test_split_requirements import (
    INVENTED,
    SPLIT,
    SplitRequirementsTestCase,
    api_request,
    whitespace_normalized,
)

LINES = [
    "Project requirements",
    "1. Users can log in",
    "with their email address.",
    "2. Instructors can export grades.",
    "3. The app sends a weekly reminder.",
]
TEXT = "\n".join(LINES)
NO_REQUIREMENTS_MESSAGE = "No requirements for Sprint 1 yet."


def build_pdf(page_streams):
    """Return the bytes of a PDF with one page per content stream (None for no content)."""
    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    resources = f"<< /Font << /F1 {font} >> >>"
    page_count = len(page_streams)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
    ]
    for index, stream in enumerate(page_streams):
        contents = "" if stream is None else f"/Contents {4 + index * 2} 0 R "
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources {resources} {contents}>>".encode()
        )
        objects.append(b"<< /Length 0 >>\nstream\n\nendstream" if stream is None else stream)
    return _assemble(objects)


def _assemble(objects, trailer_extra=""):
    pdf = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {trailer_extra}>>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(pdf)


def text_stream(lines):
    commands = "BT /F1 12 Tf 72 720 Td 14 TL "
    commands += " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    data = commands.encode()
    return f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def text_pdf(lines=LINES):
    return build_pdf([text_stream(lines)])


def scanned_pdf():
    """A page that shows an image and has no text, like a scanner's output."""
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
    )
    drawing = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
    return _assemble(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
            f"<< /Length {len(drawing)} >>\nstream\n".encode() + drawing + b"\nendstream",
            image,
        ]
    )


def password_protected_pdf():
    """A PDF that can't be opened without a password nobody gave us."""
    encrypt = (
        "/Encrypt << /Filter /Standard /V 1 /R 2 /P -4 "
        f"/O <{'11' * 32}> /U <{'22' * 32}> >> /ID [<{'33' * 16}> <{'33' * 16}>] "
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    return _assemble(objects, trailer_extra=encrypt)


def damaged_second_page_pdf():
    """Page 1 reads fine, but page 2's compressed text can't be decompressed."""
    damaged = zlib.compress(b"BT (Instructors can export grades.) Tj ET")[:8] + b"not zlib data"
    stream = (
        f"<< /Length {len(damaged)} /Filter /FlateDecode >>\nstream\n".encode()
        + damaged
        + b"\nendstream"
    )
    return build_pdf([text_stream(LINES[:3]), stream])


class UploadTestCase(SplitRequirementsTestCase):
    def upload(self, content=None, name="requirements.pdf", sprint=None, text="", **kwargs):
        content = text_pdf() if content is None else content
        kwargs.setdefault("content_type", "application/pdf")
        file = SimpleUploadedFile(name, content, **kwargs)
        return self.client.post(self.import_url(sprint), {"text": text, "pdf": file})

    def assert_rejected(self, response, expected_text=""):
        """The page shows an error, the LLM wasn't called and nothing was stored."""
        self.assertEqual(response.status_code, 200, "Expected the page to show an error")
        errors = [error for field in response.context["form"].errors.values() for error in field]
        self.assertTrue(errors, "Expected the page to show an error")
        for error in errors:
            self.assertContains(response, escape(error))
        self.assertIn(expected_text, " ".join(errors).lower())
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.stored(), [])


class UploadPageTests(UploadTestCase):
    def test_the_page_has_a_file_input_for_a_pdf(self):
        response = self.client.get(self.import_url())

        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'name="pdf"')

    def test_the_form_can_submit_a_file(self):
        # Without this a browser sends only the file's name, and the upload silently fails.
        response = self.client.get(self.import_url())

        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_the_page_can_still_be_used_to_paste(self):
        response = self.client.get(self.import_url())

        self.assertContains(response, 'name="text"')


class SameResultAsPastingTests(UploadTestCase):
    """An uploaded PDF produces the same result as pasting its text (US-9)."""

    def test_an_upload_stores_the_same_requirements_as_pasting_its_text(self):
        pasted_sprint = self.new_sprint("Pasted")

        pasted = self.paste(TEXT, sprint=pasted_sprint)
        uploaded = self.upload()

        self.assertEqual(pasted.status_code, 302)
        self.assertEqual(uploaded.status_code, 302)
        self.assertEqual(self.stored(), self.stored(pasted_sprint))
        self.assertEqual(self.stored(), list(enumerate(SPLIT, start=1)))

    def test_the_llm_receives_the_same_text_as_a_paste(self):
        pasted_sprint = self.new_sprint("Pasted")

        self.paste(TEXT, sprint=pasted_sprint)
        self.upload()

        self.assertEqual(len(self.llm.calls), 2, "Expected one LLM call for each import")
        pasted_prompt, uploaded_prompt = [call["messages"][0]["content"] for call in self.llm.calls]
        self.assertEqual(
            whitespace_normalized(uploaded_prompt), whitespace_normalized(pasted_prompt)
        )

    def test_the_instructor_is_told_how_many_were_created(self):
        file = SimpleUploadedFile("requirements.pdf", text_pdf(), content_type="application/pdf")

        response = self.client.post(self.import_url(), {"text": "", "pdf": file}, follow=True)

        self.assertContains(response, "3 requirements")

    def test_text_from_every_page_is_used(self):
        pdf = build_pdf([text_stream(LINES[:3]), text_stream(LINES[3:])])

        response = self.upload(pdf)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored(), list(enumerate(SPLIT, start=1)))

    def test_a_requirement_split_across_two_pages_is_found_in_the_source(self):
        pdf = build_pdf([text_stream(LINES[:2]), text_stream(LINES[2:])])

        response = self.upload(pdf)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored()[0], (1, SPLIT[0]))

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)
    def test_a_file_too_large_to_keep_in_memory_is_read_from_disk(self):
        response = self.upload()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.stored()), 3)

    def test_the_file_is_judged_by_its_contents_not_its_name_or_browser_type(self):
        cases = {
            "uppercase extension": {"name": "REQUIREMENTS.PDF"},
            "generic browser type": {"content_type": "application/octet-stream"},
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                sprint = self.new_sprint(label)

                response = self.upload(sprint=sprint, **kwargs)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(self.stored(sprint)), 3)

    def test_a_requirement_not_in_the_pdf_is_refused(self):
        self.llm.replies_with([*SPLIT, INVENTED])

        response = self.upload()

        self.assert_import_error(response, "source text")
        self.assertNotContains(response, INVENTED)
        self.assertEqual(self.stored(), [])

    def test_a_sprint_with_requirements_refuses_an_upload(self):
        Requirement.objects.create(sprint=self.sprint, number=1, text="Users can log out")

        response = self.upload()

        self.assert_import_error(response, "already has requirements")
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.stored(), [(1, "Users can log out")])

    def test_an_llm_failure_shows_an_error_and_stores_nothing(self):
        self.llm.error = anthropic.APITimeoutError(request=api_request())

        response = self.upload()

        self.assert_import_error(response, "couldn't be split")
        self.assertEqual(self.stored(), [])

    def test_a_pdf_and_pasted_text_together_are_refused(self):
        # Using one and ignoring the other could import the wrong document, and only once.
        response = self.upload(text=TEXT)

        self.assert_rejected(response, "both")


class UnreadableFileTests(UploadTestCase):
    """A scanned PDF, a corrupt file or a non-PDF shows a clear error, and creates nothing."""

    def test_a_scanned_pdf_with_no_text_is_rejected(self):
        self.assert_rejected(self.upload(scanned_pdf()), "scanned")

    def test_a_pdf_with_blank_pages_is_rejected(self):
        self.assert_rejected(self.upload(build_pdf([None, None])), "scanned")

    def test_a_pdf_containing_only_whitespace_is_rejected(self):
        self.assert_rejected(self.upload(text_pdf(["   ", "  "])), "scanned")

    def test_a_corrupt_file_is_rejected(self):
        whole = text_pdf()
        two_pages = build_pdf([text_stream(LINES[:3]), text_stream(LINES[3:])])
        cases = {
            "header then garbage": b"%PDF-1.7\n" + b"\x00\xff garbage " * 50,
            "only a header": b"%PDF-1.7\n",
            "cut off before any page": whole[: whole.index(b"3 0 obj")],
            "cut off after the first page": two_pages[: two_pages.index(b"5 0 obj")],
        }
        for label, content in cases.items():
            with self.subTest(label):
                self.assert_rejected(self.upload(content), "pdf")

    def test_a_pdf_with_an_unreadable_page_is_rejected_rather_than_partly_imported(self):
        # Importing page 1 alone would look like success, with requirements silently missing.
        self.assert_rejected(self.upload(damaged_second_page_pdf()), "pdf")

    def test_a_password_protected_pdf_is_rejected(self):
        self.assert_rejected(self.upload(password_protected_pdf()), "password")

    def test_a_file_that_is_not_a_pdf_is_rejected(self):
        cases = {
            "text with a pdf name": ("requirements.pdf", TEXT.encode(), "application/pdf"),
            "word document": ("requirements.docx", b"PK\x03\x04 not a pdf", "application/msword"),
            "image": ("requirements.png", b"\x89PNG\r\n\x1a\n not a pdf", "image/png"),
        }
        for label, (name, content, content_type) in cases.items():
            with self.subTest(label):
                response = self.upload(content, name=name, content_type=content_type)

                self.assert_rejected(response, "pdf")

    def test_an_empty_file_is_rejected(self):
        self.assert_rejected(self.upload(b""), "file")

    def test_pasting_still_works_after_a_rejected_upload(self):
        self.assert_rejected(self.upload(scanned_pdf()), "scanned")

        response = self.paste(TEXT)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.stored()), 3)


class NoRequirementsTests(UploadTestCase):
    """A project with no requirements still works.

    Summaries for such a project are covered in summaries/tests/test_generate_summary.py.
    """

    def assert_project_works_without_requirements(self):
        Enrollment.objects.get_or_create(
            user=self.admin, course=self.course, role=Enrollment.Role.INSTRUCTOR
        )
        self.assertEqual(self.stored(), [])

        project_page = self.client.get(reverse("admin:core_project_change", args=[self.project.pk]))
        team_page = self.client.get(reverse("dashboard:team", args=[self.team.pk]))
        overview = self.client.get(reverse("dashboard:course_overview", args=[self.course.pk]))

        self.assertEqual(project_page.status_code, 200)
        self.assertEqual(team_page.status_code, 200)
        self.assertContains(overview, NO_REQUIREMENTS_MESSAGE)

    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(project=self.project, name="Team Alpha")

    def test_a_project_that_never_imported_requirements_works(self):
        self.assert_project_works_without_requirements()

    def test_a_project_works_after_a_rejected_upload(self):
        self.assert_rejected(self.upload(scanned_pdf()), "scanned")

        self.assert_project_works_without_requirements()
