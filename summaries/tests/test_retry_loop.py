"""Tests for US-13 (#38): retrying the claims that fail validation.

generate_summary asks the LLM for claims, validates them, and sends only the failing ones
back for up to 2 retries (3 attempts in all). A claim that still fails afterwards is
saved as couldn't verify. A retry that errors or comes back malformed counts as a failed
attempt, not a crash.

The LLM is a mock whose side_effect lists each attempt's reply in turn, so a reply can
be a list of claims or an LLMError to raise. Nothing here makes a network call. The
fixtures and helpers are the ones the generate_summary tests use.
"""

from summaries.llm import LLMError
from summaries.models import Summary
from summaries.tests.test_generate_summary import (
    BEFORE_WEEK,
    COMMIT,
    PR,
    REVIEW,
    STUDENT_B_PR,
    TEAM_B_COMMIT,
    WEEK_START,
    GenerateSummaryTestCase,
    make_claim,
)

# Claims whose text is unique, so a test can tell which ones a prompt mentions.
GOOD = "Built the task form."
GOOD_2 = "Reviewed the done button."
BAD = "Wrote the billing module."
BAD_2 = "Redesigned the settings page."
FIXED = "Corrected: added the task model."
FIXED_2 = "Corrected: saved tasks to the database."


def good(text=GOOD, artifact=PR):
    return make_claim(artifact, text=text)


def bad(text=BAD, artifact=STUDENT_B_PR):
    """Cites another student's PR, so it fails with not_contributor."""
    return make_claim(artifact, text=text)


class RetryTestCase(GenerateSummaryTestCase):
    def replies(self, *replies):
        """What the LLM returns on each call, in order."""
        self.llm.side_effect = list(replies)

    @property
    def calls(self):
        return self.llm.call_args_list

    def prompt_of(self, call_number):
        return self.calls[call_number].args[0]

    def saved(self):
        return list(Summary.objects.get().claims.order_by("pk"))


class NoRetryTests(RetryTestCase):
    def test_no_retry_when_every_claim_passes(self):
        self.replies([good(), good(GOOD_2, COMMIT)])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 1)

    def test_no_retry_when_the_llm_makes_no_claims(self):
        self.replies([])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 1)

    def test_an_error_on_the_first_attempt_still_raises_and_saves_nothing(self):
        self.replies(LLMError("The LLM request timed out."))

        with self.assertRaisesMessage(LLMError, "timed out"):
            self.run_summary()

        self.assertEqual(self.llm.call_count, 1)
        self.assertEqual(Summary.objects.count(), 0)


class RetryPromptTests(RetryTestCase):
    def setUp(self):
        super().setUp()
        self.replies([good(), bad()], [good(FIXED, COMMIT)])
        self.run_summary()

    def test_only_the_failing_claim_goes_back(self):
        self.assertEqual(self.llm.call_count, 2)
        self.assertIn(BAD, self.prompt_of(1))
        self.assertNotIn(GOOD, self.prompt_of(1))

    def test_the_failing_claims_citation_goes_back(self):
        self.assertIn(STUDENT_B_PR, self.prompt_of(1))

    def test_the_validators_errors_go_back(self):
        self.assertIn("not_contributor", self.prompt_of(1))
        self.assertIn("isn't the author or a co-author", self.prompt_of(1))

    def test_the_artifact_list_goes_back_so_the_llm_still_has_the_evidence(self):
        for artifact_id in [COMMIT, PR, REVIEW]:
            with self.subTest(artifact_id=artifact_id):
                self.assertIn(artifact_id, self.prompt_of(1))
        self.assertIn("Add the task model", self.prompt_of(1))  # A commit's title.

    def test_the_prompt_asks_for_one_corrected_claim_per_failing_claim_in_order(self):
        self.assertIn("exactly one", self.prompt_of(1))
        self.assertIn("same order", self.prompt_of(1))

    def test_the_retry_uses_the_same_system_prompt_as_the_first_attempt(self):
        self.assertEqual(self.calls[1].kwargs["system"], self.calls[0].kwargs["system"])

    def test_failing_claims_are_numbered(self):
        self.llm.reset_mock()
        self.replies([bad(BAD, STUDENT_B_PR), good(), bad(BAD_2, TEAM_B_COMMIT)], [good(FIXED), good(FIXED_2)])
        Summary.objects.all().delete()

        self.run_summary()

        self.assertIn("Claim 1", self.prompt_of(1))
        self.assertIn("Claim 2", self.prompt_of(1))
        self.assertNotIn("Claim 3", self.prompt_of(1))


