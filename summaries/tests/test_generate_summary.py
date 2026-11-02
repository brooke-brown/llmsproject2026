"""Tests for US-12 (#37): generate_summary, the weekly summary of a student's work.

generate_summary is imported inside each test rather than at the top of the file, so
each criterion passes or fails on its own instead of the whole module failing to import.

The LLM is replaced by a mock, so nothing here makes a network call: tests choose which
claims it "returns" and check what it was sent. The artifacts come from the sample
fixture, which the validator tests use too.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from core.models import Project, Repo, Requirement, Team
from ingest.models import Artifact
from summaries.llm import LLMError
from summaries.models import Claim as ClaimRow
from summaries.models import Summary
from summaries.schemas import Claim

NEW_YORK = ZoneInfo("America/New_York")
WEEK_START = date(2026, 10, 26)  # A Monday. The week ends Sunday Nov 1.
QUIET_WEEK = date(2026, 11, 2)  # The fixture has nothing for student A this week.
MID_WEEK = datetime(2026, 10, 28, 12, 0, tzinfo=NEW_YORK)

TEAM_A_REPO = "sample-strive-org/team-a-app"
TEAM_B_REPO = "sample-strive-org/team-b-app"

# The five artifacts the fixture credits to student A in WEEK_START's week.
COMMIT = f"{TEAM_A_REPO}:commit:{'a1' * 20}"
PR = f"{TEAM_A_REPO}:pr:1"
REVIEW = f"{TEAM_A_REPO}:review:1001"
ISSUE = f"{TEAM_A_REPO}:issue:3"
CO_AUTHORED = f"{TEAM_A_REPO}:commit:{'b2' * 20}"
STUDENT_A_ARTIFACTS = [COMMIT, PR, REVIEW, ISSUE, CO_AUTHORED]

# Fixture artifacts that must never reach the prompt for student A.
STUDENT_B_PR = f"{TEAM_A_REPO}:pr:2"
UNATTRIBUTED = f"{TEAM_A_REPO}:commit:{'c3' * 20}"
BEFORE_WEEK = f"{TEAM_A_REPO}:commit:{'d4' * 20}"
TEAM_B_COMMIT = f"{TEAM_B_REPO}:commit:{'e5' * 20}"
NOT_STUDENT_A = [STUDENT_B_PR, UNATTRIBUTED, BEFORE_WEEK, TEAM_B_COMMIT]

DIFF_CAP = 4000


def generate_summary(*args, **kwargs):
    from summaries.services import generate_summary

    return generate_summary(*args, **kwargs)


def make_claim(*artifact_ids, text="Added the task form.", category="feature", significance="minor"):
    return Claim(
        text=text,
        category=category,
        artifact_ids=list(artifact_ids),
        requirement_ids=[],
        significance=significance,
    )


class GenerateSummaryTestCase(TestCase):
    def setUp(self):
        call_command("loaddata", "sample_artifacts", verbosity=0)
        users = get_user_model().objects
        self.team = Team.objects.get(name="Sample Team A")
        self.student = users.get(github_login="sample-student-a")
        self.student_b = users.get(github_login="sample-student-b")
        self.student_c = users.get(github_login="sample-student-c")  # On team B, not team A.

        patcher = mock.patch("summaries.services.generate_claims")
        self.llm = patcher.start()
        self.addCleanup(patcher.stop)
        self.llm.return_value = [make_claim(PR)]

    def run_summary(self, user=None, team=None, week_start=WEEK_START):
        return generate_summary(user or self.student, team or self.team, week_start)

    @property
    def prompt(self):
        self.llm.assert_called_once()
        return self.llm.call_args.args[0]

    @property
    def system_prompt(self):
        self.llm.assert_called_once()
        return self.llm.call_args.kwargs["system"]

    def add_artifact(self, suffix, **fields):
        """A commit by student A in team A's repo, mid-week unless told otherwise."""
        values = {
            "type": "commit",
            "timestamp": MID_WEEK,
            "author_login": "sample-student-a",
            **fields,
        }
        return Artifact.objects.create(
            repo=Repo.objects.get(full_name=TEAM_A_REPO),
            artifact_id=f"{TEAM_A_REPO}:commit:{suffix}",
            url=f"https://github.com/{TEAM_A_REPO}/commit/{suffix}",
            **values,
        )


