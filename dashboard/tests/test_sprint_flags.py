"""Tests for US-41 (#123): flag students on their share of a finished sprint.

A student's share is their verified, requirement-tied claims in the sprint's summary, weighted
minor = 1, moderate = 2, major = 3. Below 1/2 of an equal share is a flag; from 1/2 up to (not
including) 3/4 is a warning. Flags are raised only for a sprint that has ended and where every
student on the team has a complete summary, and each sprint keeps its own flags.

The clock is fixed at Tuesday Nov 10, 2026, noon in New York. Sprint 1 (Oct 12-25) and Sprint 2
(Oct 26-Nov 1) have ended; Sprint 3 (Nov 2-15) is still running.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Course, Enrollment, Project, Repo, Sprint, Team, TeamMembership
from dashboard import services
from dashboard.models import Flag, FlagDispute
from ingest.models import Artifact
from summaries.models import Claim, Summary, ValidationRun

from .test_course_overview import enroll, make_user, overview_url
from .test_flag_rules import give as give_week
from .test_show_flags import article_containing

Role = Enrollment.Role
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=NEW_YORK)
# After Sprints 1 and 2 ended, so their summaries are complete.
AFTER_SPRINT_2 = datetime(2026, 11, 3, 9, 0, tzinfo=NEW_YORK)
# The last day of Sprint 2, so a summary written then is partial.
DURING_SPRINT_2 = datetime(2026, 11, 1, 18, 0, tzinfo=NEW_YORK)
RULE = "low-requirement-share"
REPO = "acme/app"


def at(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


class SprintFlagFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.sprint1 = Sprint.objects.create(
            project=cls.project, name="Sprint 1", start_date=date(2026, 10, 12),
            end_date=date(2026, 10, 25),
        )
        cls.sprint2 = Sprint.objects.create(
            project=cls.project, name="Sprint 2", start_date=date(2026, 10, 26),
            end_date=date(2026, 11, 1),
        )
        cls.sprint3 = Sprint.objects.create(
            project=cls.project, name="Sprint 3", start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 15),
        )

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        cls.alan = make_user("sample-alan", "Alan", "Placeholder")
        cls.edsger = make_user("sample-edsger", "Edsger", "Demo")
        for student in [cls.ada, cls.grace, cls.alan, cls.edsger]:
            enroll(student, cls.course, Role.STUDENT)

        # Four students, so an equal share is 25%: below 12.5% is a flag, and from 12.5% up to
        # 18.75% is a warning.
        cls.team = cls.make_team("Team Alpha", cls.ada, cls.grace, cls.alan, cls.edsger)
        # Two students, so an equal share is 50%: below 25% is a flag, and from 25% up to 37.5%
        # is a warning.
        cls.pair = cls.make_team("Team Pair", cls.ada, cls.grace)
        cls.repo = Repo.objects.create(
            team=cls.pair, url=f"https://github.com/{REPO}", full_name=REPO
        )

    @classmethod
    def make_team(cls, name, *students):
        team = Team.objects.create(project=cls.project, name=name)
        for student in students:
            TeamMembership.objects.create(user=student, team=team)
        return team

    def setUp(self):
        patcher = at(NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def give(
        self, student, team, sprint, *significances, generated=AFTER_SPRINT_2, tied=True,
        status=Claim.Status.VERIFIED, artifact_ids=(),
    ):
        """Give the student a summary for the sprint, generated at `generated`, with one claim
        per significance. Generating again reuses the summary, as generate_sprint_summary does."""
        with at(generated):
            summary, _ = Summary.objects.get_or_create(team=team, user=student, sprint=sprint)
            for significance in significances:
                Claim.objects.create(
                    summary=summary,
                    text=f"Sample {significance} work",
                    category="feature",
                    artifact_ids=list(artifact_ids),
                    requirement_ids=[1] if tied else [],
                    significance=significance,
                    status=status,
                )
            ValidationRun.objects.create(
                summary=summary, claims_total=len(significances), claims_first_pass=0,
                claims_dropped=0, retries=0,
            )
        return summary

    def regenerate(self, summary, generated):
        """Log another generation of an existing summary, without changing its claims."""
        with at(generated):
            ValidationRun.objects.create(
                summary=summary, claims_total=0, claims_first_pass=0, claims_dropped=0, retries=0
            )

    def compute(self, team):
        from dashboard.flags import compute_flags

        return compute_flags(team)

    def flagged(self, team, sprint):
        """{github_login: severity} for the team's flags in this sprint."""
        self.compute(team)
        return {
            flag.user.github_login: flag.severity
            for flag in Flag.objects.filter(team=team, sprint=sprint)
        }

    def flags_for(self, team, sprint):
        self.compute(team)
        return list(Flag.objects.filter(team=team, sprint=sprint).order_by("user__github_login"))


