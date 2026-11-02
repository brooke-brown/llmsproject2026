"""Tests for T-9 (#40): logging each validation run.

Every successful generate_summary call saves one ValidationRun beside its summary, in the
same transaction, so the log never says something the database doesn't have. A call that
fails saves no summary, so it has nothing to attach a run to and logs nothing.

The log holds counts and rates only, never student text or code. Its rates are
properties of the model, so the tests check the saved counts and the rates they give.

The LLM is a mock, as in the retry-loop tests, whose helpers and fixtures these reuse.
"""

from datetime import date
from unittest import mock

from django.db import models

from summaries.llm import LLMError
from summaries.models import Claim as ClaimRow
from summaries.models import Summary, ValidationRun
from summaries.tests.test_generate_summary import COMMIT, QUIET_WEEK, REVIEW
from summaries.tests.test_retry_loop import (
    FIXED,
    GOOD,
    GOOD_2,
    RetryTestCase,
    bad,
    good,
)


class RunTestCase(RetryTestCase):
    def only_run(self):
        self.assertEqual(ValidationRun.objects.count(), 1)
        return ValidationRun.objects.get()


class SavedRunTests(RunTestCase):
    def test_a_summary_call_saves_one_run_linked_to_its_summary(self):
        self.replies([good()])

        summary = self.run_summary()

        self.assertEqual(self.only_run().summary, summary)
        self.assertEqual(list(summary.validation_runs.all()), [self.only_run()])

    def test_the_run_records_when_it_happened(self):
        self.replies([good()])

        self.run_summary()

        self.assertIsNotNone(self.only_run().created_at)

    def test_the_run_counts_the_claims_that_were_saved(self):
        self.replies([good(), good(GOOD_2, COMMIT), bad()], [bad("Try 2.")], [bad("Try 3.")])

        summary = self.run_summary()

        self.assertEqual(self.only_run().claims_total, summary.claims.count())
        self.assertEqual(self.only_run().claims_total, 3)


class CleanRunTests(RunTestCase):
    def test_a_clean_run_needed_no_retries_and_dropped_nothing(self):
        self.replies([good(), good(GOOD_2, COMMIT), good("Reviewed it.", REVIEW)])

        self.run_summary()

        run = self.only_run()
        self.assertEqual(
            (run.claims_total, run.claims_first_pass, run.claims_dropped, run.retries),
            (3, 3, 0, 0),
        )
        self.assertEqual(run.first_pass_rate, 1)
        self.assertEqual(run.drop_rate, 0)


class RetryRunTests(RunTestCase):
    def test_a_run_that_needed_a_retry_records_it(self):
        self.replies([good(), good(GOOD_2, COMMIT), bad()], [good(FIXED, REVIEW)])

        self.run_summary()

        run = self.only_run()
        self.assertEqual(
            (run.claims_total, run.claims_first_pass, run.claims_dropped, run.retries),
            (3, 2, 0, 1),
        )
        self.assertAlmostEqual(run.first_pass_rate, 2 / 3)
        self.assertEqual(run.drop_rate, 0)

    def test_a_retry_that_failed_still_counts_as_a_retry(self):
        self.replies([bad()], LLMError("The LLM request timed out."), [good(FIXED, COMMIT)])

        self.run_summary()

        self.assertEqual(self.only_run().retries, 2)


class DroppedClaimRunTests(RunTestCase):
    def test_a_run_that_drops_a_claim_records_it(self):
        self.replies([good(), bad()], [bad("Try 2.")], [bad("Try 3.")])

        self.run_summary()

        run = self.only_run()
        self.assertEqual(
            (run.claims_total, run.claims_first_pass, run.claims_dropped, run.retries),
            (2, 1, 1, 2),
        )
        self.assertEqual(run.first_pass_rate, 0.5)
        self.assertEqual(run.drop_rate, 0.5)

    def test_the_dropped_count_matches_the_summarys_unverified_count(self):
        self.replies([bad(), bad("Other.", GOOD_2)], [bad("Try 2."), bad("Try 2b.")], [bad("Try 3."), bad("Try 3b.")])

        summary = self.run_summary()

        self.assertEqual(self.only_run().claims_dropped, summary.unverified_count)


