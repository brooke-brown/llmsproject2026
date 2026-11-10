"""Tests for T-19 (#119): the validator's date check for a sprint's covered range."""

from datetime import date

from summaries.tests.sprint_helpers import SprintTestCase, at, make_claim


def validate_sprint_claims(*args, **kwargs):
    from summaries.validator import validate_sprint_claims

    return validate_sprint_claims(*args, **kwargs)


class SprintValidatorTests(SprintTestCase):
    def check(self, artifact_id, last_day=date(2026, 10, 29)):
        (result,) = validate_sprint_claims(
            [make_claim(artifact_id)], self.student, self.team, self.sprint, last_day
        )
        return result

    def test_a_sprint_need_not_start_on_a_monday(self):
        self.assertTrue(self.check(self.add_commit("aa1", at(10, 23, 0, 30))).ok)

    def test_the_last_day_is_included_until_midnight_in_new_york(self):
        self.assertTrue(self.check(self.add_commit("aa1", at(10, 29, 23, 30))).ok)

    def test_a_day_either_side_of_the_range_fails_with_outside_week(self):
        for suffix, when in (("aa1", at(10, 22, 23, 30)), ("aa2", at(10, 30, 0, 30))):
            result = self.check(self.add_commit(suffix, when))
            self.assertEqual([error.check for error in result.errors], ["outside_week"])
            self.assertIn("2026-10-23", result.errors[0].message)

    def test_the_covered_range_can_end_before_the_sprint_does(self):
        later = self.add_commit("aa1", at(10, 27))
        self.assertFalse(self.check(later, last_day=date(2026, 10, 25)).ok)

    def test_the_students_authorship_is_still_checked(self):
        result = self.check(self.add_commit("aa1", at(10, 25), author="bo-dev"))
        self.assertEqual([error.check for error in result.errors], ["not_contributor"])
