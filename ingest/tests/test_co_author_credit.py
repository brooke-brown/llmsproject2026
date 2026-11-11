"""Tests for US-3 (#19): crediting everyone named in a commit's Co-authored-by trailers.

These reuse the US-2 fakes, so nothing here touches the network.

A student is anyone on the team that owns the repo, through TeamMembership. Each test that
says someone isn't credited also expects something positive, such as another co-author
being credited or the stranger being kept on record, so that it can't pass just because
trailers were ignored. MalformedTrailerTests.test_collects_the_commit_and_credits_nobody
and the test of a commit with no trailers are the exceptions: they guard behavior that
ingest already has.
"""

from django.contrib.auth import get_user_model

from core.models import Enrollment, TeamMembership
from ingest.tests.test_ingest_commits import (
    FULL_NAME,
    STUDENT,
    IngestTestCase,
    fake_commit,
    sha,
)
from ingest.tests.test_student_artifacts import (
    add_repo,
    lookup,
    make_project,
    make_team,
    make_user,
)

PARTNER = "sample-partner"
PARTNER_EMAIL = "partner.account@example.com"
PARTNER_TRAILER = f"Co-authored-by: Sample Partner <{PARTNER_EMAIL}>"
TEAMMATE = "sample-teammate"
TEAMMATE_EMAIL = "teammate.account@example.com"
TEAMMATE_TRAILER = f"Co-authored-by: Sample Teammate <{TEAMMATE_EMAIL}>"
STRANGER_EMAIL = "stranger@example.com"
STRANGER_TRAILER = f"Co-authored-by: Sample Stranger <{STRANGER_EMAIL}>"
STRANGER_RECORD = {"name": "Sample Stranger", "email": STRANGER_EMAIL}


def message_with(*lines):
    return "\n".join(["Add the task model", "", *lines])


class CoAuthorTestCase(IngestTestCase):
    def setUp(self):
        super().setUp()
        self.partner = self.add_student(PARTNER, PARTNER_EMAIL)
        self.teammate = self.add_student(TEAMMATE, TEAMMATE_EMAIL)

    def add_student(self, github_login, email, team=None):
        user = get_user_model().objects.create_user(
            username=github_login, email=email, github_login=github_login
        )
        TeamMembership.objects.create(user=user, team=team or self.team)
        return user

    def ingest_message(self, message):
        """Ingests one commit with this message and returns its saved artifact."""
        return self.ingest_one(fake_commit(sha("a"), message=message))

    def ingest_trailers(self, *lines):
        return self.ingest_message(message_with(*lines))

    def assert_credit(self, artifact, logins, unmatched=()):
        # assertCountEqual also catches someone who's credited twice.
        self.assertCountEqual(artifact.co_author_logins, logins)
        self.assertCountEqual(artifact.unmatched_co_authors, unmatched)


