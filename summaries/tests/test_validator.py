"""Tests for US-11 (#36): the validator that checks every claim against stored GitHub data.

The validator is imported inside each test rather than at the top of the file, so each
criterion passes or fails on its own instead of the whole module failing to import.

The tests are built on the sample artifact fixture. Two of its artifacts are there to
catch a check that compares UTC dates instead of New York ones: commit d4d4... is
stamped Oct 26 03:30 UTC, which is still Sunday Oct 25 in New York and so before the
week, and issue 3 is stamped Nov 2 04:30 UTC, which is still Sunday Nov 1 in New York
and so inside it.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from core.models import Project, Repo, Requirement, Team
from ingest.models import Artifact
from summaries.schemas import Claim
from summaries.tests.week_shim import sprint_for_week

NEW_YORK = ZoneInfo("America/New_York")
WEEK_START = date(2026, 10, 26)  # A Monday. The week ends Sunday Nov 1.

TEAM_A_REPO = "sample-strive-org/team-a-app"
TEAM_B_REPO = "sample-strive-org/team-b-app"

# Artifacts in the fixture, all in team A's repo unless noted.
COMMIT = f"{TEAM_A_REPO}:commit:{'a1' * 20}"  # By student A, mid-week.
PR = f"{TEAM_A_REPO}:pr:1"  # By student A.
REVIEW = f"{TEAM_A_REPO}:review:1001"  # Submitted by student A.
ISSUE_SUNDAY_NIGHT = f"{TEAM_A_REPO}:issue:3"  # By A, Sunday Nov 1 at 11:30pm New York.
CO_AUTHORED = f"{TEAM_A_REPO}:commit:{'b2' * 20}"  # By student B, with A as co-author.
UNATTRIBUTED = f"{TEAM_A_REPO}:commit:{'c3' * 20}"  # Its git email matches no login.
BEFORE_WEEK = f"{TEAM_A_REPO}:commit:{'d4' * 20}"  # By A, Sunday Oct 25 at 11:30pm New York.
STUDENT_B_PR = f"{TEAM_A_REPO}:pr:2"  # By student B alone.
TEAM_B_COMMIT = f"{TEAM_B_REPO}:commit:{'e5' * 20}"  # By A, but in team B's repo.
MISSING = f"{TEAM_A_REPO}:pr:99"  # Well formed, but no such artifact.


def validate(claims, user, team, week_start=WEEK_START):
    from summaries.validator import validate_sprint_claims

    sprint = sprint_for_week(team.project, week_start)
    return validate_sprint_claims(claims, user, team, sprint, sprint.end_date)


def make_claim(*artifact_ids):
    return Claim(
        text="Worked on the login flow.",
        category="feature",
        artifact_ids=list(artifact_ids),
        requirement_ids=[],
        significance="minor",
    )


class ValidatorTestCase(TestCase):
    def setUp(self):
        call_command("loaddata", "sample_artifacts", verbosity=0)
        self.team = Team.objects.get(name="Sample Team A")
        self.team_b = Team.objects.get(name="Sample Team B")
        self.student = get_user_model().objects.get(github_login="sample-student-a")
        self.student_b = get_user_model().objects.get(github_login="sample-student-b")

    def check(self, *artifact_ids, user=None, team=None, week_start=WEEK_START):
        """Validate one claim citing the given artifacts, and return its result."""
        results = validate(
            [make_claim(*artifact_ids)], user or self.student, team or self.team, week_start
        )
        self.assertEqual(len(results), 1)
        return results[0]

    def add_artifact(self, suffix, timestamp, author_login="sample-student-a", **fields):
        """A commit in team A's repo, for tests that need an exact timestamp."""
        artifact_id = f"{TEAM_A_REPO}:commit:{suffix}"
        Artifact.objects.create(
            repo=Repo.objects.get(full_name=TEAM_A_REPO),
            artifact_id=artifact_id,
            type="commit",
            url=f"https://github.com/{TEAM_A_REPO}/commit/{suffix}",
            timestamp=timestamp,
            author_login=author_login,
            **fields,
        )
        return artifact_id

    def checks_of(self, result):
        return [error.check for error in result.errors]


