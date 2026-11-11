"""Tests for T-19 (#119): the ingest helpers take a first and last day, both inclusive."""

from datetime import date

from summaries.tests.sprint_helpers import SprintTestCase, at

FIRST, LAST = date(2026, 10, 23), date(2026, 10, 29)  # Friday to Thursday.


class RangeHelperTests(SprintTestCase):
    def setUp(self):
        super().setUp()
        self.inside = [
            self.add_commit("aa1", at(10, 23, 0, 30)),
            self.add_commit("aa2", at(10, 29, 23, 30)),
        ]
        self.add_commit("aa3", at(10, 22, 23, 30))
        self.add_commit("aa4", at(10, 30, 0, 30))

    def test_student_artifacts_stops_at_the_last_day(self):
        from ingest.services import student_artifacts

        found = student_artifacts(self.student, self.team, FIRST, LAST)
        self.assertEqual([a.artifact_id for a in found], self.inside)

    def test_a_later_last_day_takes_in_more(self):
        from ingest.services import student_artifacts

        wider = student_artifacts(self.student, self.team, FIRST, date(2026, 10, 30))
        self.assertEqual(len(wider), 3)

    def test_unattributed_commits_stops_at_the_last_day(self):
        from ingest.services import unattributed_commits

        for suffix, when in (("cc1", at(10, 23)), ("cc2", at(10, 30))):
            self.add_commit(suffix, when, author="")
        found = unattributed_commits(self.repo, FIRST, LAST)
        self.assertEqual([a.artifact_id for a in found], ["acme/app:commit:cc1"])