class SavedSummaryTests(GenerateSummaryTestCase):
    def test_returns_a_saved_summary_for_the_student_team_and_week(self):
        summary = self.run_summary()

        self.assertEqual(Summary.objects.get(pk=summary.pk), summary)
        self.assertEqual(summary.team, self.team)
        self.assertEqual(summary.user, self.student)
        self.assertEqual(summary.week_start, WEEK_START)

    def test_records_how_many_artifacts_the_student_had_that_week(self):
        self.assertEqual(self.run_summary().artifact_count, 5)

    def test_a_verified_claim_is_saved_with_its_fields(self):
        self.llm.return_value = [
            make_claim(PR, text="Built the task form.", category="feature", significance="major")
        ]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "verified")
        self.assertEqual(claim.text, "Built the task form.")
        self.assertEqual(claim.category, "feature")
        self.assertEqual(claim.significance, "major")
        self.assertEqual(claim.artifact_ids, [PR])
        self.assertEqual(claim.validator_errors, [])

    def test_a_verified_claim_is_linked_to_the_artifacts_it_cites(self):
        self.llm.return_value = [make_claim(PR, COMMIT)]

        claim = self.run_summary().claims.get()

        self.assertCountEqual(
            [artifact.artifact_id for artifact in claim.artifacts.all()], [PR, COMMIT]
        )

    def test_a_claim_citing_a_co_authored_commit_is_verified(self):
        self.llm.return_value = [make_claim(CO_AUTHORED)]

        self.assertEqual(self.run_summary().claims.get().status, "verified")

    def test_several_claims_are_all_saved(self):
        self.llm.return_value = [make_claim(PR), make_claim(COMMIT), make_claim(REVIEW)]

        self.assertEqual(self.run_summary().claims.count(), 3)


class FailedClaimTests(GenerateSummaryTestCase):
    def test_a_claim_that_fails_validation_is_saved_as_could_not_verify(self):
        self.llm.return_value = [make_claim(STUDENT_B_PR)]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "couldnt_verify")
        self.assertEqual(claim.artifact_ids, [STUDENT_B_PR])

    def test_a_failed_claim_keeps_the_validators_errors(self):
        self.llm.return_value = [make_claim(STUDENT_B_PR)]

        claim = self.run_summary().claims.get()

        self.assertEqual(len(claim.validator_errors), 1)
        self.assertEqual(claim.validator_errors[0]["check"], "not_contributor")
        self.assertEqual(claim.validator_errors[0]["artifact_id"], STUDENT_B_PR)

    def test_a_failed_claim_is_not_linked_to_any_artifact(self):
        self.llm.return_value = [make_claim(STUDENT_B_PR)]

        self.assertEqual(self.run_summary().claims.get().artifacts.count(), 0)

    def test_a_made_up_artifact_id_fails_validation(self):
        self.llm.return_value = [make_claim(f"{TEAM_A_REPO}:pr:999")]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "couldnt_verify")
        self.assertEqual(claim.validator_errors[0]["check"], "artifact_not_found")

    def test_a_claim_with_one_good_and_one_bad_citation_fails(self):
        self.llm.return_value = [make_claim(PR, BEFORE_WEEK)]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "couldnt_verify")

    def test_the_summary_counts_the_claims_that_could_not_be_verified(self):
        self.llm.return_value = [
            make_claim(PR),
            make_claim(STUDENT_B_PR),
            make_claim(TEAM_B_COMMIT),
        ]

        summary = self.run_summary()

        self.assertEqual(summary.unverified_count, 2)
        self.assertEqual(summary.claims.filter(status="verified").count(), 1)

    def test_a_claim_is_judged_for_the_student_being_summarized(self):
        # B authored this PR, so it's fine in B's summary and not in A's.
        self.llm.return_value = [make_claim(STUDENT_B_PR)]

        self.assertEqual(self.run_summary(user=self.student).claims.get().status, "couldnt_verify")
        self.assertEqual(self.run_summary(user=self.student_b).claims.get().status, "verified")