class TrailerTests(CoAuthorTestCase):
    def test_credits_a_co_author_whatever_the_letter_case_of_the_trailer(self):
        for key in ("Co-authored-by", "co-authored-by", "CO-AUTHORED-BY", "Co-Authored-By"):
            with self.subTest(key=key):
                artifact = self.ingest_trailers(f"{key}: Sample Partner <{PARTNER_EMAIL}>")
                self.assert_credit(artifact, [PARTNER])

    def test_credits_everyone_named_in_several_trailers(self):
        artifact = self.ingest_trailers(PARTNER_TRAILER, TEAMMATE_TRAILER)
        self.assert_credit(artifact, [PARTNER, TEAMMATE])

    def test_reads_trailers_among_other_trailers(self):
        artifact = self.ingest_trailers(
            "Signed-off-by: Sample Student <student@example.com>",
            PARTNER_TRAILER,
            "Reviewed-by: Sample Reviewer <reviewer@example.com>",
            TEAMMATE_TRAILER,
        )
        self.assert_credit(artifact, [PARTNER, TEAMMATE])

    def test_reads_trailers_with_windows_line_endings(self):
        artifact = self.ingest_message(
            f"Add the task model\r\n\r\n{PARTNER_TRAILER}\r\n{TEAMMATE_TRAILER}\r\n"
        )
        self.assert_credit(artifact, [PARTNER, TEAMMATE])

    def test_reads_a_trailer_with_no_space_after_the_colon(self):
        artifact = self.ingest_trailers(f"Co-authored-by:Sample Partner <{PARTNER_EMAIL}>")
        self.assert_credit(artifact, [PARTNER])

    def test_reads_a_trailer_outside_the_last_paragraph(self):
        # A squash merge's message keeps each squashed commit's trailers in its own paragraph.
        artifact = self.ingest_message(
            f"Squash the task work\n\n* Add the model\n\n{PARTNER_TRAILER}\n\n"
            f"* Add the view\n\n{TEAMMATE_TRAILER}\n"
        )
        self.assert_credit(artifact, [PARTNER, TEAMMATE])

    def test_a_mention_inside_a_sentence_is_not_a_trailer(self):
        artifact = self.ingest_trailers(
            f"Fix the parser for Co-authored-by: Sample Teammate <{TEAMMATE_EMAIL}> lines.",
            PARTNER_TRAILER,
        )
        self.assert_credit(artifact, [PARTNER])

    def test_other_trailers_credit_nobody(self):
        artifact = self.ingest_trailers(
            f"Reviewed-by: Sample Teammate <{TEAMMATE_EMAIL}>",
            f"Not-Co-authored-by: Sample Teammate <{TEAMMATE_EMAIL}>",
            PARTNER_TRAILER,
        )
        self.assert_credit(artifact, [PARTNER])

    def test_a_commit_with_no_trailers_credits_no_co_authors(self):
        self.assert_credit(self.ingest_message("Add the task model"), [])

    def test_a_credited_co_author_finds_the_commit_in_their_week(self):
        artifact = self.ingest_trailers(PARTNER_TRAILER)
        self.assertEqual([found.pk for found in lookup(self.partner, self.team)], [artifact.pk])

    def test_crediting_a_co_author_keeps_the_commits_author(self):
        artifact = self.ingest_trailers(PARTNER_TRAILER)
        self.assert_credit(artifact, [PARTNER])
        self.assertEqual(artifact.author_login, STUDENT)


class NoreplyEmailTests(CoAuthorTestCase):
    def test_matches_both_noreply_formats(self):
        for email in (
            f"12345+{PARTNER}@users.noreply.github.com",
            f"{PARTNER}@users.noreply.github.com",
        ):
            with self.subTest(email=email):
                artifact = self.ingest_trailers(f"Co-authored-by: Anyone <{email}>")
                self.assert_credit(artifact, [PARTNER])

    def test_ignores_case_and_stores_the_students_own_login(self):
        for email in (
            "12345+Sample-PARTNER@users.noreply.github.com",
            "SAMPLE-PARTNER@USERS.NOREPLY.GITHUB.COM",
        ):
            with self.subTest(email=email):
                artifact = self.ingest_trailers(f"Co-authored-by: Anyone <{email}>")
                self.assert_credit(artifact, [PARTNER])

    def test_matches_the_whole_login_not_part_of_it(self):
        self.add_student(f"{PARTNER}-2", "partner.two@example.com")
        artifact = self.ingest_trailers(
            f"Co-authored-by: Anyone <{PARTNER}-2@users.noreply.github.com>"
        )
        self.assert_credit(artifact, [f"{PARTNER}-2"])

    def test_a_login_outside_the_team_is_kept_on_record(self):
        make_user("sample-outsider")
        email = "12345+sample-outsider@users.noreply.github.com"
        artifact = self.ingest_trailers(f"Co-authored-by: Sample Outsider <{email}>")
        self.assert_credit(artifact, [], [{"name": "Sample Outsider", "email": email}])

    def test_addresses_that_only_look_like_noreply_are_kept_on_record(self):
        for email in (
            f"{PARTNER}@users.noreply.github.com.example.com",
            f"{PARTNER}@evilusers.noreply.github.com",
            f"{PARTNER}@noreply.github.com",
            f"12345+{PARTNER}@example.com",
            f"abc+{PARTNER}@users.noreply.github.com",
            f"12345+other+{PARTNER}@users.noreply.github.com",
        ):
            with self.subTest(email=email):
                artifact = self.ingest_trailers(f"Co-authored-by: Anyone <{email}>")
                self.assert_credit(artifact, [], [{"name": "Anyone", "email": email}])