class ModelTests(SprintFlagFixture):
    def test_a_flag_belongs_to_a_sprint_not_a_week(self):
        field_names = {field.name for field in Flag._meta.get_fields()}

        self.assertIn("sprint", field_names)
        self.assertNotIn("week_start", field_names)

    def test_a_dispute_belongs_to_a_sprint_not_a_week(self):
        field_names = {field.name for field in FlagDispute._meta.get_fields()}

        self.assertIn("sprint", field_names)
        self.assertNotIn("week_start", field_names)

    def dispute(self, sprint):
        return FlagDispute.objects.create(
            team=self.pair, student=self.ada, rule=RULE, sprint=sprint,
            flag_reason="Sample reason", severity="flag", explanation="Sample explanation",
        )

    def test_a_student_can_dispute_the_same_rule_in_two_sprints(self):
        self.dispute(self.sprint1)
        self.dispute(self.sprint2)

        self.assertEqual(FlagDispute.objects.count(), 2)

    def test_a_student_cant_dispute_the_same_flag_twice(self):
        self.dispute(self.sprint1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.dispute(self.sprint1)


class ShareTests(SprintFlagFixture):
    def test_share_below_half_an_equal_share_raises_a_flag(self):
        for student in [self.ada, self.grace, self.alan]:
            self.give(student, self.team, self.sprint2, "major")
        self.give(self.edsger, self.team, self.sprint2, "minor")  # 1 of 10 points: 10%.

        self.assertEqual(self.flagged(self.team, self.sprint2), {"sample-edsger": "flag"})

    def test_share_between_half_and_three_quarters_of_an_equal_share_raises_a_warning(self):
        for student in [self.ada, self.grace, self.alan]:
            self.give(student, self.team, self.sprint2, "major")
        self.give(self.edsger, self.team, self.sprint2, "moderate")  # 2 of 11: about 18.2%.

        self.assertEqual(self.flagged(self.team, self.sprint2), {"sample-edsger": "warning"})

    def test_share_of_exactly_half_an_equal_share_is_a_warning(self):
        self.give(self.ada, self.pair, self.sprint2, "minor")  # 1 of 4 points: 25%.
        self.give(self.grace, self.pair, self.sprint2, "major")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {"sample-ada": "warning"})

    def test_share_of_exactly_three_quarters_of_an_equal_share_raises_nothing(self):
        self.give(self.ada, self.pair, self.sprint2, "major")  # 3 of 8 points: 37.5%.
        self.give(self.grace, self.pair, self.sprint2, "major", "moderate")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})

    def test_claims_are_weighted_by_significance(self):
        self.give(self.ada, self.pair, self.sprint2, "minor", "minor", "minor")
        self.give(self.grace, self.pair, self.sprint2, "major")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})

    def test_claims_that_couldnt_be_verified_never_count(self):
        self.give(
            self.ada, self.pair, self.sprint2, "major", "major",
            status=Claim.Status.COULDNT_VERIFY,
        )
        self.give(self.grace, self.pair, self.sprint2, "major")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {"sample-ada": "flag"})

    def test_claims_not_tied_to_a_requirement_never_count(self):
        self.give(self.ada, self.pair, self.sprint2, "major", "major", tied=False)
        self.give(self.grace, self.pair, self.sprint2, "major")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {"sample-ada": "flag"})

    def test_a_sprint_with_no_requirement_tied_work_raises_nothing(self):
        self.give(self.ada, self.pair, self.sprint2, "major", tied=False)
        self.give(self.grace, self.pair, self.sprint2, "major", tied=False)

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})

    def test_a_team_with_one_student_raises_nothing(self):
        solo = self.make_team("Team Solo", self.ada)
        self.give(self.ada, solo, self.sprint2, "major")

        self.assertEqual(self.flagged(solo, self.sprint2), {})

    def test_weekly_summaries_raise_no_flags(self):
        give_week(self.ada, self.pair, "minor", week=date(2026, 10, 26))
        give_week(self.grace, self.pair, "major", "major", week=date(2026, 10, 26))

        self.compute(self.pair)

        self.assertFalse(Flag.objects.filter(team=self.pair).exists())