class PromptTests(GenerateSummaryTestCase):
    def test_the_prompt_lists_every_artifact_the_student_had_that_week(self):
        self.run_summary()

        for artifact_id in STUDENT_A_ARTIFACTS:
            with self.subTest(artifact_id=artifact_id):
                self.assertIn(artifact_id, self.prompt)

    def test_the_prompt_leaves_out_everything_else(self):
        self.run_summary()

        for artifact_id in NOT_STUDENT_A:
            with self.subTest(artifact_id=artifact_id):
                self.assertNotIn(artifact_id, self.prompt)

    def test_the_prompt_includes_titles_and_bodies(self):
        self.run_summary()

        self.assertIn("Add a form for creating tasks", self.prompt)  # A PR title.
        self.assertIn("Tasks disappear after a page refresh", self.prompt)  # An issue title.
        self.assertIn("Approved. The done button work", self.prompt)  # A review's body.

    def test_the_prompt_includes_a_commits_diff(self):
        commit = Artifact.objects.get(artifact_id=COMMIT, repo__full_name=TEAM_A_REPO)

        self.run_summary()

        self.assertIn(commit.diff, self.prompt)

    def test_a_long_diff_is_cut_to_the_cap_and_marked(self):
        self.add_artifact("longdiff1", diff="A" * DIFF_CAP + "B" * 6000)

        self.run_summary()

        self.assertIn("A" * DIFF_CAP, self.prompt)
        self.assertNotIn("B" * 50, self.prompt)
        self.assertIn("diff truncated", self.prompt)

    def test_a_short_diff_is_not_marked_as_truncated(self):
        self.run_summary()

        self.assertNotIn("diff truncated", self.prompt)

    def test_the_prompt_does_not_include_the_students_other_weeks(self):
        self.add_artifact(
            "lastweek1", timestamp=datetime(2026, 10, 20, 12, 0, tzinfo=NEW_YORK), title="LAST-WEEK-WORK"
        )

        self.run_summary()

        self.assertNotIn("LAST-WEEK-WORK", self.prompt)


class SystemPromptTests(GenerateSummaryTestCase):
    def test_tells_the_llm_to_describe_observable_activity_only(self):
        self.run_summary()

        text = self.system_prompt.lower()
        self.assertIn("observable activity", text)
        self.assertIn("character", text)  # No judgments of character.

    def test_tells_the_llm_to_cite_artifact_ids_exactly_as_listed(self):
        self.run_summary()

        text = self.system_prompt.lower()
        self.assertIn("artifact id", text)
        self.assertIn("exactly", text)

    def test_tells_the_llm_to_judge_significance_by_what_the_work_accomplishes(self):
        self.run_summary()

        text = self.system_prompt.lower()
        self.assertIn("significance", text)
        self.assertIn("lines", text)  # ...not by lines changed.

    def test_tells_the_llm_what_to_put_in_requirement_ids(self):
        self.run_summary()

        text = self.system_prompt.lower()
        self.assertIn("requirement_ids", self.system_prompt)
        self.assertIn("using only the numbers", text)  # From the project's own list.
        self.assertIn("empty list", text)  # When the work advances none.
        self.assertNotIn("always set requirement_ids to an empty list", text)


class NoActivityTests(GenerateSummaryTestCase):
    def test_a_week_with_no_activity_makes_no_llm_call(self):
        self.run_summary(week_start=QUIET_WEEK)

        self.llm.assert_not_called()

    def test_a_week_with_no_activity_gives_a_summary_with_no_claims(self):
        summary = self.run_summary(week_start=QUIET_WEEK)

        self.assertEqual(Summary.objects.get(pk=summary.pk), summary)
        self.assertEqual(summary.claims.count(), 0)
        self.assertEqual(summary.artifact_count, 0)
        self.assertEqual(summary.unverified_count, 0)

    def test_a_week_where_the_llm_makes_no_claims_gives_an_empty_summary(self):
        self.llm.return_value = []

        summary = self.run_summary()

        self.assertEqual(summary.claims.count(), 0)
        self.assertEqual(summary.artifact_count, 5)
        self.assertEqual(summary.unverified_count, 0)


class RejectedInputTests(GenerateSummaryTestCase):
    def test_a_week_start_that_is_not_a_monday_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "Monday"):
            self.run_summary(week_start=date(2026, 10, 28))

        self.llm.assert_not_called()
        self.assertEqual(Summary.objects.count(), 0)

    def test_a_student_who_is_not_on_the_team_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "team"):
            self.run_summary(user=self.student_c)

        self.llm.assert_not_called()
        self.assertEqual(Summary.objects.count(), 0)