class AccountEmailTests(CoAuthorTestCase):
    def test_matches_a_students_account_email(self):
        self.assert_credit(self.ingest_trailers(PARTNER_TRAILER), [PARTNER])

    def test_ignores_case_in_the_trailer(self):
        artifact = self.ingest_trailers(f"Co-authored-by: Anyone <{PARTNER_EMAIL.upper()}>")
        self.assert_credit(artifact, [PARTNER])

    def test_ignores_case_in_the_account_email(self):
        self.add_student("sample-mixed", "Mixed.Case@Example.COM")
        artifact = self.ingest_trailers("Co-authored-by: Anyone <mixed.case@example.com>")
        self.assert_credit(artifact, ["sample-mixed"])

    def test_a_login_at_another_domain_is_not_an_account_email(self):
        email = f"{PARTNER}@example.com"
        artifact = self.ingest_trailers(f"Co-authored-by: Anyone <{email}>")
        self.assert_credit(artifact, [], [{"name": "Anyone", "email": email}])

    def test_a_student_on_another_team_is_kept_on_record(self):
        other_team = make_team(self.project, "Team B")
        self.add_student("sample-other", "other.team@example.com", team=other_team)
        artifact = self.ingest_trailers("Co-authored-by: Anyone <other.team@example.com>")
        self.assert_credit(artifact, [], [{"name": "Anyone", "email": "other.team@example.com"}])

    def test_course_staff_not_on_the_team_are_kept_on_record(self):
        ta = get_user_model().objects.create_user(
            username="sample-ta", email="ta@example.com", github_login="sample-ta"
        )
        Enrollment.objects.create(user=ta, course=self.project.course, role="ta")
        artifact = self.ingest_trailers("Co-authored-by: Anyone <ta@example.com>")
        self.assert_credit(artifact, [], [{"name": "Anyone", "email": "ta@example.com"}])

    def test_an_email_shared_by_two_teammates_credits_neither(self):
        # User.email isn't unique, and guessing could credit the wrong student.
        self.add_student("sample-twin-a", "shared@example.com")
        self.add_student("sample-twin-b", "SHARED@example.com")
        artifact = self.ingest_trailers("Co-authored-by: Anyone <shared@example.com>")
        self.assert_credit(artifact, [], [{"name": "Anyone", "email": "shared@example.com"}])

    def test_credit_follows_the_team_that_owns_the_repo(self):
        # A later project can reuse the GitHub repo with different teams.
        later_team = make_team(make_project(self.project.course, "Project 2"))
        later_repo = add_repo(later_team, FULL_NAME)
        message = message_with(PARTNER_TRAILER)
        branches = {"main": [fake_commit(sha("a"), message=message)]}

        original = self.ingest(branches)[0]
        copy = self.ingest(branches, repo=later_repo)[0]

        self.assert_credit(original, [PARTNER])
        self.assert_credit(
            copy, [], [{"name": "Sample Partner", "email": PARTNER_EMAIL}]
        )


class NamesAreNeverMatchedTests(CoAuthorTestCase):
    def setUp(self):
        super().setUp()
        self.partner.first_name = "Sample"
        self.partner.last_name = "Partner"
        self.partner.save()

    def test_a_matching_name_with_someone_elses_email_credits_nobody(self):
        for name in ("Sample Partner", "sample partner", PARTNER):
            with self.subTest(name=name):
                artifact = self.ingest_trailers(f"Co-authored-by: {name} <{STRANGER_EMAIL}>")
                self.assert_credit(artifact, [], [{"name": name, "email": STRANGER_EMAIL}])
                self.assertEqual(lookup(self.partner, self.team), [])

    def test_a_matching_name_with_no_email_credits_nobody(self):
        artifact = self.ingest_trailers("Co-authored-by: Sample Partner", TEAMMATE_TRAILER)
        self.assertCountEqual(artifact.co_author_logins, [TEAMMATE])

    def test_a_right_email_with_an_unrelated_name_still_credits_the_student(self):
        artifact = self.ingest_trailers(f"Co-authored-by: Nickname <{PARTNER_EMAIL}>")
        self.assert_credit(artifact, [PARTNER])