class ValidClaimTests(ValidatorTestCase):
    def test_a_claim_citing_the_students_own_artifact_passes(self):
        result = self.check(COMMIT)

        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_every_artifact_type_the_student_made_passes(self):
        for artifact_id in [COMMIT, PR, REVIEW, ISSUE_SUNDAY_NIGHT]:
            with self.subTest(artifact_id=artifact_id):
                self.assertTrue(self.check(artifact_id).ok)

    def test_a_review_passes_for_its_reviewer(self):
        # A review's author_login is the reviewer, so the reviewer is credited.
        self.assertTrue(self.check(REVIEW).ok)

    def test_a_co_authored_commit_passes_for_the_co_author(self):
        self.assertTrue(self.check(CO_AUTHORED).ok)

    def test_a_claim_citing_several_valid_artifacts_passes(self):
        self.assertTrue(self.check(COMMIT, PR, REVIEW, CO_AUTHORED).ok)

    def test_the_students_login_is_matched_ignoring_case(self):
        self.student.github_login = "SAMPLE-Student-A"

        self.assertTrue(self.check(COMMIT, CO_AUTHORED, user=self.student).ok)

    def test_the_artifacts_author_is_matched_ignoring_case(self):
        artifact_id = self.add_artifact(
            "caseauthor1", datetime(2026, 10, 28, 12, 0, tzinfo=NEW_YORK), author_login="Sample-STUDENT-A"
        )

        self.assertTrue(self.check(artifact_id).ok)

    def test_an_artifact_in_the_team_being_checked_passes_for_that_team(self):
        # Student A's commit in team B's repo is valid when checking team B.
        self.assertTrue(self.check(TEAM_B_COMMIT, team=self.team_b).ok)


class ArtifactExistsTests(ValidatorTestCase):
    def test_an_artifact_that_does_not_exist_fails(self):
        result = self.check(MISSING)

        self.assertFalse(result.ok)
        self.assertEqual(self.checks_of(result), ["artifact_not_found"])

    def test_an_artifact_in_another_teams_repo_fails(self):
        result = self.check(TEAM_B_COMMIT)

        self.assertFalse(result.ok)
        self.assertEqual(self.checks_of(result), ["artifact_not_found"])

    def test_the_failure_names_the_artifact_that_was_not_found(self):
        result = self.check(MISSING)

        self.assertEqual(result.errors[0].artifact_id, MISSING)


class MalformedIdTests(ValidatorTestCase):
    def test_a_malformed_artifact_id_fails(self):
        for artifact_id in ["", "not-an-id", f"{TEAM_A_REPO}:pull:1", f"{TEAM_A_REPO}:pr:"]:
            with self.subTest(artifact_id=artifact_id):
                result = self.check(artifact_id)

                self.assertFalse(result.ok)
                self.assertEqual(self.checks_of(result), ["malformed_id"])

    def test_the_failure_names_the_malformed_id(self):
        self.assertEqual(self.check("not-an-id").errors[0].artifact_id, "not-an-id")


class ReportingWeekTests(ValidatorTestCase):
    def test_an_artifact_before_the_week_fails(self):
        result = self.check(BEFORE_WEEK)

        self.assertFalse(result.ok)
        self.assertEqual(self.checks_of(result), ["outside_dates"])

    def test_the_week_is_judged_in_new_york_time_not_utc(self):
        # d4d4... is Oct 26 in UTC, so a UTC date check would wrongly accept it.
        self.assertEqual(self.checks_of(self.check(BEFORE_WEEK)), ["outside_dates"])
        # Issue 3 is Nov 2 in UTC, so a UTC date check would wrongly reject it.
        self.assertTrue(self.check(ISSUE_SUNDAY_NIGHT).ok)

    def test_the_first_moment_of_the_week_passes(self):
        artifact_id = self.add_artifact("monday00", datetime(2026, 10, 26, 0, 0, tzinfo=NEW_YORK))

        self.assertTrue(self.check(artifact_id).ok)

    def test_the_last_moment_of_the_week_passes(self):
        # The clock change on Sunday Nov 1 falls inside this week.
        artifact_id = self.add_artifact(
            "sunday2359", datetime(2026, 11, 1, 23, 59, 59, 999999, tzinfo=NEW_YORK)
        )

        self.assertTrue(self.check(artifact_id).ok)

    def test_the_first_moment_of_the_next_week_fails(self):
        artifact_id = self.add_artifact("nextmonday", datetime(2026, 11, 2, 0, 0, tzinfo=NEW_YORK))

        self.assertEqual(self.checks_of(self.check(artifact_id)), ["outside_dates"])

    def test_a_moment_before_the_week_fails(self):
        artifact_id = self.add_artifact(
            "justbefore", datetime(2026, 10, 25, 23, 59, 59, 999999, tzinfo=NEW_YORK)
        )

        self.assertEqual(self.checks_of(self.check(artifact_id)), ["outside_dates"])

    def test_the_message_says_which_week_was_expected(self):
        message = self.check(BEFORE_WEEK).errors[0].message

        self.assertIn("2026-10-26", message)
        self.assertIn("2026-11-01", message)