class WholeSprintTests(SprintFlagFixture):
    """Sprint 1 is two weeks long. Shares cover the whole sprint, so when in it a student
    worked doesn't matter."""

    def artifact(self, number, day, author):
        artifact = Artifact.objects.create(
            repo=self.repo,
            artifact_id=f"{REPO}:pr:{number}",
            type=Artifact.Type.PR,
            url=f"https://github.com/{REPO}/pull/{number}",
            timestamp=datetime(2026, 10, day, 12, 0, tzinfo=NEW_YORK),
            author_login=author.github_login,
        )
        return artifact.artifact_id

    def test_front_loaded_and_back_loaded_work_of_equal_weight_raises_nothing(self):
        early = self.artifact(1, 13, self.ada)  # First week of the sprint.
        late = self.artifact(2, 24, self.grace)  # Second week.
        self.give(self.ada, self.pair, self.sprint1, "major", artifact_ids=[early])
        self.give(self.grace, self.pair, self.sprint1, "major", artifact_ids=[late])

        self.assertEqual(self.flagged(self.pair, self.sprint1), {})

    def test_a_student_who_did_their_work_early_isnt_flagged_for_a_quiet_second_week(self):
        early = [self.artifact(n, 13, self.ada) for n in (1, 2)]
        late = self.artifact(3, 22, self.grace)
        self.give(self.ada, self.pair, self.sprint1, "major", "major", artifact_ids=early)
        self.give(self.grace, self.pair, self.sprint1, "major", "major", artifact_ids=[late])

        self.assertEqual(self.flagged(self.pair, self.sprint1), {})

    def test_a_student_who_did_their_work_late_isnt_flagged_for_a_quiet_first_week(self):
        early = self.artifact(1, 13, self.grace)
        late = [self.artifact(n, 24, self.ada) for n in (2, 3)]
        self.give(self.ada, self.pair, self.sprint1, "major", "major", artifact_ids=late)
        self.give(self.grace, self.pair, self.sprint1, "major", "major", artifact_ids=[early])

        self.assertEqual(self.flagged(self.pair, self.sprint1), {})


class EligibilityTests(SprintFlagFixture):
    def test_a_running_sprint_raises_no_flags_or_warnings(self):
        with at(NOW):
            self.give(self.ada, self.pair, self.sprint3, "minor", generated=NOW)
            self.give(self.grace, self.pair, self.sprint3, "major", "major", generated=NOW)

        self.assertEqual(self.flagged(self.pair, self.sprint3), {})

    def test_a_partial_summary_keeps_the_sprint_from_raising_flags(self):
        self.give(self.ada, self.pair, self.sprint2, "minor", generated=DURING_SPRINT_2)
        self.give(self.grace, self.pair, self.sprint2, "major", "major")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})

    def test_a_partial_summary_counts_once_the_sprint_is_generated_again(self):
        ada = self.give(self.ada, self.pair, self.sprint2, "minor", generated=DURING_SPRINT_2)
        self.give(self.grace, self.pair, self.sprint2, "major", "major")
        self.regenerate(ada, AFTER_SPRINT_2)

        self.assertEqual(self.flagged(self.pair, self.sprint2), {"sample-ada": "flag"})

    def test_a_missing_summary_keeps_the_sprint_from_raising_flags(self):
        for student in [self.ada, self.grace, self.alan]:
            self.give(student, self.team, self.sprint2, "major")
        # Edsger has no summary for Sprint 2.

        self.assertEqual(self.flagged(self.team, self.sprint2), {})

    def test_a_summary_on_another_team_doesnt_complete_the_sprint(self):
        self.give(self.ada, self.pair, self.sprint2, "major")
        self.give(self.grace, self.team, self.sprint2, "minor")

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})


class FlagContentTests(SprintFlagFixture):
    def setUp(self):
        super().setUp()
        for student in [self.ada, self.grace, self.alan]:
            self.give(student, self.team, self.sprint2, "major")
        self.give(self.edsger, self.team, self.sprint2, "minor")  # 1 of 10 points: 10%.

    def test_flag_names_the_team_student_sprint_and_rule(self):
        [flag] = self.flags_for(self.team, self.sprint2)

        self.assertEqual(flag.team, self.team)
        self.assertEqual(flag.user, self.edsger)
        self.assertEqual(flag.sprint, self.sprint2)
        self.assertEqual(flag.rule, RULE)

    def test_reason_names_the_sprint(self):
        [flag] = self.flags_for(self.team, self.sprint2)

        self.assertEqual(
            flag.reason,
            "Edsger Demo did 10% of the team's requirement-tied work in Sprint 2 "
            "(an equal share is 25%).",
        )

    def test_compute_flags_returns_the_saved_flags(self):
        flags = self.compute(self.team)

        self.assertEqual([(f.user, f.sprint) for f in flags], [(self.edsger, self.sprint2)])
        self.assertTrue(all(f.pk for f in flags))

    def test_recomputing_replaces_the_flags_instead_of_adding_more(self):
        self.compute(self.team)
        self.compute(self.team)

        self.assertEqual(Flag.objects.filter(team=self.team).count(), 1)


