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

    def test_the_date_error_names_the_sprint_not_a_reporting_week(self):
        result = self.check(self.add_commit("aa1", at(10, 30, 0, 30)))
        message = result.errors[0].message
        self.assertIn("outside Sprint 1 (2026-10-23 to 2026-10-29)", message)
        self.assertNotIn("week", message)

    def test_the_covered_range_can_end_before_the_sprint_does(self):
        later = self.add_commit("aa1", at(10, 27))
        self.assertFalse(self.check(later, last_day=date(2026, 10, 25)).ok)

    def test_the_students_authorship_is_still_checked(self):
        result = self.check(self.add_commit("aa1", at(10, 25), author="bo-dev"))
        self.assertEqual([error.check for error in result.errors], ["not_contributor"])


class SourceClaimTests(SprintTestCase):
    """T-20 (#120): a merged claim may cite only artifacts from the claims it was given."""

    def run_check(self, cited, allowed, **kwargs):
        from summaries.validator import validate_sprint_claims

        return validate_sprint_claims(
            [make_claim(*cited)], self.student, self.team, self.sprint, date(2026, 10, 29),
            allowed_artifact_ids=allowed, **kwargs,
        )[0]

    def test_a_citation_outside_the_given_claims_is_a_specific_error(self):
        given, stranger = self.add_commit("aa1", at(10, 24)), self.add_commit("aa2", at(10, 25))
        result = self.run_check([given, stranger], allowed={given})
        self.assertEqual([(e.artifact_id, e.check) for e in result.errors], [(stranger, "not_in_source")])

    def test_citations_from_the_given_claims_pass(self):
        given = self.add_commit("aa1", at(10, 24))
        self.assertTrue(self.run_check([given], allowed={given}).ok)

    def test_without_a_list_any_citation_is_allowed(self):
        self.assertTrue(self.run_check([self.add_commit("aa1", at(10, 24))], allowed=None).ok)

    def test_the_range_can_start_after_the_sprint_does(self):
        early = self.add_commit("aa1", at(10, 24))
        result = self.run_check([early], allowed=None, first_day=date(2026, 10, 27))
        self.assertEqual([e.check for e in result.errors], ["outside_week"])
