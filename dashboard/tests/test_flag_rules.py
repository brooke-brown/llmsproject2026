"""Tests for US-19 (#61): flag students whose share of requirement work is low.

Each student's weighted score is the sum of their verified, requirement-tied claims,
weighted minor = 1, moderate = 2, major = 3. A share below 50% of an equal share
raises a flag, and a share from 50% up to (not including) 75% raises a warning.
These tests go through compute_flags, so they don't depend on the rule's name.
"""

from datetime import date

from django.test import TestCase

from core.models import Course, Enrollment, Project, Team, TeamMembership
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user
from .test_flags import compute_flags

WEEK = date(2026, 10, 26)
EARLIER_WEEK = date(2026, 10, 19)
RULE = "low-requirement-share"


def make_team(project, name, *students):
    team = Team.objects.create(project=project, name=name)
    for student in students:
        TeamMembership.objects.create(user=student, team=team)
    return team


def give(student, team, *significances, week=WEEK, tied=True, status=Claim.Status.VERIFIED):
    """Give the student a summary for the week, with one claim per significance."""
    summary, _ = Summary.objects.get_or_create(team=team, user=student, week_start=week)
    for significance in significances:
        Claim.objects.create(
            summary=summary,
            text=f"Sample {significance} work",
            category="feature",
            artifact_ids=[],
            requirement_ids=[1] if tied else [],
            significance=significance,
            status=status,
        )
    return summary


class FlagRuleFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        cls.alan = make_user("sample-alan", "Alan", "Placeholder")
        cls.edsger = make_user("sample-edsger", "Edsger", "Demo")
        for student in [cls.ada, cls.grace, cls.alan, cls.edsger]:
            enroll(student, cls.course, Enrollment.Role.STUDENT)

        # Four students, so an equal share is 25%: below 12.5% is a flag, and from
        # 12.5% up to 18.75% is a warning.
        cls.team = make_team(cls.project, "Team Alpha", cls.ada, cls.grace, cls.alan, cls.edsger)
        # Two students, so an equal share is 50%: below 25% is a flag, and from 25%
        # up to 37.5% is a warning.
        cls.pair = make_team(cls.project, "Team Pair", cls.ada, cls.grace)

    def flags_for(self, team):
        compute_flags(team)
        return list(team.flags.order_by("user__github_login"))

    def flagged(self, team):
        """{github_login: severity} for the team's flags."""
        return {flag.user.github_login: flag.severity for flag in self.flags_for(team)}


class LowShareThresholdTests(FlagRuleFixture):
    def test_share_below_half_an_equal_share_raises_a_flag(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "major")
        give(self.alan, self.team, "major")
        give(self.edsger, self.team, "minor")  # 1 of 10 points: 10%.

        self.assertEqual(self.flagged(self.team), {"sample-edsger": "flag"})

    def test_share_between_half_and_three_quarters_of_an_equal_share_raises_a_warning(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "major")
        give(self.alan, self.team, "major")
        give(self.edsger, self.team, "moderate")  # 2 of 11 points: about 18.2%.

        self.assertEqual(self.flagged(self.team), {"sample-edsger": "warning"})

    def test_share_of_exactly_half_an_equal_share_is_a_warning_not_a_flag(self):
        give(self.ada, self.pair, "minor")  # 1 of 4 points: 25%.
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "warning"})

    def test_share_just_below_half_an_equal_share_is_a_flag(self):
        give(self.ada, self.pair, "minor")  # 1 of 5 points: 20%.
        give(self.grace, self.pair, "major", "minor")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "flag"})

    def test_share_of_exactly_three_quarters_of_an_equal_share_raises_nothing(self):
        give(self.ada, self.pair, "major")  # 3 of 8 points: 37.5%.
        give(self.grace, self.pair, "major", "moderate")

        self.assertEqual(self.flagged(self.pair), {})

    def test_balanced_team_raises_nothing(self):
        for student in [self.ada, self.grace, self.alan, self.edsger]:
            give(student, self.team, "moderate")

        self.assertEqual(self.flagged(self.team), {})

    def test_every_low_student_is_flagged(self):
        give(self.ada, self.team, "major", "major")
        give(self.grace, self.team, "major", "major")
        give(self.alan, self.team, "minor")  # 1 of 14 points: about 7.1%.
        give(self.edsger, self.team)  # No claims: 0%.

        self.assertEqual(
            self.flagged(self.team), {"sample-alan": "flag", "sample-edsger": "flag"}
        )