class LLMFailureTests(GenerateSummaryTestCase):
    def test_a_failed_llm_call_raises_and_saves_nothing(self):
        self.llm.side_effect = LLMError("The LLM request timed out.")

        with self.assertRaisesMessage(LLMError, "timed out"):
            self.run_summary()

        self.assertEqual(Summary.objects.count(), 0)
        self.assertEqual(ClaimRow.objects.count(), 0)

    def test_a_failed_regeneration_leaves_the_earlier_summary_as_it_was(self):
        first = self.run_summary()
        self.llm.side_effect = LLMError("The LLM rate limit was hit.")

        with self.assertRaises(LLMError):
            self.run_summary()

        self.assertEqual(Summary.objects.get(), first)
        self.assertEqual([claim.artifact_ids for claim in first.claims.all()], [[PR]])


class RegenerationTests(GenerateSummaryTestCase):
    def test_regenerating_a_week_reuses_the_same_summary(self):
        first = self.run_summary()

        second = self.run_summary()

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(Summary.objects.count(), 1)

    def test_regenerating_a_week_replaces_the_old_claims(self):
        self.run_summary()
        self.llm.return_value = [make_claim(COMMIT, text="Added the task model.")]

        summary = self.run_summary()

        self.assertEqual([claim.text for claim in summary.claims.all()], ["Added the task model."])
        self.assertEqual(ClaimRow.objects.count(), 1)

    def test_regenerating_does_not_touch_another_students_summary(self):
        other = self.run_summary(user=self.student_b)

        self.run_summary(user=self.student)

        self.assertEqual(other.claims.count(), 1)
        self.assertEqual(Summary.objects.count(), 2)

    def test_another_week_gets_its_own_summary(self):
        self.run_summary()

        self.run_summary(week_start=QUIET_WEEK)

        self.assertEqual(Summary.objects.count(), 2)


class RequirementIdsTests(GenerateSummaryTestCase):
    """Requirement numbers are checked by the validator, so verified claims keep them.

    The sample fixture's project has requirements 1 and 2.
    """

    def claim_with_requirements(self, artifact_id, requirement_ids):
        return Claim(
            text="Built the task form.",
            category="feature",
            artifact_ids=[artifact_id],
            requirement_ids=requirement_ids,
            significance="minor",
        )

    def test_a_verified_claim_keeps_its_requirement_ids(self):
        self.llm.return_value = [self.claim_with_requirements(PR, [1, 2])]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "verified")
        self.assertEqual(claim.requirement_ids, [1, 2])

    def test_a_claim_tied_to_no_requirement_is_verified_with_an_empty_list(self):
        self.llm.return_value = [self.claim_with_requirements(PR, [])]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "verified")
        self.assertEqual(claim.requirement_ids, [])

    def test_a_claim_with_an_unknown_requirement_is_not_verified(self):
        self.llm.return_value = [self.claim_with_requirements(PR, [1, 99])]

        claim = self.run_summary().claims.get()

        self.assertEqual(claim.status, "couldnt_verify")
        self.assertEqual([error["check"] for error in claim.validator_errors], ["unknown_requirement"])
        self.assertEqual(claim.validator_errors[0]["requirement_id"], 99)

    def test_a_claim_that_could_not_be_verified_keeps_the_numbers_as_written(self):
        # Like its artifact_ids, so the admin shows what was wrong.
        self.llm.return_value = [self.claim_with_requirements(PR, [1, 99])]

        self.assertEqual(self.run_summary().claims.get().requirement_ids, [1, 99])

    def test_a_project_with_no_requirements_still_gets_summaries(self):
        Requirement.objects.filter(project=self.team.project).delete()
        self.llm.return_value = [self.claim_with_requirements(PR, [])]

        summary = self.run_summary()

        self.assertEqual(summary.claims.get().status, "verified")
        self.assertEqual(summary.claims.get().requirement_ids, [])


def prompts():
    import summaries.prompts as module

    return module


BODY_CAP = 2000
TITLE_CAP = 1000
MANY_ARTIFACTS = 150