class UnmatchedCoAuthorTests(CoAuthorTestCase):
    def test_keeps_the_name_and_email_as_written_and_credits_nobody(self):
        artifact = self.ingest_trailers("Co-authored-by: José Stranger <Jose.S@Example.com>")
        record = {"name": "José Stranger", "email": "Jose.S@Example.com"}
        self.assert_credit(artifact, [], [record])
        self.assertEqual(lookup(self.partner, self.team), [])

    def test_keeps_matched_and_unmatched_co_authors_apart(self):
        artifact = self.ingest_trailers(PARTNER_TRAILER, STRANGER_TRAILER)
        self.assert_credit(artifact, [PARTNER], [STRANGER_RECORD])

    def test_a_run_after_a_student_joins_the_team_credits_them(self):
        message = message_with("Co-authored-by: Sample Joiner <joiner@example.com>")
        joiner = {"name": "Sample Joiner", "email": "joiner@example.com"}
        self.assert_credit(self.ingest_message(message), [], [joiner])

        self.add_student("sample-joiner", "joiner@example.com")

        self.assert_credit(self.ingest_message(message), ["sample-joiner"])

    def test_a_run_after_a_student_leaves_the_team_removes_their_credit(self):
        message = message_with(PARTNER_TRAILER)
        self.assert_credit(self.ingest_message(message), [PARTNER])

        TeamMembership.objects.filter(user=self.partner, team=self.team).delete()

        self.assert_credit(
            self.ingest_message(message),
            [],
            [{"name": "Sample Partner", "email": PARTNER_EMAIL}],
        )


class MalformedTrailerTests(CoAuthorTestCase):
    MALFORMED = (
        "Co-authored-by:",
        "Co-authored-by: Sample Partner",
        "Co-authored-by: Sample Partner <",
        f"Co-authored-by: Sample Partner <{PARTNER_EMAIL}",
        f"Co-authored-by: Sample Partner {PARTNER_EMAIL}>",
        f"Co-authored-by: {PARTNER_EMAIL}",
        "Co-authored-by: Sample Partner <not an email>",
        "Co-authored-by: Sample Partner <>",
        "Co-authored-by: <>",
    )

    def test_collects_the_commit_and_credits_nobody(self):
        for line in self.MALFORMED:
            with self.subTest(line=line):
                artifact = self.ingest_trailers(line)
                self.assertEqual(artifact.title, message_with(line))
                self.assertEqual(artifact.co_author_logins, [])

    def test_still_credits_the_valid_trailers_beside_it(self):
        for line in self.MALFORMED:
            with self.subTest(line=line):
                artifact = self.ingest_trailers(line, TEAMMATE_TRAILER)
                self.assertEqual(artifact.co_author_logins, [TEAMMATE])

    def test_an_empty_email_never_matches_a_student_with_no_account_email(self):
        self.add_student("sample-no-email", "")
        artifact = self.ingest_trailers("Co-authored-by: Anyone <>", TEAMMATE_TRAILER)
        self.assertEqual(artifact.co_author_logins, [TEAMMATE])


class RepeatedCoAuthorTests(CoAuthorTestCase):
    def test_the_same_trailer_twice_credits_once(self):
        artifact = self.ingest_trailers(PARTNER_TRAILER, PARTNER_TRAILER)
        self.assert_credit(artifact, [PARTNER])

    def test_trailers_differing_only_in_case_credit_once(self):
        artifact = self.ingest_trailers(
            PARTNER_TRAILER, f"CO-AUTHORED-BY: SAMPLE PARTNER <{PARTNER_EMAIL.upper()}>"
        )
        self.assert_credit(artifact, [PARTNER])

    def test_one_student_under_several_emails_is_credited_once(self):
        artifact = self.ingest_trailers(
            PARTNER_TRAILER,
            f"Co-authored-by: Sample Partner <{PARTNER}@users.noreply.github.com>",
            f"Co-authored-by: S. Partner <12345+{PARTNER}@users.noreply.github.com>",
        )
        self.assert_credit(artifact, [PARTNER])

    def test_a_repeated_unmatched_co_author_is_kept_once(self):
        artifact = self.ingest_trailers(
            STRANGER_TRAILER, f"Co-authored-by: Sample Stranger <{STRANGER_EMAIL.upper()}>"
        )
        self.assert_credit(artifact, [], [STRANGER_RECORD])

    def test_an_author_naming_themselves_gets_the_commit_once(self):
        student = self.add_student(STUDENT, "student.account@example.com")
        artifact = self.ingest_trailers(
            "Co-authored-by: Sample Student <student.account@example.com>", PARTNER_TRAILER
        )
        self.assertEqual([found.pk for found in lookup(student, self.team)], [artifact.pk])
        self.assertIn(PARTNER, artifact.co_author_logins)
