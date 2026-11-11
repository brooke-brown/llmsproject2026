"""Tests for T-21 (#125): one reporting model, so the weekly summary code is gone."""

from datetime import date

from summaries.tests.sprint_helpers import SprintTestCase, at, make_claim


class WeeklyCodeIsGoneTests(SprintTestCase):
    def test_the_weekly_generators_are_removed(self):
        from summaries import services

        self.assertFalse(hasattr(services, "generate_summary"))
        self.assertFalse(hasattr(services, "generate_validated_claims"))
        self.assertTrue(callable(services.generate_sprint_summary))

    def test_the_weekly_validator_is_removed_and_the_sprint_one_stays(self):
        from summaries import validator

        self.assertFalse(hasattr(validator, "validate_claims"))
        self.assertFalse(hasattr(validator, "MONDAY"))
        self.assertTrue(callable(validator.validate_sprint_claims))

    def test_there_is_one_system_prompt_and_it_is_about_a_sprint(self):
        from summaries import prompts

        self.assertIn("sprint", prompts.SYSTEM_PROMPT.lower())
        self.assertNotIn("weekly", prompts.SYSTEM_PROMPT.lower())
        self.assertFalse(hasattr(prompts, "SPRINT_SYSTEM_PROMPT"))

    def test_a_prompt_has_to_say_which_period_it_covers(self):
        from summaries.prompts import build_prompt

        with self.assertRaises(TypeError):
            build_prompt([], [])


class OutsideDatesTests(SprintTestCase):
    def test_an_artifact_outside_the_range_fails_with_outside_dates(self):
        from summaries.validator import validate_sprint_claims

        late = self.add_commit("aa1", at(10, 30, 0, 30))
        (result,) = validate_sprint_claims(
            [make_claim(late)], self.student, self.team, self.sprint, date(2026, 10, 29)
        )
        self.assertEqual([error.check for error in result.errors], ["outside_dates"])
        self.assertIn("2026-10-23", result.errors[0].message)
        self.assertNotIn("week", result.errors[0].message.lower())

    def test_the_old_code_name_is_gone(self):
        from summaries import validator

        self.assertEqual(validator.OUTSIDE_DATES, "outside_dates")
        self.assertFalse(hasattr(validator, "OUTSIDE_WEEK"))