class PerSprintTests(SprintFlagFixture):
    def setUp(self):
        super().setUp()
        # Ada did little in Sprint 1, and Grace did little in Sprint 2.
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")
        self.give(self.ada, self.pair, self.sprint2, "major", "major")
        self.give(self.grace, self.pair, self.sprint2, "minor")

    def test_each_finished_sprint_keeps_its_own_flags(self):
        self.compute(self.pair)

        self.assertEqual(
            {(f.sprint.name, f.user.github_login) for f in Flag.objects.filter(team=self.pair)},
            {("Sprint 1", "sample-ada"), ("Sprint 2", "sample-grace")},
        )

    def test_generating_one_sprint_again_doesnt_change_anothers_flags(self):
        [before] = self.flags_for(self.pair, self.sprint1)
        # Grace's Sprint 2 summary is generated again with more work, which balances Sprint 2.
        with at(NOW):
            self.give(self.grace, self.pair, self.sprint2, "major", generated=NOW)

        self.assertEqual(self.flagged(self.pair, self.sprint2), {})
        [after] = self.flags_for(self.pair, self.sprint1)
        self.assertEqual(
            (after.user, after.severity, after.reason),
            (before.user, before.severity, before.reason),
        )

    def test_another_teams_flags_are_left_alone(self):
        other = self.make_team("Team Other", self.alan, self.edsger)
        self.give(self.alan, other, self.sprint1, "minor")
        self.give(self.edsger, other, self.sprint1, "major", "major")
        self.compute(other)

        self.compute(self.pair)

        self.assertTrue(Flag.objects.filter(team=other, sprint=self.sprint1).exists())


class WithdrawnDisputeTests(SprintFlagFixture):
    def setUp(self):
        super().setUp()
        self.give(self.ada, self.pair, self.sprint2, "minor")
        self.give(self.grace, self.pair, self.sprint2, "major", "major")
        self.compute(self.pair)
        FlagDispute.objects.create(
            team=self.pair, student=self.ada, rule=RULE, sprint=self.sprint2,
            flag_reason="Sample reason", severity="flag", explanation="Sample explanation",
            status=FlagDispute.Status.FLAG_WITHDRAWN,
        )

    def test_a_withdrawn_flag_stays_withdrawn_when_the_sprint_is_generated_again(self):
        self.give(self.ada, self.pair, self.sprint2, generated=NOW)
        self.compute(self.pair)

        self.assertTrue(Flag.objects.filter(team=self.pair, sprint=self.sprint2).exists())
        self.assertFalse(services.active_flags().filter(team=self.pair).exists())

    def test_a_withdrawn_dispute_in_one_sprint_doesnt_withdraw_anothers_flag(self):
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")
        self.compute(self.pair)

        self.assertEqual(
            list(services.active_flags().filter(team=self.pair).values_list("sprint", flat=True)),
            [self.sprint1.pk],
        )


class CourseOverviewTests(SprintFlagFixture):
    """The overview labels a team from its latest sprint that has ended and where every
    student has a complete summary."""

    def label(self, team):
        self.client.force_login(self.instructor)
        content = self.client.get(overview_url(self.course.id)).content.decode()
        team_article = article_containing(content, team.name)
        if "<mark>Flag</mark>" in team_article:
            return "Flag"
        if "<mark>Warning</mark>" in team_article:
            return "Warning"
        return None

    def test_a_flag_in_an_earlier_sprint_doesnt_label_a_team_balanced_in_a_later_one(self):
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")
        self.give(self.ada, self.pair, self.sprint2, "major")
        self.give(self.grace, self.pair, self.sprint2, "major")
        self.compute(self.pair)

        self.assertIsNone(self.label(self.pair))

    def test_a_running_sprint_doesnt_replace_the_latest_finished_one(self):
        self.give(self.ada, self.pair, self.sprint2, "minor")
        self.give(self.grace, self.pair, self.sprint2, "major", "major")
        self.give(self.ada, self.pair, self.sprint3, "major", generated=NOW)
        self.give(self.grace, self.pair, self.sprint3, "major", generated=NOW)
        self.compute(self.pair)

        self.assertEqual(self.label(self.pair), "Flag")

    def test_a_sprint_missing_a_summary_doesnt_replace_the_latest_complete_one(self):
        self.give(self.ada, self.pair, self.sprint1, "major")
        self.give(self.grace, self.pair, self.sprint1, "major", "moderate")  # Ada: 3 of 8.
        self.give(self.ada, self.pair, self.sprint2, "major")
        # Grace has no Sprint 2 summary, so Sprint 1 still labels the team.
        self.compute(self.pair)

        self.assertIsNone(self.label(self.pair))
        Claim.objects.filter(summary__user=self.ada, summary__sprint=self.sprint1).update(
            significance="minor"
        )  # Ada: 1 of 6, a flag.
        self.compute(self.pair)

        self.assertEqual(self.label(self.pair), "Flag")