class ContributorTests(ValidatorTestCase):
    def test_an_artifact_by_another_student_fails(self):
        result = self.check(STUDENT_B_PR)

        self.assertFalse(result.ok)
        self.assertEqual(self.checks_of(result), ["not_contributor"])

    def test_an_unattributed_commit_fails(self):
        # Its author has no GitHub login, so it isn't credited to anyone.
        self.assertEqual(self.checks_of(self.check(UNATTRIBUTED)), ["not_contributor"])

    def test_a_login_that_only_contains_the_students_login_does_not_match(self):
        artifact_id = self.add_artifact(
            "similarlogin", datetime(2026, 10, 28, 12, 0, tzinfo=NEW_YORK), author_login="sample-student-a-2"
        )

        self.assertEqual(self.checks_of(self.check(artifact_id)), ["not_contributor"])

    def test_the_check_is_for_the_student_being_summarized(self):
        # B authored the PR that A did not, and A's commit isn't B's.
        self.assertTrue(self.check(STUDENT_B_PR, user=self.student_b).ok)
        self.assertEqual(
            self.checks_of(self.check(COMMIT, user=self.student_b)), ["not_contributor"]
        )


class ErrorShapeTests(ValidatorTestCase):
    def test_an_error_names_the_claim_the_artifact_and_the_check(self):
        error = self.check(MISSING).errors[0]

        self.assertEqual(error.claim_index, 0)
        self.assertEqual(error.artifact_id, MISSING)
        self.assertEqual(error.check, "artifact_not_found")
        self.assertIsInstance(error.message, str)
        self.assertTrue(error.message)

    def test_an_error_becomes_a_dict_that_can_be_stored_and_sent_to_the_llm(self):
        error = self.check(MISSING).errors[0]

        self.assertEqual(
            error.to_dict(),
            {
                "claim_index": 0,
                "artifact_id": MISSING,
                "check": "artifact_not_found",
                "message": error.message,
            },
        )


class SeveralProblemsTests(ValidatorTestCase):
    def test_every_problem_with_a_claim_is_reported(self):
        result = self.check("not-an-id", TEAM_B_COMMIT, STUDENT_B_PR)

        self.assertFalse(result.ok)
        self.assertEqual(
            {(error.artifact_id, error.check) for error in result.errors},
            {
                ("not-an-id", "malformed_id"),
                (TEAM_B_COMMIT, "artifact_not_found"),
                (STUDENT_B_PR, "not_contributor"),
            },
        )

    def test_one_artifact_with_two_problems_reports_both(self):
        # By another student, and also before the week.
        artifact_id = self.add_artifact(
            "twoproblems", datetime(2026, 10, 20, 12, 0, tzinfo=NEW_YORK), author_login="sample-student-b"
        )

        result = self.check(artifact_id)

        self.assertCountEqual(self.checks_of(result), ["outside_dates", "not_contributor"])

    def test_a_claim_with_one_bad_citation_fails_even_if_the_others_are_fine(self):
        result = self.check(COMMIT, MISSING, PR)

        self.assertFalse(result.ok)
        self.assertEqual([error.artifact_id for error in result.errors], [MISSING])


class SeveralClaimsTests(ValidatorTestCase):
    def test_each_claim_gets_its_own_result_in_order(self):
        claims = [make_claim(COMMIT), make_claim(MISSING), make_claim(PR)]

        results = validate(claims, self.student, self.team)

        self.assertEqual([result.claim_index for result in results], [0, 1, 2])
        self.assertEqual([result.ok for result in results], [True, False, True])

    def test_errors_carry_the_index_of_the_claim_they_belong_to(self):
        claims = [make_claim(COMMIT), make_claim(MISSING)]

        results = validate(claims, self.student, self.team)

        self.assertEqual(results[1].errors[0].claim_index, 1)

    def test_no_claims_gives_no_results(self):
        self.assertEqual(validate([], self.student, self.team), [])