class WeightingTests(FlagRuleFixture):
    def test_claims_are_weighted_by_significance_not_counted(self):
        # By count Ada has half the claims, but by weight she has 1 of 4 points.
        give(self.ada, self.pair, "minor")
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "warning"})

    def test_equal_weights_from_different_claims_are_balanced(self):
        give(self.ada, self.pair, "minor", "minor", "minor")
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {})

    def test_claims_that_couldnt_be_verified_never_count(self):
        give(self.ada, self.pair, "major", "major", status=Claim.Status.COULDNT_VERIFY)
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "flag"})

    def test_claims_not_tied_to_a_requirement_never_count(self):
        give(self.ada, self.pair, "major", "major", tied=False)
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "flag"})

    def test_only_this_teams_claims_count(self):
        other = make_team(self.project, "Team Other", self.ada)
        give(self.ada, other, "major", "major", "major")
        give(self.ada, self.pair, "minor")
        give(self.grace, self.pair, "major", "minor")

        self.assertEqual(self.flagged(self.pair), {"sample-ada": "flag"})


class WeekSelectionTests(FlagRuleFixture):
    def test_checks_the_latest_week_every_student_has_a_summary_for(self):
        give(self.ada, self.pair, "minor", week=EARLIER_WEEK)
        give(self.grace, self.pair, "major", "major", week=EARLIER_WEEK)
        # Only Grace has a summary for the later week, so it isn't checked.
        give(self.grace, self.pair, "major")

        flags = self.flags_for(self.pair)

        self.assertEqual([(f.user, f.week_start) for f in flags], [(self.ada, EARLIER_WEEK)])

    def test_earlier_weeks_are_ignored_once_a_later_week_is_complete(self):
        give(self.ada, self.pair, "minor", week=EARLIER_WEEK)
        give(self.grace, self.pair, "major", "major", week=EARLIER_WEEK)
        give(self.ada, self.pair, "major")
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {})

    def test_no_week_with_every_students_summary_raises_nothing(self):
        give(self.grace, self.pair, "major")

        self.assertEqual(self.flagged(self.pair), {})

    def test_a_week_with_no_requirement_tied_work_raises_nothing(self):
        give(self.ada, self.pair, "major", tied=False)
        give(self.grace, self.pair, "major", tied=False)

        self.assertEqual(self.flagged(self.pair), {})

    def test_team_with_one_student_raises_nothing(self):
        solo = make_team(self.project, "Team Solo", self.ada)
        give(self.ada, solo)

        self.assertEqual(self.flagged(solo), {})

    def test_team_with_no_students_raises_nothing(self):
        empty = make_team(self.project, "Team Empty")

        self.assertEqual(self.flagged(empty), {})


class FlagContentTests(FlagRuleFixture):
    def setUp(self):
        give(self.ada, self.team, "major")
        give(self.grace, self.team, "major")
        give(self.alan, self.team, "major")
        give(self.edsger, self.team, "minor")  # 1 of 10 points: 10%.

    def test_flag_names_the_student_week_and_rule(self):
        [flag] = self.flags_for(self.team)

        self.assertEqual(flag.team, self.team)
        self.assertEqual(flag.user, self.edsger)
        self.assertEqual(flag.week_start, WEEK)
        self.assertEqual(flag.rule, RULE)

    def test_reason_describes_the_share_and_an_equal_share(self):
        [flag] = self.flags_for(self.team)

        self.assertEqual(
            flag.reason,
            "Edsger Demo did 10% of the team's requirement-tied work in the week of "
            "Oct 26 (an equal share is 25%).",
        )

    def test_reason_uses_the_github_login_when_a_student_has_no_name(self):
        self.edsger.first_name = self.edsger.last_name = ""
        self.edsger.save()

        [flag] = self.flags_for(self.team)

        self.assertTrue(flag.reason.startswith("sample-edsger did 10%"))

    def test_recomputing_replaces_the_flags_instead_of_adding_more(self):
        compute_flags(self.team)

        self.assertEqual(len(self.flags_for(self.team)), 1)