class NoClaimsRunTests(RunTestCase):
    def test_a_run_where_the_llm_made_no_claims_has_zero_counts_and_zero_rates(self):
        self.replies([])

        self.run_summary()

        run = self.only_run()
        self.assertEqual(
            (run.claims_total, run.claims_first_pass, run.claims_dropped, run.retries),
            (0, 0, 0, 0),
        )
        self.assertEqual((run.first_pass_rate, run.drop_rate), (0, 0))

    def test_a_week_with_no_activity_still_logs_a_run_of_zeros(self):
        self.run_summary(week_start=QUIET_WEEK)

        self.llm.assert_not_called()
        run = self.only_run()
        self.assertEqual(
            (run.claims_total, run.claims_first_pass, run.claims_dropped, run.retries),
            (0, 0, 0, 0),
        )
        self.assertEqual((run.first_pass_rate, run.drop_rate), (0, 0))


class RegenerationRunTests(RunTestCase):
    def test_regenerating_adds_a_run_and_keeps_the_earlier_one(self):
        self.replies([good()])
        summary = self.run_summary()
        first_run = ValidationRun.objects.get()

        self.replies([good(), bad()], [good(FIXED, COMMIT)])
        self.run_summary()

        self.assertEqual(summary.validation_runs.count(), 2)
        first_run.refresh_from_db()
        self.assertEqual((first_run.claims_total, first_run.retries), (1, 0))

    def test_each_run_records_its_own_numbers(self):
        self.replies([good()])
        self.run_summary()
        self.replies([good(), bad()], [good(FIXED, COMMIT)])
        self.run_summary()

        first, second = ValidationRun.objects.order_by("pk")

        self.assertEqual((first.claims_total, first.claims_first_pass, first.retries), (1, 1, 0))
        self.assertEqual((second.claims_total, second.claims_first_pass, second.retries), (2, 1, 1))


class NoRunTests(RunTestCase):
    def test_a_failed_first_attempt_logs_no_run(self):
        self.replies(LLMError("The LLM request timed out."))

        with self.assertRaises(LLMError):
            self.run_summary()

        self.assertEqual(ValidationRun.objects.count(), 0)

    def test_a_non_monday_logs_no_run(self):
        with self.assertRaises(ValueError):
            self.run_summary(week_start=date(2026, 10, 28))

        self.assertEqual(ValidationRun.objects.count(), 0)

    def test_a_student_not_on_the_team_logs_no_run(self):
        with self.assertRaises(ValueError):
            self.run_summary(user=self.student_c)

        self.assertEqual(ValidationRun.objects.count(), 0)

    def test_a_failed_regeneration_leaves_the_earlier_run_as_the_only_one(self):
        self.replies([good()])
        self.run_summary()
        self.replies(LLMError("The LLM rate limit was hit."))

        with self.assertRaises(LLMError):
            self.run_summary()

        self.assertEqual(ValidationRun.objects.count(), 1)


class AtomicTests(RunTestCase):
    def test_if_the_run_cannot_be_saved_the_summary_and_claims_are_rolled_back(self):
        self.replies([good(), good(GOOD_2, COMMIT)])

        with mock.patch("summaries.services.ValidationRun.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaisesMessage(RuntimeError, "boom"):
                self.run_summary()

        self.assertEqual(Summary.objects.count(), 0)
        self.assertEqual(ClaimRow.objects.count(), 0)
        self.assertEqual(ValidationRun.objects.count(), 0)

    def test_a_failed_save_on_regeneration_leaves_the_earlier_summary_as_it_was(self):
        self.replies([good(GOOD)])
        first = self.run_summary()
        self.replies([good("Replacement.", COMMIT)])

        with mock.patch("summaries.services.ValidationRun.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_summary()

        self.assertEqual([claim.text for claim in first.claims.all()], [GOOD])
        self.assertEqual(ValidationRun.objects.count(), 1)


class StudentWorkStaysOutOfTheLogTests(RunTestCase):
    def test_the_log_has_only_counts_and_dates_never_text(self):
        # Student work is text, so a text column here would be a way for it to leak.
        text_types = (models.TextField, models.CharField, models.JSONField)
        for field in ValidationRun._meta.concrete_fields:
            with self.subTest(field=field.name):
                self.assertNotIsInstance(field, text_types)

    def test_the_saved_run_contains_nothing_from_the_prompt_or_the_claims(self):
        self.replies([good("PRIVATE-CLAIM-TEXT-4471")])

        self.run_summary()

        values = [str(value) for value in ValidationRun.objects.values().get().values()]
        self.assertFalse(any("PRIVATE-CLAIM-TEXT-4471" in value for value in values))
