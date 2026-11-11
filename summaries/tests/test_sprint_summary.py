"""Tests for T-19 (#119): generate_sprint_summary, one summary per student per sprint.

The LLM is mocked, so nothing here makes a network call. Time is pinned by patching
django.utils.timezone.now, which is what "today" and a ValidationRun's timestamp use.
"""

from datetime import date
from unittest import mock

from django.db import IntegrityError, transaction

from core.models import Course, Project, Sprint
from summaries.models import Summary
from summaries.tests.sprint_helpers import SprintTestCase, at, make_claim


def generate_sprint_summary(*args, **kwargs):
    from summaries.services import generate_sprint_summary

    return generate_sprint_summary(*args, **kwargs)


class GenerateSprintSummaryTests(SprintTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("summaries.services.generate_claims")
        self.llm = patcher.start()
        self.addCleanup(patcher.stop)
        self.set_now(at(11, 2))  # After the sprint ends.

    def set_now(self, moment):
        patcher = mock.patch("django.utils.timezone.now", return_value=moment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def generate(self, sprint=None, user=None):
        return generate_sprint_summary(user or self.student, self.team, sprint or self.sprint)

    @property
    def prompt(self):
        return self.llm.call_args.args[0]

    def test_covers_a_friday_to_thursday_sprint_and_nothing_a_day_outside_it(self):
        before = self.add_commit("aa1", at(10, 22, 23, 30))
        first = self.add_commit("aa2", at(10, 23, 0, 30))
        last = self.add_commit("aa3", at(10, 29, 23, 30))
        after = self.add_commit("aa4", at(10, 30, 0, 30))
        self.llm.return_value = [make_claim(f"acme/app:commit:aa2", f"acme/app:commit:aa3")]
        self.generate()
        for included in ("aa2", "aa3"):
            self.assertIn(f"acme/app:commit:{included}", self.prompt)
        for excluded in ("aa1", "aa4"):
            self.assertNotIn(f"acme/app:commit:{excluded}", self.prompt)

    def test_a_verified_claim_is_saved_on_the_sprint_summary(self):
        self.add_commit("aa2", at(10, 25))
        self.llm.return_value = [make_claim("acme/app:commit:aa2")]
        summary = self.generate()
        self.assertEqual(summary.sprint, self.sprint)
        self.assertIsNone(summary.week_start)
        self.assertEqual(summary.claims.get().status, "verified")

    def test_a_running_sprint_covers_only_through_today_and_is_partial(self):
        self.set_now(at(10, 25))
        self.add_commit("aa1", at(10, 25))
        self.add_commit("aa2", at(10, 26))
        self.llm.return_value = [make_claim("acme/app:commit:aa1")]
        summary = self.generate()
        self.assertIn("acme/app:commit:aa1", self.prompt)
        self.assertNotIn("acme/app:commit:aa2", self.prompt)
        self.assertTrue(summary.is_partial)

    def test_a_summary_written_after_the_sprint_is_not_partial(self):
        summary = self.generate()
        self.assertFalse(summary.is_partial)

    def test_a_summary_written_on_the_last_day_is_still_partial(self):
        self.set_now(at(10, 29, 18))
        self.assertTrue(self.generate().is_partial)

    def test_the_validator_rejects_an_artifact_after_the_covered_range(self):
        # The LLM cites something real but dated after today.
        self.set_now(at(10, 25))
        self.add_commit("aa1", at(10, 25))
        later = self.add_commit("aa2", at(10, 27))
        self.llm.return_value = [make_claim(later)]
        summary = self.generate()
        self.assertEqual(summary.claims.get().status, "couldnt_verify")

    def test_generating_again_replaces_the_students_summary(self):
        self.add_commit("aa1", at(10, 25))
        self.llm.return_value = [make_claim("acme/app:commit:aa1")]
        first = self.generate()
        self.llm.return_value = [make_claim("acme/app:commit:aa1"), make_claim("acme/app:commit:aa1")]
        second = self.generate()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Summary.objects.filter(sprint=self.sprint).count(), 1)
        self.assertEqual(second.claims.count(), 2)

    def test_a_day_shared_by_two_sprints_belongs_to_both(self):
        next_sprint = Sprint.objects.create(
            project=self.project, name="Sprint 2", start_date=date(2026, 10, 29), end_date=date(2026, 11, 5)
        )
        shared = self.add_commit("aa1", at(10, 29))
        self.llm.return_value = [make_claim(shared)]
        for sprint in (self.sprint, next_sprint):
            self.assertEqual(self.generate(sprint).claims.get().status, "verified")

    def test_an_unstarted_sprint_raises_and_saves_nothing(self):
        self.set_now(at(10, 20))
        with self.assertRaises(ValueError):
            self.generate()
        self.assertFalse(Summary.objects.exists())
        self.llm.assert_not_called()

    def test_another_projects_sprint_raises(self):
        other_project = Project.objects.create(course=Course.objects.get(), name="Project 2")
        sprint = Sprint.objects.create(
            project=other_project, name="Sprint 1", start_date=date(2026, 10, 23), end_date=date(2026, 10, 29)
        )
        with self.assertRaises(ValueError):
            self.generate(sprint)

    def test_a_student_not_on_the_team_raises(self):
        from django.contrib.auth import get_user_model

        stranger = get_user_model().objects.create_user("cy", github_login="cy-dev", password="x")
        with self.assertRaises(ValueError):
            self.generate(user=stranger)

    def test_the_prompt_describes_the_sprint_not_a_week(self):
        self.add_commit("aa1", at(10, 25))
        self.llm.return_value = []
        self.generate()
        self.assertIn("Sprint 1", self.prompt)
        self.assertIn("2026-10-23", self.prompt)
        self.assertIn("2026-10-29", self.prompt)
        self.assertNotIn("for the week", self.prompt)
        self.assertIn("sprint", self.llm.call_args.kwargs["system"].lower())
        self.assertNotIn("weekly", self.llm.call_args.kwargs["system"].lower())


class TwoSprintsTestCase(SprintTestCase):
    """Two sprints whose requirement 1 differ, plus a leftover project-wide requirement."""

    def setUp(self):
        super().setUp()
        self.next_sprint = Sprint.objects.create(
            project=self.project, name="Sprint 2", start_date=date(2026, 10, 30), end_date=date(2026, 11, 5)
        )
        self.add_requirement(self.sprint, 1, "Users can log in")
        self.add_requirement(self.sprint, 2, "Users can reset passwords")
        self.add_requirement(self.next_sprint, 1, "Users can pay")
        self.add_requirement(None, 3, "A project-wide leftover")
        patcher = mock.patch("summaries.services.generate_claims")
        self.llm = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("django.utils.timezone.now", return_value=at(11, 10))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.commit = self.add_commit("bb1", at(11, 2))


class SprintRequirementTests(TwoSprintsTestCase):
    """The same requirement number can mean different things in two sprints."""

    def test_the_prompt_lists_only_the_sprints_requirements(self):
        self.llm.return_value = []
        generate_sprint_summary(self.student, self.team, self.next_sprint)
        prompt = self.llm.call_args.args[0]
        self.assertIn("Users can pay", prompt)
        for other in ("Users can log in", "reset passwords", "project-wide leftover"):
            self.assertNotIn(other, prompt)

    def test_a_number_that_exists_in_the_sprint_is_accepted(self):
        self.llm.return_value = [make_claim(self.commit, requirement_ids=[1])]
        summary = generate_sprint_summary(self.student, self.team, self.next_sprint)
        self.assertEqual(summary.claims.get().status, "verified")

    def test_a_number_that_exists_only_in_another_sprint_is_rejected(self):
        self.llm.return_value = [make_claim(self.commit, requirement_ids=[2])]
        summary = generate_sprint_summary(self.student, self.team, self.next_sprint)
        claim = summary.claims.get()
        self.assertEqual(claim.status, "couldnt_verify")
        self.assertEqual(claim.validator_errors[-1]["check"], "unknown_requirement")


class SprintWordingTests(TwoSprintsTestCase):
    """Everything sent to the LLM describes the sprint and its own requirements, never the
    project's, because a sprint's list is all it's offered (#139 review)."""

    def test_the_requirement_list_is_labelled_as_the_sprints(self):
        self.llm.return_value = []
        generate_sprint_summary(self.student, self.team, self.next_sprint)
        prompt = self.llm.call_args.args[0]
        self.assertIn("Sprint 2's requirements:", prompt)
        self.assertNotIn("project", prompt.lower())

    def test_a_sprint_with_no_requirements_says_so_even_when_another_sprint_has_some(self):
        empty = Sprint.objects.create(
            project=self.project, name="Sprint 3", start_date=date(2026, 11, 5), end_date=date(2026, 11, 9)
        )
        self.add_commit("cc1", at(11, 6))
        self.llm.return_value = []
        generate_sprint_summary(self.student, self.team, empty)
        prompt = self.llm.call_args.args[0]
        self.assertIn("Sprint 3 has no requirements, so leave requirement_ids empty.", prompt)
        self.assertNotIn("project", prompt.lower())

    def test_the_system_prompt_points_at_the_sprints_requirement_list(self):
        self.llm.return_value = []
        generate_sprint_summary(self.student, self.team, self.next_sprint)
        system = self.llm.call_args.kwargs["system"]
        self.assertIn("the sprint's requirement list", system)
        self.assertNotIn("project", system.lower())

    def test_the_error_for_another_sprints_number_names_this_sprint(self):
        self.llm.return_value = [make_claim(self.commit, requirement_ids=[2])]
        summary = generate_sprint_summary(self.student, self.team, self.next_sprint)
        message = summary.claims.get().validator_errors[-1]["message"]
        self.assertEqual(message, "Requirement 2 isn't one of Sprint 2's requirements.")

    def test_the_retry_prompt_keeps_the_sprint_wording(self):
        self.llm.return_value = [make_claim(self.commit, requirement_ids=[2])]
        generate_sprint_summary(self.student, self.team, self.next_sprint)
        retry = self.llm.call_args_list[1]
        self.assertIn("Sprint 2's requirements:", retry.args[0])
        self.assertIn("isn't one of Sprint 2's requirements", retry.args[0])
        self.assertNotIn("project", retry.args[0].lower())
        self.assertNotIn("project", retry.kwargs["system"].lower())


class SummarySprintModelTests(SprintTestCase):
    def test_a_summary_needs_a_week_or_a_sprint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Summary.objects.create(team=self.team, user=self.student)

    def test_a_summary_cannot_have_both_a_week_and_a_sprint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Summary.objects.create(
                team=self.team, user=self.student, week_start=date(2026, 10, 26), sprint=self.sprint
            )

    def test_one_summary_per_team_student_and_sprint(self):
        Summary.objects.create(team=self.team, user=self.student, sprint=self.sprint)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Summary.objects.create(team=self.team, user=self.student, sprint=self.sprint)

    def test_another_student_or_sprint_is_allowed(self):
        other_sprint = Sprint.objects.create(
            project=self.project, name="Sprint 2", start_date=date(2026, 10, 29), end_date=date(2026, 11, 5)
        )
        Summary.objects.create(team=self.team, user=self.student, sprint=self.sprint)
        Summary.objects.create(team=self.team, user=self.other, sprint=self.sprint)
        Summary.objects.create(team=self.team, user=self.student, sprint=other_sprint)

    def test_weekly_summaries_still_work_beside_sprint_ones(self):
        Summary.objects.create(team=self.team, user=self.student, week_start=date(2026, 10, 26))
        Summary.objects.create(team=self.team, user=self.student, sprint=self.sprint)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Summary.objects.create(team=self.team, user=self.student, week_start=date(2026, 10, 26))


class SprintFlagCodeIgnoresWeeklySummariesTests(SprintTestCase):
    """Until T-21, weekly summaries sit beside sprint summaries, and the sprint flag code
    (US-41) must not trip on them."""

    def test_sprint_requirement_shares_skip_a_weekly_summary(self):
        from dashboard import flags

        for user in (self.student, self.other):
            Summary.objects.create(team=self.team, user=user, sprint=self.sprint)
            Summary.objects.create(team=self.team, user=user, week_start=date(2026, 10, 26))
        # Neither kind has claims, so there's nothing to compare.
        self.assertIsNone(flags.sprint_shares(self.team, self.sprint))