class ReplacementTests(RetryTestCase):
    def test_a_fixed_claim_replaces_the_failing_one_in_the_same_position(self):
        self.replies([good(), bad(), good(GOOD_2, COMMIT)], [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertEqual([claim.text for claim in self.saved()], [GOOD, FIXED, GOOD_2])
        self.assertEqual({claim.status for claim in self.saved()}, {"verified"})

    def test_claims_that_passed_are_kept_unchanged(self):
        first, last = good(GOOD, PR), good(GOOD_2, COMMIT)
        self.replies([first, bad(), last], [good(FIXED, REVIEW)])

        self.run_summary()

        saved = self.saved()
        self.assertEqual((saved[0].text, saved[0].artifact_ids), (first.text, first.artifact_ids))
        self.assertEqual((saved[2].text, saved[2].artifact_ids), (last.text, last.artifact_ids))

    def test_a_fixed_claim_is_linked_to_the_artifacts_it_now_cites(self):
        self.replies([bad()], [good(FIXED, COMMIT)])

        self.run_summary()

        fixed = self.saved()[0]
        self.assertEqual([artifact.artifact_id for artifact in fixed.artifacts.all()], [COMMIT])

    def test_several_failing_claims_are_replaced_by_position(self):
        self.replies(
            [bad(BAD, STUDENT_B_PR), good(), bad(BAD_2, TEAM_B_COMMIT)],
            [good(FIXED, COMMIT), good(FIXED_2, REVIEW)],
        )

        self.run_summary()

        self.assertEqual([claim.text for claim in self.saved()], [FIXED, GOOD, FIXED_2])


class AttemptLimitTests(RetryTestCase):
    def test_a_claim_fixed_on_the_first_retry_takes_two_calls(self):
        self.replies([bad()], [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 2)

    def test_a_claim_fixed_on_the_second_retry_takes_three_calls(self):
        self.replies([bad()], [bad("Still wrong.")], [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertEqual(self.saved()[0].status, "verified")
        self.assertEqual(self.saved()[0].text, FIXED)

    def test_there_are_at_most_two_retries(self):
        # An LLM that never fixes anything: one attempt, then two retries, then stop.
        self.replies([bad()], [bad("Try 2.")], [bad("Try 3.")], [bad("Try 4.")])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 3)

    def test_a_claim_that_still_fails_after_the_last_attempt_is_dropped_and_counted(self):
        self.replies([good(), bad()], [bad("Try 2.")], [bad("Try 3.")])

        summary = self.run_summary()

        self.assertEqual(summary.claims.filter(status="verified").count(), 1)
        self.assertEqual(summary.claims.filter(status="couldnt_verify").count(), 1)
        self.assertEqual(summary.unverified_count, 1)

    def test_a_later_retry_sends_only_the_claims_that_still_fail(self):
        self.replies(
            [bad(BAD, STUDENT_B_PR), bad(BAD_2, TEAM_B_COMMIT)],
            [good(FIXED, COMMIT), bad("Second try for two.", BEFORE_WEEK)],
            [good(FIXED_2, REVIEW)],
        )

        self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertIn("Second try for two.", self.prompt_of(2))
        self.assertNotIn(FIXED, self.prompt_of(2))
        self.assertNotIn("Claim 2", self.prompt_of(2))  # Only one claim is left to fix.


class FailedAttemptTests(RetryTestCase):
    def test_a_retry_that_raises_counts_as_a_failed_attempt_and_the_next_one_still_happens(self):
        self.replies([bad()], LLMError("The LLM request timed out."), [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertEqual(self.saved()[0].text, FIXED)
        self.assertEqual(self.saved()[0].status, "verified")

    def test_retries_that_keep_failing_leave_the_claim_dropped_not_the_summary_lost(self):
        self.replies(
            [good(), bad()],
            LLMError("The LLM rate limit was hit."),
            LLMError("The LLM's reply didn't match the claim schema."),
        )

        summary = self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertEqual(Summary.objects.get(), summary)
        self.assertEqual([claim.status for claim in self.saved()], ["verified", "couldnt_verify"])
        self.assertEqual(self.saved()[1].text, BAD)  # Unchanged: no retry reply ever arrived.

    def test_a_reply_with_too_few_claims_counts_as_a_failed_attempt(self):
        self.replies(
            [bad(BAD, STUDENT_B_PR), bad(BAD_2, TEAM_B_COMMIT)],
            [good(FIXED, COMMIT)],  # Two claims failed, one came back.
            [good(FIXED, COMMIT), good(FIXED_2, REVIEW)],
        )

        self.run_summary()

        # The short reply is ignored, so the next attempt resends both failing claims.
        self.assertEqual(self.llm.call_count, 3)
        self.assertIn(BAD, self.prompt_of(2))
        self.assertIn(BAD_2, self.prompt_of(2))
        self.assertEqual([claim.text for claim in self.saved()], [FIXED, FIXED_2])

    def test_a_reply_with_too_many_claims_counts_as_a_failed_attempt(self):
        self.replies(
            [bad()],
            [good(FIXED, COMMIT), good(FIXED_2, REVIEW)],  # One claim failed, two came back.
            [good(FIXED, COMMIT)],
        )

        self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertEqual([claim.text for claim in self.saved()], [FIXED])

    def test_a_reply_that_is_too_short_changes_nothing_when_it_is_the_last_attempt(self):
        self.replies([bad(BAD, STUDENT_B_PR), bad(BAD_2, TEAM_B_COMMIT)], [], [])

        summary = self.run_summary()

        self.assertEqual(summary.unverified_count, 2)
        self.assertEqual([claim.text for claim in self.saved()], [BAD, BAD_2])


class DroppedClaimTests(RetryTestCase):
    def test_a_dropped_claim_is_saved_in_its_latest_form_with_its_latest_errors(self):
        self.replies(
            [bad("Version 1.", STUDENT_B_PR)],
            [bad("Version 2.", TEAM_B_COMMIT)],  # artifact_not_found
            [bad("Version 3.", BEFORE_WEEK)],  # outside_week
        )

        self.run_summary()

        claim = self.saved()[0]
        self.assertEqual(claim.status, "couldnt_verify")
        self.assertEqual(claim.text, "Version 3.")
        self.assertEqual(claim.artifact_ids, [BEFORE_WEEK])
        self.assertEqual([error["check"] for error in claim.validator_errors], ["outside_week"])

    def test_a_dropped_claims_errors_carry_its_position_in_the_summary(self):
        self.replies([good(), bad()], [bad("Try 2.")], [bad("Try 3.")])

        self.run_summary()

        dropped = self.saved()[1]
        self.assertEqual({error["claim_index"] for error in dropped.validator_errors}, {1})

    def test_a_dropped_claim_is_not_linked_to_any_artifact(self):
        self.replies([bad()], [bad("Try 2.")], [bad("Try 3.")])

        self.run_summary()

        self.assertEqual(self.saved()[0].artifacts.count(), 0)


class AttemptStatsTests(RetryTestCase):
    """The loop reports what T-9 will log: retries used and claims valid on the first try."""

    def generate(self):
        from ingest.services import student_week_artifacts
        from summaries.services import generate_validated_claims

        artifacts = student_week_artifacts(self.student, self.team, WEEK_START)
        return generate_validated_claims(self.student, self.team, WEEK_START, artifacts)

    def test_a_clean_run_used_no_retries_and_every_claim_was_valid_first_time(self):
        self.replies([good(), good(GOOD_2, COMMIT)])

        result = self.generate()

        self.assertEqual(result.retries, 0)
        self.assertEqual(result.first_pass, 2)

    def test_a_run_that_needed_one_retry(self):
        self.replies([good(), good(GOOD_2, COMMIT), bad()], [good(FIXED, REVIEW)])

        result = self.generate()

        self.assertEqual(result.retries, 1)
        self.assertEqual(result.first_pass, 2)  # Not 3: the third claim needed the retry.

    def test_a_run_that_never_fixed_its_claim_used_both_retries(self):
        self.replies([bad()], [bad("Try 2.")], [bad("Try 3.")])

        result = self.generate()

        self.assertEqual(result.retries, 2)
        self.assertEqual(result.first_pass, 0)

    def test_a_run_with_no_claims_used_no_retries(self):
        self.replies([])

        result = self.generate()

        self.assertEqual((result.retries, result.first_pass), (0, 0))

    def test_the_result_holds_the_final_claims_and_their_results_in_order(self):
        self.replies([good(), bad()], [good(FIXED, COMMIT)])

        result = self.generate()

        self.assertEqual([claim.text for claim in result.claims], [GOOD, FIXED])
        self.assertEqual([check.ok for check in result.results], [True, True])
        self.assertEqual([check.claim_index for check in result.results], [0, 1])

    def test_a_failed_retry_still_counts_as_a_retry_used(self):
        self.replies([bad()], LLMError("The LLM request timed out."), [good(FIXED, COMMIT)])

        result = self.generate()

        self.assertEqual(result.retries, 2)


class RetryPromptSizeTests(RetryTestCase):
    """A retry prompt repeats the artifact list, so it's held to the same size budget."""

    def test_a_retry_prompt_for_a_week_with_many_artifacts_stays_near_the_budget(self):
        import summaries.prompts as prompts
        from ingest.services import student_week_artifacts

        for number in range(150):
            self.add_artifact(f"bulk{number:03d}", diff=f"{number:03d}" + "x" * 5000)
        artifacts = student_week_artifacts(self.student, self.team, WEEK_START)
        failing = [(bad(), [])]

        prompt = prompts.build_retry_prompt(artifacts, failing)

        # The failing claims are short, so they add little beyond the artifact list.
        self.assertLessEqual(len(prompt), prompts.PROMPT_BUDGET + 2000)


class RequirementRetryTests(RetryTestCase):
    """An unknown requirement is an ordinary failure, so the loop already retries it."""

    def with_requirements(self, requirement_ids, text="Built the task form.", artifact=PR):
        from summaries.schemas import Claim

        return Claim(
            text=text,
            category="feature",
            artifact_ids=[artifact],
            requirement_ids=requirement_ids,
            significance="minor",
        )

    def test_the_retry_prompt_lists_the_projects_requirements(self):
        self.replies([bad()], [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertIn("1. Users can add a task to a shared list.", self.prompt_of(1))

    def test_an_unknown_requirement_and_its_error_go_back_to_the_llm(self):
        self.replies([self.with_requirements([99])], [self.with_requirements([1], FIXED)])

        self.run_summary()

        self.assertEqual(self.llm.call_count, 2)
        self.assertIn("unknown_requirement", self.prompt_of(1))
        self.assertIn("99", self.prompt_of(1))

    def test_a_claim_fixed_on_retry_is_saved_verified_with_its_requirement_numbers(self):
        self.replies([self.with_requirements([99])], [self.with_requirements([1, 2], FIXED)])

        self.run_summary()

        claim = self.saved()[0]
        self.assertEqual((claim.status, claim.text, claim.requirement_ids), ("verified", FIXED, [1, 2]))

    def test_a_claim_that_keeps_citing_an_unknown_requirement_is_dropped(self):
        self.replies(
            [self.with_requirements([99])],
            [self.with_requirements([98], "Try 2.")],
            [self.with_requirements([97], "Try 3.")],
        )

        summary = self.run_summary()

        self.assertEqual(self.llm.call_count, 3)
        self.assertEqual(summary.unverified_count, 1)
        self.assertEqual(self.saved()[0].validator_errors[0]["requirement_id"], 97)
