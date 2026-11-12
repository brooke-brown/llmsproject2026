"""Tests for the data behind the Balance layout (docs/mockups/c-balance.html).

The pages show more than they used to: a role and flag counts on each course, a status and
a latest week on each team, claim counts on each week, a share chart on the team page, and
stat tiles and a week switcher on the summary page. These check the numbers and labels, not
the look.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag
from summaries.models import Claim, Summary
from summaries.tests.week_shim import sprint_for_week

from .sprint_helpers import flaggable_sprint, sprint_for

WEEK = date(2026, 10, 26)
EARLIER_WEEK = date(2026, 10, 19)


def make_user(login, first="", last=""):
    return get_user_model().objects.create_user(
        username=login, password="not-a-real-password", github_login=login,
        first_name=first, last_name=last,
    )


def make_claim(summary, significance="minor", requirement_ids=(1,), status="verified"):
    return Claim.objects.create(
        summary=summary, text="Did some work.", category="feature", artifact_ids=[],
        requirement_ids=list(requirement_ids), significance=significance, status=status,
    )


class BalanceFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        Enrollment.objects.create(user=cls.instructor, course=cls.course, role="instructor")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        sprint_for_week(cls.project, WEEK).requirements.create(number=1, text="Users can log in.")
        cls.alpha = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.ada = make_user("sample-ada", "Ada", "Sample")
        cls.grace = make_user("sample-grace", "Grace", "Example")
        for student in (cls.ada, cls.grace):
            Enrollment.objects.create(user=student, course=cls.course, role="student")
            TeamMembership.objects.create(user=student, team=cls.alpha)

    def setUp(self):
        self.client.force_login(self.instructor)

    def summary(self, user, week=WEEK, artifacts=1, team=None):
        team = team or self.alpha
        return Summary.objects.create(
            team=team,
            user=user,
            sprint=sprint_for_week(team.project, week),
            artifact_count=artifacts,
        )

    def get(self, name, *args):
        return self.client.get(reverse(f"dashboard:{name}", args=args)).content.decode()


class CoursePickerTests(BalanceFixture):
    def test_each_course_shows_the_users_role_and_its_team_count(self):
        other = Course.objects.create(name="CS 3240, Fall 2026")
        Enrollment.objects.create(user=self.instructor, course=other, role="ta")
        Team.objects.create(project=Project.objects.create(course=other, name="P"), name="T1")
        Team.objects.create(project=Project.objects.get(course=other), name="T2")
        Team.objects.create(project=Project.objects.get(course=other), name="T3")

        content = self.get("home")

        self.assertIn("Instructor · 1 team<", content)
        self.assertIn("TA · 3 teams<", content)

    def test_each_course_counts_its_flagged_and_warned_teams(self):
        other = Course.objects.create(name="CS 3240, Fall 2026")
        Enrollment.objects.create(user=self.instructor, course=other, role="ta")
        beta = Team.objects.create(project=self.project, name="Team Beta")
        TeamMembership.objects.create(user=make_user("sample-beta"), team=beta)
        Flag.objects.create(
            team=self.alpha, rule="r", reason="x", severity="flag",
            sprint=flaggable_sprint(self.alpha),
        )
        Flag.objects.create(
            team=beta, rule="r", reason="x", severity="warning", sprint=flaggable_sprint(beta)
        )
        Team.objects.create(project=Project.objects.create(course=other, name="P"), name="T1")

        content = self.get("home")

        self.assertIn("1 flag</span>", content)
        self.assertIn("1 warning</span>", content)
        self.assertEqual(content.count("No flags</span>"), 1)  # The other course.

    def test_a_team_with_both_a_flag_and_a_warning_counts_only_as_flagged(self):
        other = Course.objects.create(name="CS 3240, Fall 2026")
        Enrollment.objects.create(user=self.instructor, course=other, role="ta")
        for severity in ("flag", "warning"):
            Flag.objects.create(
                team=self.alpha, rule="r", reason="x", severity=severity,
                sprint=flaggable_sprint(self.alpha),
            )

        content = self.get("home")

        self.assertIn("1 flag</span>", content)
        self.assertNotIn("warning</span>", content)


class OverviewCardTests(BalanceFixture):
    def card(self, team):
        content = self.get("course_overview", self.course.id)
        at = content.index(f"<strong>{team.name}</strong>")
        return content[content.rindex("<article", 0, at) : content.index("</article>", at)]

    def test_a_team_with_summaries_and_no_flags_says_so(self):
        # A sprint's status counts once every student has a complete summary for it.
        self.summary(self.ada)
        self.summary(self.grace)

        self.assertIn("No flags</span>", self.card(self.alpha))

    def test_a_team_nothing_has_been_generated_for_says_so(self):
        card = self.card(self.alpha)

        self.assertIn("Not generated</span>", card)
        self.assertNotIn("No flags", card)
        self.assertIn("No summaries yet", card)

    def test_a_card_shows_the_teams_latest_sprint(self):
        self.summary(self.ada, week=EARLIER_WEEK)
        self.summary(self.grace, week=WEEK)

        self.assertIn("Latest sprint: Week of Oct 26", self.card(self.alpha))

    def test_neither_extra_status_uses_the_flag_marker(self):
        # Only a real flag or warning is a <mark>, so one can't be mistaken for a status.
        self.summary(self.ada)

        self.assertNotIn("<mark>", self.card(self.alpha))


class TeamPageTests(BalanceFixture):
    def test_a_week_shows_how_many_verified_claims_it_has(self):
        summary = self.summary(self.ada)
        make_claim(summary)
        make_claim(summary)
        make_claim(summary, status="couldnt_verify")

        self.assertIn("2 claims", self.get("team", self.alpha.id))

    def test_a_week_with_one_claim_is_singular(self):
        make_claim(self.summary(self.ada))

        content = self.get("team", self.alpha.id)

        self.assertIn("1 claim<", content)
        self.assertNotIn("1 claims", content)

    def test_a_week_with_no_artifacts_says_no_activity(self):
        self.summary(self.ada, artifacts=0)

        self.assertIn("no activity", self.get("team", self.alpha.id))

    def test_the_header_shows_the_teams_status(self):
        self.assertNotIn('class="tag', self.get("team", self.alpha.id))  # Nothing generated yet.
        sprint = self.summary(self.ada).sprint
        self.summary(self.grace)
        self.assertIn('<span class="tag ok">No flags</span>', self.get("team", self.alpha.id))
        Flag.objects.create(team=self.alpha, rule="r", reason="x", severity="flag", sprint=sprint)
        self.assertIn('<span class="tag flag">Flag</span>', self.get("team", self.alpha.id))

    def give_shares(self):
        # Ada 3 of 4 points and Grace 1 of 4, in a sprint both have a summary for.
        sprint = sprint_for(self.project)
        make_claim(Summary.objects.create(team=self.alpha, user=self.ada, sprint=sprint), "major")
        make_claim(Summary.objects.create(team=self.alpha, user=self.grace, sprint=sprint), "minor")

    def test_the_chart_shows_each_students_share_and_the_equal_share(self):
        self.give_shares()

        content = self.get("team", self.alpha.id)

        self.assertIn("An equal share is 50% (2 students).", content)
        self.assertIn('<span class="val">75%', content)
        self.assertIn('<span class="val">25%', content)

    def test_there_is_no_chart_until_there_is_something_to_compare(self):
        self.summary(self.ada)

        self.assertNotIn("An equal share", self.get("team", self.alpha.id))

    def test_the_chart_comes_after_the_students_in_the_page(self):
        # Students are found by their first appearance, so the chart's names must come later.
        # CSS shows the chart first.
        self.give_shares()

        content = self.get("team", self.alpha.id)

        self.assertLess(content.index("Summaries by student"), content.index('class="chart"'))

    def test_a_flagged_students_slice_is_red(self):
        self.give_shares()
        Flag.objects.create(
            team=self.alpha, user=self.grace, rule="r", reason="x", severity="flag",
            sprint=sprint_for(self.project),
        )

        self.assertIn("fill: var(--flag)", self.get("team", self.alpha.id))


class SummaryPageTests(BalanceFixture):
    def test_the_tiles_count_verified_claims_and_those_tied_to_a_requirement(self):
        summary = self.summary(self.ada)
        make_claim(summary)
        make_claim(summary, requirement_ids=())
        make_claim(summary, requirement_ids=())

        content = self.get("summary", summary.id)

        self.assertIn("<b>3</b><span>Verified claims</span>", content)
        self.assertIn("<b>1</b><span>Tied to a requirement</span>", content)

    def test_claims_that_could_not_be_verified_are_not_in_the_tiles(self):
        summary = self.summary(self.ada)
        make_claim(summary)
        make_claim(summary, status="couldnt_verify")

        content = self.get("summary", summary.id)

        self.assertIn("<b>1</b><span>Verified claims</span>", content)
        self.assertIn("1 couldn't be verified.", content)

    def test_a_summary_with_no_claims_shows_no_tiles(self):
        summary = self.summary(self.ada, artifacts=0)

        content = self.get("summary", summary.id)

        self.assertNotIn("Verified claims", content)
        self.assertIn("No activity in this sprint.", content)

    def test_the_sprint_switcher_lists_this_students_sprints_and_marks_the_current_one(self):
        earlier, current = self.summary(self.ada, week=EARLIER_WEEK), self.summary(self.ada)
        graces = self.summary(self.grace, week=date(2026, 10, 12))  # Another student's sprint.

        content = self.get("summary", current.id)

        earlier_url = reverse("dashboard:summary", args=[earlier.id])
        self.assertIn(f'href="{earlier_url}">Week of Oct 19<', content)
        self.assertIn('aria-current="page">Week of Oct 26 · Oct 26, 2026 – Nov 1, 2026<', content)
        self.assertNotIn(f'href="{reverse("dashboard:summary", args=[graces.id])}"', content)

    def test_significance_is_drawn_as_one_two_or_three_marks(self):
        summary = self.summary(self.ada)
        for significance in ("minor", "moderate", "major"):
            make_claim(summary, significance)

        content = self.get("summary", summary.id)

        self.assertEqual(content.count('<i class="on"></i><i></i><i></i>'), 1)
        self.assertEqual(content.count('<i class="on"></i><i class="on"></i><i></i>'), 1)
        self.assertEqual(content.count('<i class="on"></i><i class="on"></i><i class="on"></i>'), 1)
