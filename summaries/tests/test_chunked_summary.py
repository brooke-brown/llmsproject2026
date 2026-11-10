"""Tests for T-20 (#120): long sprints are written chunk by chunk and merged.

The LLM is mocked with a function that sees each prompt, so a test can answer a chunk
prompt, a retry prompt and the merge prompt differently. Sprint 1 here is 14 days, Fri
Oct 23 to Thu Nov 5, so it has two chunks: Oct 23-29 and Oct 30-Nov 5.
"""

from datetime import date
from unittest import mock

from summaries.llm import LLMError
from summaries.models import Summary
from summaries.schemas import Claim
from summaries.tests.sprint_helpers import SprintTestCase, at, make_claim


def generate_sprint_summary(*args, **kwargs):
    from summaries.services import generate_sprint_summary

    return generate_sprint_summary(*args, **kwargs)


class ChunkedSummaryTestCase(SprintTestCase):
    def setUp(self):
        super().setUp()
        self.sprint.end_date = date(2026, 11, 5)
        self.sprint.save()
        self.add_requirement(self.sprint, 1, "Users can log in")
        self.early = self.add_commit("aa1", at(10, 25))  # Chunk 1.
        self.late = self.add_commit("aa2", at(11, 2))  # Chunk 2.
        self.calls = []
        self.answers = {}
        self.merge_answer = None  # None means: return every claim it was given, joined as one.
        for patcher in (
            mock.patch("summaries.services.generate_claims", side_effect=self.fake_llm),
            mock.patch("django.utils.timezone.now", return_value=at(11, 10)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_llm(self, prompt, *, system="", client=None):
        kind = "merge" if "merge" in system.lower() else "retry" if "failed validation" in prompt else "chunk"
        self.calls.append((kind, prompt))
        if kind == "merge":
            if isinstance(self.merge_answer, Exception):
                raise self.merge_answer
            return self.merge_answer if self.merge_answer is not None else []
        # Chunk and retry prompts are answered by which chunk's dates the prompt names.
        key = "first" if "2026-10-23 to 2026-10-29" in prompt else "second"
        answer = self.answers[key]
        return answer.pop(0) if isinstance(answer, list) and answer and isinstance(answer[0], list) else answer

    def kinds(self):
        return [kind for kind, _ in self.calls]

    def generate(self):
        return generate_sprint_summary(self.student, self.team, self.sprint)


class ChunkedGenerationTests(ChunkedSummaryTestCase):
    def test_each_chunk_gets_its_own_dates_and_only_its_own_artifacts(self):
        self.answers = {"first": [make_claim(self.early)], "second": [make_claim(self.late)]}
        self.merge_answer = [make_claim(self.early, self.late)]
        self.generate()
        first, second = (prompt for kind, prompt in self.calls if kind == "chunk")
        self.assertIn("2026-10-23 to 2026-10-29", first)
        self.assertIn(self.early, first)
        self.assertNotIn(self.late, first)
        self.assertIn("2026-10-30 to 2026-11-05", second)
        self.assertIn(self.late, second)
        self.assertNotIn(self.early, second)

    def test_several_chunks_run_a_merge_pass_and_save_one_summary(self):
        self.answers = {"first": [make_claim(self.early)], "second": [make_claim(self.late)]}
        self.merge_answer = [make_claim(self.early, self.late)]
        summary = self.generate()
        self.assertEqual(self.kinds(), ["chunk", "chunk", "merge"])
        self.assertEqual(Summary.objects.count(), 1)
        claim = summary.claims.get()
        self.assertEqual(claim.status, "verified")
        self.assertEqual(sorted(claim.artifact_ids), sorted([self.early, self.late]))

    def test_the_merge_prompt_holds_the_validated_claims_and_the_sprints_requirements(self):
        self.answers = {
            "first": [make_claim(self.early, requirement_ids=[1])],
            "second": [make_claim(self.late)],
        }
        self.merge_answer = [make_claim(self.early, self.late)]
        self.generate()
        prompt = next(prompt for kind, prompt in self.calls if kind == "merge")
        self.assertIn(self.early, prompt)
        self.assertIn(self.late, prompt)
        self.assertIn("Users can log in", prompt)
        self.assertIn("2026-10-23", prompt)
        self.assertIn("2026-11-05", prompt)

    def test_one_chunk_has_no_merge_pass(self):
        self.sprint.end_date = date(2026, 10, 29)
        self.sprint.save()
        self.answers = {"first": [make_claim(self.early)]}
        self.generate()
        self.assertEqual(self.kinds(), ["chunk"])

    def test_a_chunk_with_no_artifacts_makes_no_call_and_one_claim_needs_no_merge(self):
        self.answers = {"second": [make_claim(self.late)]}
        # Only the second chunk has work, so remove the early artifact.
        from ingest.models import Artifact

        Artifact.objects.filter(artifact_id=self.early).delete()
        summary = self.generate()
        self.assertEqual(self.kinds(), ["chunk"])
        self.assertEqual(summary.claims.get().artifact_ids, [self.late])

    def test_a_claim_is_validated_against_its_own_chunk(self):
        # The first chunk cites the second chunk's artifact, which is real but out of its dates.
        self.answers = {
            "first": [make_claim(self.late)],
            "second": [make_claim(self.late)],
        }
        self.merge_answer = []
        summary = self.generate()
        self.assertEqual(self.kinds().count("retry"), 2)  # The existing two retries.
        dropped = summary.claims.filter(status="couldnt_verify").get()
        self.assertEqual(dropped.validator_errors[0]["check"], "outside_week")

    def test_a_failing_chunk_claim_is_retried_and_can_pass(self):
        self.answers = {
            "first": [[make_claim(self.late)], [make_claim(self.early)]],  # Bad, then fixed.
            "second": [make_claim(self.late)],
        }
        self.merge_answer = [make_claim(self.early, self.late)]
        summary = self.generate()
        self.assertEqual(self.kinds().count("retry"), 1)
        self.assertEqual(summary.unverified_count, 0)


class MergeSafetyTests(ChunkedSummaryTestCase):
    def setUp(self):
        super().setUp()
        self.answers = {
            "first": [make_claim(self.early, requirement_ids=[1])],
            "second": [make_claim(self.late)],
        }

    def test_a_merged_claim_cannot_cite_an_artifact_it_was_not_given(self):
        stranger = self.add_commit("zz9", at(10, 26))  # Real, in range, the student's, but unused.
        self.merge_answer = [make_claim(self.early, self.late, stranger)]
        summary = self.generate()
        dropped = summary.claims.filter(status="couldnt_verify").get()
        self.assertEqual(
            [error["check"] for error in dropped.validator_errors if error["artifact_id"] == stranger],
            ["not_in_source"],
        )

    def test_a_dropped_merged_claim_adds_the_original_claims_back(self):
        stranger = self.add_commit("zz9", at(10, 26))
        self.merge_answer = [make_claim(self.early, self.late, stranger)]
        summary = self.generate()
        verified = summary.claims.filter(status="verified")
        self.assertEqual(sorted(sum((c.artifact_ids for c in verified), [])), sorted([self.early, self.late]))

    def test_a_merge_that_removes_a_claim_cannot_remove_its_credit(self):
        self.merge_answer = [make_claim(self.early)]  # Silently loses the late claim.
        summary = self.generate()
        verified = summary.claims.filter(status="verified")
        self.assertEqual(sorted(sum((c.artifact_ids for c in verified), [])), sorted([self.early, self.late]))
        added_back = verified.get(artifact_ids=[self.late])
        self.assertEqual(added_back.text, "Added the login form.")

    def test_an_added_back_claim_keeps_its_requirements_and_significance(self):
        self.merge_answer = [make_claim(self.late)]
        summary = self.generate()
        original = summary.claims.get(artifact_ids=[self.early])
        self.assertEqual(original.requirement_ids, [1])
        self.assertEqual(original.significance, "minor")

    def test_the_merged_result_is_validated_against_the_sprints_requirements(self):
        self.merge_answer = [make_claim(self.early, self.late, requirement_ids=[7])]
        summary = self.generate()
        dropped = summary.claims.filter(status="couldnt_verify").get()
        self.assertEqual(dropped.validator_errors[0]["check"], "unknown_requirement")

    def test_when_the_merge_call_fails_nothing_is_saved(self):
        self.merge_answer = LLMError("down")
        with self.assertRaises(LLMError):
            self.generate()
        self.assertFalse(Summary.objects.exists())

    def test_a_failed_regeneration_leaves_the_earlier_summary_alone(self):
        self.merge_answer = [make_claim(self.early, self.late)]
        first = self.generate()
        self.answers["second"] = [make_claim(self.late)]
        self.merge_answer = LLMError("down")
        with self.assertRaises(LLMError):
            self.generate()
        self.assertEqual(first.claims.count(), 1)


class ChunkedLoggingTests(ChunkedSummaryTestCase):
    def test_unverified_count_includes_chunk_drops_and_merge_drops(self):
        stranger = self.add_commit("zz9", at(10, 26))
        self.answers = {
            "first": [make_claim(self.early), make_claim(self.late)],  # Second one can't pass.
            "second": [make_claim(self.late)],
        }
        self.merge_answer = [make_claim(self.early, self.late, stranger)]  # Dropped.
        summary = self.generate()
        self.assertEqual(summary.unverified_count, 2)

    def test_one_validation_run_has_totals_across_chunks_and_the_merge(self):
        self.answers = {
            "first": [[make_claim(self.late)], [make_claim(self.early)]],  # One retry to pass.
            "second": [make_claim(self.late)],
        }
        self.merge_answer = [make_claim(self.early, self.late)]
        summary = self.generate()
        run = summary.validation_runs.get()
        self.assertEqual(run.claims_total, 3)  # Two chunk claims and one merged claim.
        self.assertEqual(run.claims_first_pass, 2)  # The second chunk's and the merged claim.
        self.assertEqual(run.claims_dropped, 0)
        self.assertEqual(run.retries, 1)
