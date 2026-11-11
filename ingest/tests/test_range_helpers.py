"""Tests for T-21 (#125): the artifact helpers take a first and last day, not a week."""

from datetime import date

from ingest.tests.test_sprint_range import FIRST, LAST
from summaries.tests.sprint_helpers import SprintTestCase, at


class RangeHelperTests(SprintTestCase):
    def setUp(self):
        super().setUp()
        self.inside = self.add_commit("aa1", at(10, 24))
        self.add_commit("aa2", at(10, 30, 0))

    def test_student_artifacts_takes_a_first_and_last_day(self):
        from ingest.services import student_artifacts

        found = student_artifacts(self.student, self.team, FIRST, LAST)
        self.assertEqual([a.artifact_id for a in found], [self.inside])

    def test_the_last_day_is_required(self):
        from ingest.services import student_artifacts, unattributed_commits

        with self.assertRaises(TypeError):
            student_artifacts(self.student, self.team, FIRST)
        with self.assertRaises(TypeError):
            unattributed_commits(self.repo, FIRST)

    def test_unattributed_commits_takes_a_first_and_last_day(self):
        from ingest.services import unattributed_commits

        loose = self.add_commit("cc1", at(10, 25), author="")
        self.assertEqual([a.artifact_id for a in unattributed_commits(self.repo, FIRST, LAST)], [loose])

    def test_the_week_named_lookup_is_gone(self):
        from ingest import services

        self.assertFalse(hasattr(services, "student_week_artifacts"))
        self.assertFalse(hasattr(services, "_week_bounds"))