class PromptSizeTests(GenerateSummaryTestCase):
    """The prompt stays a sensible size, however long a body is or many artifacts there are."""

    def test_a_long_body_is_cut_to_the_cap_and_marked(self):
        self.add_artifact("longbody1", body="A" * BODY_CAP + "Z" * 500)

        self.run_summary()

        self.assertIn("A" * BODY_CAP, self.prompt)
        self.assertNotIn("Z" * 50, self.prompt)
        self.assertIn("body truncated", self.prompt)

    def test_a_long_title_is_cut_to_the_cap_and_marked(self):
        # A commit's title is its whole message, which can be pasted output.
        self.add_artifact("longtitle1", title="T" * (TITLE_CAP + 500))

        self.run_summary()

        self.assertIn("T" * TITLE_CAP, self.prompt)
        self.assertNotIn("T" * (TITLE_CAP + 1), self.prompt)
        self.assertIn("title truncated", self.prompt)

    def test_short_titles_and_bodies_are_not_marked_as_truncated(self):
        self.run_summary()

        self.assertNotIn("body truncated", self.prompt)
        self.assertNotIn("title truncated", self.prompt)

    def test_a_week_with_many_artifacts_stays_within_the_prompt_budget(self):
        for number in range(MANY_ARTIFACTS):
            self.add_artifact(f"bulk{number:03d}", diff=f"{number:03d}" + "x" * 5000)

        self.run_summary()

        self.assertLessEqual(len(self.prompt), prompts().PROMPT_BUDGET)

    def test_when_there_are_many_artifacts_each_diff_is_shortened_but_keeps_its_start(self):
        for number in range(MANY_ARTIFACTS):
            self.add_artifact(f"bulk{number:03d}", diff=f"{number:03d}" + "x" * 5000)

        self.run_summary()

        for number in range(MANY_ARTIFACTS):
            with self.subTest(artifact=number):
                self.assertIn(f"{number:03d}" + "x" * 150, self.prompt)
        self.assertGreaterEqual(self.prompt.count("[diff truncated]"), MANY_ARTIFACTS)

    def test_a_prompt_with_no_diffs_at_all_is_built_without_trouble(self):
        # Reviews, issues and PRs have no diff, so a week can have none.
        no_diffs = Artifact.objects.filter(artifact_id__in=[PR, REVIEW, ISSUE])

        prompt = prompts().build_prompt(no_diffs)

        for artifact_id in [PR, REVIEW, ISSUE]:
            self.assertIn(artifact_id, prompt)
        self.assertNotIn("diff:", prompt)

    def test_a_prompt_with_no_artifacts_is_just_the_heading(self):
        self.assertTrue(prompts().build_prompt([]).startswith("This student's artifacts"))

    def test_a_few_artifacts_still_get_the_full_diff_cap(self):
        self.add_artifact("longdiff2", diff="A" * DIFF_CAP + "B" * 6000)

        self.run_summary()

        self.assertIn("A" * DIFF_CAP, self.prompt)


class RequirementPromptTests(GenerateSummaryTestCase):
    """The prompt lists the project's requirements, so the LLM can tie claims to them."""

    def test_the_prompt_lists_the_projects_requirements_by_number(self):
        self.run_summary()

        self.assertIn("1. Users can add a task to a shared list.", self.prompt)
        self.assertIn("2. Users can mark a task as done.", self.prompt)

    def test_requirements_are_listed_in_number_order(self):
        Requirement.objects.create(project=self.team.project, number=3, text="Third one.")

        self.run_summary()

        self.assertLess(self.prompt.index("1. Users can add"), self.prompt.index("2. Users can mark"))
        self.assertLess(self.prompt.index("2. Users can mark"), self.prompt.index("3. Third one."))

    def test_another_projects_requirements_are_not_in_the_prompt(self):
        other = Project.objects.create(course=self.team.project.course, name="Other project")
        Requirement.objects.create(project=other, number=7, text="OTHER-PROJECT-REQUIREMENT")

        self.run_summary()

        self.assertNotIn("OTHER-PROJECT-REQUIREMENT", self.prompt)

    def test_a_project_with_no_requirements_says_so(self):
        Requirement.objects.filter(project=self.team.project).delete()

        self.run_summary()

        self.assertIn("has no requirements", self.prompt)

    def test_a_project_with_requirements_does_not_say_it_has_none(self):
        self.run_summary()

        self.assertNotIn("has no requirements", self.prompt)

    def test_a_long_requirement_is_cut_to_the_cap_and_marked(self):
        Requirement.objects.create(project=self.team.project, number=3, text="R" * 1500)

        self.run_summary()

        self.assertIn("R" * 1000, self.prompt)
        self.assertNotIn("R" * 1001, self.prompt)
        self.assertIn("requirement truncated", self.prompt)

    def test_a_short_requirement_is_not_marked_as_truncated(self):
        self.run_summary()

        self.assertNotIn("requirement truncated", self.prompt)