class RequirementTests(ValidatorTestCase):
    """A claim fails if it cites a requirement number its sprint doesn't have.

    Requirement numbers are Requirement.number, not the database ID. The sample
    fixture's sprint has requirements 1 and 2.
    """

    def requirement_result(self, requirement_ids, *artifact_ids):
        claim = Claim(
            text="Worked on the task list.",
            category="feature",
            artifact_ids=list(artifact_ids) or [PR],
            requirement_ids=requirement_ids,
            significance="minor",
        )
        return validate([claim], self.student, self.team)[0]

    def test_numbers_the_project_has_pass(self):
        self.assertTrue(self.requirement_result([1, 2]).ok)

    def test_a_claim_tied_to_no_requirement_passes(self):
        self.assertTrue(self.requirement_result([]).ok)

    def test_a_number_the_project_does_not_have_fails(self):
        result = self.requirement_result([99])

        self.assertFalse(result.ok)
        self.assertEqual(self.checks_of(result), ["unknown_requirement"])

    def test_the_error_names_the_claim_and_the_requirement_number(self):
        error = self.requirement_result([99]).errors[0]

        self.assertEqual(error.claim_index, 0)
        self.assertEqual(error.requirement_id, 99)
        self.assertEqual(error.artifact_id, "")  # It's about a requirement, not an artifact.
        self.assertIn("99", error.message)

    def test_the_error_becomes_a_dict_that_includes_the_requirement_number(self):
        error = self.requirement_result([99]).errors[0]

        self.assertEqual(
            error.to_dict(),
            {
                "claim_index": 0,
                "artifact_id": "",
                "check": "unknown_requirement",
                "message": error.message,
                "requirement_id": 99,
            },
        )

    def test_an_artifact_error_still_has_no_requirement_number_in_its_dict(self):
        # Errors saved before requirements were checked keep exactly their old shape.
        error = self.check(MISSING).errors[0]

        self.assertNotIn("requirement_id", error.to_dict())

    def test_every_unknown_number_is_reported(self):
        result = self.requirement_result([98, 99, 1])

        self.assertEqual({error.requirement_id for error in result.errors}, {98, 99})

    def test_only_the_unknown_numbers_are_reported_when_some_are_fine(self):
        result = self.requirement_result([1, 99])

        self.assertEqual([error.requirement_id for error in result.errors], [99])

    def test_another_projects_requirement_does_not_count(self):
        other = Project.objects.create(course=self.team.project.course, name="Other project")
        Requirement.objects.create(
            sprint=sprint_for_week(other, WEEK_START), number=7, text="Belongs to another project."
        )

        self.assertEqual(self.checks_of(self.requirement_result([7])), ["unknown_requirement"])

    def test_numbers_are_matched_by_requirement_number_not_database_id(self):
        requirement = Requirement.objects.create(
            sprint=sprint_for_week(self.team.project, WEEK_START),
            number=5,
            text="Users can delete a task.",
        )
        self.assertNotIn(requirement.pk, {1, 2, 5})  # So the two can't be confused.

        self.assertTrue(self.requirement_result([5]).ok)
        self.assertEqual(self.checks_of(self.requirement_result([requirement.pk])), ["unknown_requirement"])

    def test_a_requirement_error_and_an_artifact_error_are_both_reported(self):
        result = self.requirement_result([99], STUDENT_B_PR)

        self.assertCountEqual(self.checks_of(result), ["not_contributor", "unknown_requirement"])

    def test_a_project_with_no_requirements_accepts_only_an_empty_list(self):
        Requirement.objects.filter(sprint__project=self.team.project).delete()

        self.assertTrue(self.requirement_result([]).ok)
        self.assertEqual(self.checks_of(self.requirement_result([1])), ["unknown_requirement"])

    def test_a_valid_claim_with_an_unknown_requirement_still_fails(self):
        # The artifact is fine, but a claim is only as good as everything it says.
        self.assertTrue(self.check(PR).ok)
        self.assertFalse(self.requirement_result([99], PR).ok)
