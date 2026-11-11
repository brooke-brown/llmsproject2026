"""Tests for US-46 (#136): see the course overview for a chosen sprint.

Each project on the overview has a sprint menu, a GET form, defaulting to the latest sprint
that has ended. Teams are labelled and ordered by that sprint's flags, then its open disputes,
then the rest. A running sprint shows "in progress" and raises no flag, and each team's link
carries the selected sprint to the team page.

The clock is fixed at Tuesday Nov 10, 2026, noon in New York (see test_sprint_flags). Sprint 1
(Oct 12-25) and Sprint 2 (Oct 26-Nov 1) have ended; Sprint 3 (Nov 2-15) is still running.
"""

from datetime import date, datetime
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Course, Enrollment, Project, Sprint, Team, TeamMembership
from dashboard import services
from dashboard.models import Flag, FlagDispute

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_show_flags import article_containing
from .test_sprint_flags import DURING_SPRINT_2, NOW, RULE, SprintFlagFixture

Role = Enrollment.Role


class OverviewSprintFixture(SprintFlagFixture):
    def overview(self, *sprints, user=None, raw=None):
        """The overview, choosing each of `sprints`, or the raw `sprint` value `raw`."""
        self.client.force_login(user or self.instructor)
        values = [sprint.id for sprint in sprints]
        if raw is not None:
            values.append(raw)
        return self.client.get(overview_url(self.course.id), {"sprint": values})

    def content(self, *sprints, **kwargs):
        return self.overview(*sprints, **kwargs).content.decode()

    def card(self, team, *sprints):
        return article_containing(self.content(*sprints), f">{team.name}<")

    def project_section(self, content, project):
        start = content.index(f'id="project-{project.id}"')
        end = content.find("</section>", start)
        return content[start:end]

    def selected(self, content, project):
        """The sprint ID selected in the project's sprint menu, or None."""
        section = self.project_section(content, project)
        marker = " selected>"
        at = section.find(marker, section.find('name="sprint" id="overview-sprint'))
        if at == -1:
            return None
        value = section.rindex('value="', 0, at) + len('value="')
        return int(section[value : section.index('"', value)])

    def flag(self, team, student, sprint, severity=Flag.Severity.FLAG):
        return Flag.objects.create(
            team=team, user=student, rule=RULE, reason="Low share.", severity=severity,
            sprint=sprint,
        )

    def dispute(self, team, student, sprint, status=FlagDispute.Status.OPEN):
        return FlagDispute.objects.create(
            team=team, student=student, rule=RULE, sprint=sprint, flag_reason="Low share.",
            severity=Flag.Severity.FLAG, explanation="I paired on most of it.", status=status,
        )

    def team_order(self, *sprints):
        content = self.content(*sprints)
        names = ["Team Alpha", "Team Pair", "Team Quiet"]
        found = [(content.index(f">{name}<"), name) for name in names if f">{name}<" in content]
        return [name for _, name in sorted(found)]

    def give_sprint1_flag(self):
        """Sprint 1, where Ada did a seventh of the Pair's work: a flag."""
        self.give(self.ada, self.pair, self.sprint1, "minor")
        self.give(self.grace, self.pair, self.sprint1, "major", "major")

    def give_sprint2_balanced(self):
        self.give(self.ada, self.pair, self.sprint2, "major")
        self.give(self.grace, self.pair, self.sprint2, "major")


class SprintMenuTests(OverviewSprintFixture):
    def test_each_project_has_a_sprint_menu_as_a_get_form(self):
        section = self.project_section(self.content(), self.project)

        select = section.index('<select name="sprint" id="overview-sprint')
        form = section[section.rindex("<form", 0, select) : section.index("</form>", select)]
        self.assertIn('method="get"', form)
        self.assertIn(f'action="{overview_url(self.course.id)}"', form)
        self.assertIn('<button type="submit"', form)
        for sprint in (self.sprint1, self.sprint2, self.sprint3):
            self.assertIn(f'<option value="{sprint.id}"', form)

    def test_the_menu_lists_only_started_sprints_newest_first(self):
        later = Sprint.objects.create(
            project=self.project, name="Sprint 4", start_date=date(2026, 11, 16),
            end_date=date(2026, 11, 22),
        )

        content = self.content()

        self.assertNotIn(f'<option value="{later.id}"', content)
        self.assertLess(
            content.index(f'<option value="{self.sprint3.id}"'),
            content.index(f'<option value="{self.sprint1.id}"'),
        )

    def test_it_defaults_to_the_latest_sprint_that_has_ended(self):
        self.assertEqual(self.selected(self.content(), self.project), self.sprint2.id)

    def test_a_running_sprint_with_summaries_isnt_the_default(self):
        self.give(self.ada, self.pair, self.sprint3, "major", generated=NOW)
        self.give(self.grace, self.pair, self.sprint3, "major", generated=NOW)

        self.assertEqual(self.selected(self.content(), self.project), self.sprint2.id)

    def test_with_no_ended_sprint_it_defaults_to_the_running_one(self):
        Sprint.objects.filter(pk__in=[self.sprint1.pk, self.sprint2.pk]).delete()

        self.assertEqual(self.selected(self.content(), self.project), self.sprint3.id)

    def test_choosing_a_sprint_shows_it(self):
        self.assertEqual(self.selected(self.content(self.sprint1), self.project), self.sprint1.id)

    def test_the_running_sprint_is_marked_in_progress_in_the_menu(self):
        section = self.project_section(self.content(), self.project)

        option = section[section.index(f'<option value="{self.sprint3.id}"') :]
        self.assertIn("in progress", option[: option.index("</option>")])
        option = section[section.index(f'<option value="{self.sprint2.id}"') :]
        self.assertNotIn("in progress", option[: option.index("</option>")])

    def test_a_sprint_that_hasnt_started_falls_back_to_the_default(self):
        later = Sprint.objects.create(
            project=self.project, name="Sprint 4", start_date=date(2026, 11, 16),
            end_date=date(2026, 11, 22),
        )

        self.assertEqual(self.selected(self.content(later), self.project), self.sprint2.id)

    def test_a_bad_sprint_value_falls_back_to_the_default(self):
        for raw in ["nope", "999999", ""]:
            with self.subTest(raw=raw):
                response = self.overview(raw=raw)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.selected(response.content.decode(), self.project),
                                 self.sprint2.id)


class SeveralProjectsTests(OverviewSprintFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.project2 = Project.objects.create(course=cls.course, name="Project Two")
        cls.p2_sprint1 = Sprint.objects.create(
            project=cls.project2, name="P2 Sprint 1", start_date=date(2026, 10, 19),
            end_date=date(2026, 10, 25),
        )
        cls.p2_sprint2 = Sprint.objects.create(
            project=cls.project2, name="P2 Sprint 2", start_date=date(2026, 10, 26),
            end_date=date(2026, 11, 1),
        )

    def test_each_project_chooses_its_own_sprint(self):
        content = self.content(self.sprint1, self.p2_sprint1)

        self.assertEqual(self.selected(content, self.project), self.sprint1.id)
        self.assertEqual(self.selected(content, self.project2), self.p2_sprint1.id)

    def test_choosing_one_projects_sprint_leaves_the_others_on_their_default(self):
        content = self.content(self.sprint1)

        self.assertEqual(self.selected(content, self.project2), self.p2_sprint2.id)

    def test_a_projects_menu_keeps_the_other_projects_choices(self):
        content = self.content(self.sprint1, self.p2_sprint1)

        section = self.project_section(content, self.project)
        form = section[section.index("<form") : section.index("</form>")]
        self.assertIn(f'<input type="hidden" name="sprint" value="{self.p2_sprint1.id}">', form)
        self.assertNotIn(f'type="hidden" name="sprint" value="{self.sprint1.id}"', form)

    def test_a_sprint_from_another_project_doesnt_change_this_one(self):
        content = self.content(self.p2_sprint1)

        self.assertEqual(self.selected(content, self.project), self.sprint2.id)

    def test_a_projects_menu_doesnt_pin_the_others_defaults(self):
        # Project Two is on its default, so choosing in Project One's menu mustn't fix it in
        # the URL: it should move on when Project Two's next sprint ends.
        content = self.content(self.sprint1)

        section = self.project_section(content, self.project)
        form = section[section.index("<form") : section.index("</form>")]
        self.assertNotIn('type="hidden" name="sprint"', form)
        # Project Two's menu still keeps Project One's choice.
        section = self.project_section(content, self.project2)
        form = section[section.index("<form") : section.index("</form>")]
        self.assertIn(f'<input type="hidden" name="sprint" value="{self.sprint1.id}">', form)

    def test_more_projects_without_started_sprints_add_no_queries(self):
        self.client.force_login(self.instructor)
        before = self.overview_queries()
        for name in ("Project Three", "Project Four"):
            later = Project.objects.create(course=self.course, name=name)
            Sprint.objects.create(
                project=later, name="Sprint 1", start_date=date(2026, 11, 16),
                end_date=date(2026, 11, 22),
            )

        self.assertEqual(self.overview_queries(), before)

    def overview_queries(self):
        with CaptureQueriesContext(connection) as captured:
            self.client.get(overview_url(self.course.id))
        return len(captured)


class KeepsChosenSprintTests(OverviewSprintFixture):
    """Overview actions return to the overview with the sprints that were chosen."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.instructor)
        self.chosen = f"{overview_url(self.course.id)}?sprint={self.sprint1.id}"

    def test_the_overview_forms_carry_the_page_they_came_from(self):
        content = self.client.get(self.chosen).content.decode()

        card = article_containing(content, ">Team Pair<")
        self.assertIn(f'name="next" value="{overview_url(self.course.id)}?sprint={self.sprint1.id}"',
                      card)

    def test_generating_returns_to_the_chosen_sprint(self):
        generation = mock.Mock()
        generation.team = self.pair
        with mock.patch.object(services, "generate_team_sprint_summaries",
                               return_value=generation), \
                mock.patch.object(services, "save_run_result"):
            response = self.client.post(
                reverse("dashboard:generate_team_summaries", args=[self.pair.id]),
                {"sprint": self.sprint2.id, "next": self.chosen},
            )

        self.assertRedirects(response, self.chosen, fetch_redirect_response=False)

    def test_a_refused_run_returns_to_the_chosen_sprint(self):
        response = self.client.post(
            reverse("dashboard:generate_team_summaries", args=[self.pair.id]),
            {"sprint": "nope", "next": self.chosen},
        )

        self.assertRedirects(response, self.chosen, fetch_redirect_response=False)

    def test_retrying_with_nothing_to_retry_returns_to_the_chosen_sprint(self):
        response = self.client.post(
            reverse("dashboard:retry_failed_summaries", args=[self.pair.id]),
            {"next": self.chosen},
        )

        self.assertRedirects(response, self.chosen, fetch_redirect_response=False)

    def test_adding_a_team_returns_to_the_chosen_sprint(self):
        response = self.client.post(
            reverse("dashboard:team_create", args=[self.project.id]),
            {"name": "Team New", "next": self.chosen},
        )

        self.assertRedirects(response, self.chosen, fetch_redirect_response=False)

    def test_another_sites_next_is_ignored(self):
        response = self.client.post(
            reverse("dashboard:retry_failed_summaries", args=[self.pair.id]),
            {"next": "https://example.com/"},
        )

        self.assertRedirects(response, overview_url(self.course.id),
                             fetch_redirect_response=False)


class CoursePickerTests(OverviewSprintFixture):
    """The course picker counts teams in the sprint the overview opens on (Sprint 2)."""

    def card(self):
        return services.course_cards(self.instructor)[0]

    def test_an_earlier_sprints_flag_isnt_counted_when_the_overview_wouldnt_show_it(self):
        # Sprint 1 ended with a flag; Sprint 2 ended with Grace's summary missing. The overview
        # opens on Sprint 2 and shows no flagged team, so the picker mustn't count one.
        self.give_sprint1_flag()
        self.compute(self.pair)
        self.give(self.ada, self.pair, self.sprint2, "major")

        self.assertEqual(self.card().flagged_count, 0)
        self.assertNotIn("<mark>Flag</mark>", self.card_html())

    def card_html(self):
        return article_containing(self.content(), ">Team Pair<")

    def test_a_flag_in_the_default_sprint_is_counted(self):
        self.flag(self.pair, self.ada, self.sprint2)
        self.flag(self.team, self.ada, self.sprint2, Flag.Severity.WARNING)

        card = self.card()

        self.assertEqual(card.flagged_count, 1)
        self.assertEqual(card.warned_count, 1)
        self.assertEqual(card.team_count, 2)

    def test_a_withdrawn_flag_isnt_counted(self):
        self.flag(self.pair, self.ada, self.sprint2)
        self.dispute(self.pair, self.ada, self.sprint2, FlagDispute.Status.FLAG_WITHDRAWN)

        self.assertEqual(self.card().flagged_count, 0)


class SprintOrderingTests(OverviewSprintFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.quiet = cls.make_team("Team Quiet", cls.alan)

    def test_teams_are_labelled_by_the_selected_sprints_flags(self):
        self.give_sprint1_flag()
        self.give_sprint2_balanced()
        self.compute(self.pair)

        self.assertNotIn("<mark>Flag</mark>", self.card(self.pair))
        self.assertIn("<mark>Flag</mark>", self.card(self.pair, self.sprint1))

    def test_flagged_teams_come_first_for_the_selected_sprint(self):
        self.flag(self.pair, self.ada, self.sprint1)

        self.assertEqual(self.team_order(), ["Team Alpha", "Team Pair", "Team Quiet"])
        self.assertEqual(
            self.team_order(self.sprint1), ["Team Pair", "Team Alpha", "Team Quiet"]
        )

    def test_flags_come_before_warnings(self):
        self.flag(self.quiet, self.alan, self.sprint2, Flag.Severity.WARNING)
        self.flag(self.pair, self.ada, self.sprint2)

        content = self.content()

        self.assertEqual(self.team_order(), ["Team Pair", "Team Quiet", "Team Alpha"])
        self.assertIn("<mark>Warning</mark>", article_containing(content, ">Team Quiet<"))

    def test_open_disputes_come_after_flags_and_before_the_rest(self):
        self.flag(self.quiet, self.alan, self.sprint2)
        self.dispute(self.pair, self.ada, self.sprint2)

        self.assertEqual(self.team_order(), ["Team Quiet", "Team Pair", "Team Alpha"])
        self.assertIn("Dispute open", self.card(self.pair))

    def test_an_open_dispute_in_an_earlier_sprint_still_moves_and_tags_the_team(self):
        # The overview defaults to Sprint 2, but a decision is still waiting in Sprint 1.
        self.dispute(self.pair, self.ada, self.sprint1)

        self.assertEqual(self.team_order(), ["Team Pair", "Team Alpha", "Team Quiet"])
        self.assertIn("Dispute open: Sprint 1", self.card(self.pair))

    def test_a_dispute_in_the_selected_sprint_doesnt_name_it(self):
        self.dispute(self.pair, self.ada, self.sprint2)

        card = self.card(self.pair)

        self.assertIn("Dispute open</span>", card)
        self.assertNotIn("Dispute open:", card)

    def test_disputes_in_several_sprints_name_them_newest_first(self):
        self.dispute(self.pair, self.ada, self.sprint1)
        self.dispute(self.pair, self.grace, self.sprint2)

        self.assertIn("Dispute open: Sprint 2, Sprint 1", self.card(self.pair))

    def test_a_former_members_dispute_doesnt_count(self):
        self.dispute(self.pair, self.alan, self.sprint1)

        self.assertNotIn("Dispute open", self.card(self.pair))

    def test_a_decided_dispute_doesnt_count_as_open(self):
        self.flag(self.pair, self.ada, self.sprint2)
        self.dispute(self.pair, self.ada, self.sprint2, FlagDispute.Status.FLAG_KEPT)

        self.assertNotIn("Dispute open", self.card(self.pair))

    def test_a_withdrawn_flag_doesnt_count(self):
        self.flag(self.pair, self.ada, self.sprint2)
        self.dispute(self.pair, self.ada, self.sprint2, FlagDispute.Status.FLAG_WITHDRAWN)

        self.assertEqual(self.team_order(), ["Team Alpha", "Team Pair", "Team Quiet"])
        self.assertNotIn("<mark>Flag</mark>", self.card(self.pair))

    def test_another_sprints_flags_dont_label_the_team(self):
        self.flag(self.pair, self.ada, self.sprint1)

        self.assertNotIn("<mark>Flag</mark>", self.card(self.pair))


class SprintStatusTests(OverviewSprintFixture):
    def test_a_finished_sprint_with_no_flags_says_no_flags(self):
        self.give_sprint2_balanced()
        self.compute(self.pair)

        self.assertIn("No flags", self.card(self.pair))

    def test_a_sprint_the_team_has_no_summaries_for_says_not_generated(self):
        self.give_sprint2_balanced()
        self.compute(self.pair)

        card = self.card(self.pair, self.sprint1)

        self.assertIn("Not generated", card)
        self.assertNotIn("No flags", card)

    def test_a_running_sprint_shows_in_progress(self):
        self.give(self.ada, self.pair, self.sprint3, "minor", generated=NOW)
        self.give(self.grace, self.pair, self.sprint3, "major", "major", generated=NOW)
        self.compute(self.pair)

        card = self.card(self.pair, self.sprint3)

        self.assertIn("In progress", card)
        self.assertNotIn("<mark>Flag</mark>", card)
        self.assertNotIn("No flags", card)

    def test_an_ended_sprint_missing_a_summary_says_not_generated_not_in_progress(self):
        # Only Grace has a summary for Sprint 2, and it's complete. The team page says Ada's is
        # "Not generated", so the overview mustn't call the sprint in progress.
        self.give(self.grace, self.pair, self.sprint2, "major")

        card = self.card(self.pair)

        self.assertIn("Not generated", card)
        self.assertNotIn("In progress", card)

    def test_an_ended_sprint_with_a_partial_summary_says_in_progress(self):
        self.give(self.ada, self.pair, self.sprint2, "major", generated=DURING_SPRINT_2)
        self.give(self.grace, self.pair, self.sprint2, "major")

        self.assertIn("In progress", self.card(self.pair))

    def test_the_overview_and_team_page_agree_on_the_tag(self):
        self.give(self.grace, self.pair, self.sprint2, "major")
        self.client.force_login(self.instructor)

        team_page = self.client.get(f"{team_url(self.pair.id)}?sprint={self.sprint2.id}")
        overview = self.overview()

        self.assertEqual(team_page.context["team_status"], "none")
        pair = next(
            team for project in overview.context["projects"] for team in project.ranked_teams
            if team.id == self.pair.id
        )
        self.assertEqual(pair.sprint_status, "none")

    def test_labels_follow_the_now_given_not_the_real_clock(self):
        # The real clock is patched to Nov 10, when Sprint 2 has ended. Asked about Nov 1, its
        # last day, the sprint is still running, so even complete summaries can't say no flags.
        self.give_sprint2_balanced()
        self.compute(self.pair)
        on_last_day = datetime(2026, 11, 1, 12, 0, tzinfo=NOW.tzinfo)

        projects = services.course_projects_with_teams(
            self.course, {self.sprint2.id}, now=on_last_day
        )

        pair = next(team for team in projects[0].ranked_teams if team.id == self.pair.id)
        self.assertNotEqual(pair.sprint_status, "ok")


class NoSprintsTests(OverviewSprintFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.empty = Project.objects.create(course=cls.course, name="Project Empty")
        cls.empty_team = Team.objects.create(project=cls.empty, name="Team Empty")
        TeamMembership.objects.create(user=cls.alan, team=cls.empty_team)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)

    def test_a_project_with_no_sprints_says_so_with_a_link_to_set_one_up(self):
        section = self.project_section(self.content(), self.empty)

        self.assertIn("No sprints yet", section)
        self.assertIn(f'href="{reverse("dashboard:project_setup", args=[self.empty.id])}"',
                      section)
        self.assertNotIn('<select name="sprint" id="overview-sprint', section)

    def test_a_ta_sees_no_sprints_yet_without_a_setup_link(self):
        section = self.project_section(self.content(user=self.ta), self.empty)

        self.assertIn("No sprints yet", section)
        self.assertNotIn(reverse("dashboard:project_setup", args=[self.empty.id]), section)

    def test_a_project_whose_sprints_havent_started_says_so(self):
        Sprint.objects.create(
            project=self.empty, name="Sprint 1", start_date=date(2026, 11, 16),
            end_date=date(2026, 11, 22),
        )

        section = self.project_section(self.content(), self.empty)

        self.assertIn("No sprint has started yet", section)
        self.assertNotIn("No sprints yet", section)

    def test_its_teams_link_to_the_team_page_without_a_sprint(self):
        section = self.project_section(self.content(), self.empty)

        self.assertIn(f'href="{team_url(self.empty_team.id)}"', section)


class TeamLinkTests(OverviewSprintFixture):
    def test_each_team_link_carries_the_default_sprint(self):
        content = self.content()

        for team in (self.team, self.pair):
            self.assertIn(f'href="{team_url(team.id)}?sprint={self.sprint2.id}"', content)

    def test_each_team_link_carries_the_chosen_sprint(self):
        content = self.content(self.sprint1)

        self.assertIn(f'href="{team_url(self.pair.id)}?sprint={self.sprint1.id}"', content)
        self.assertNotIn(f'href="{team_url(self.pair.id)}?sprint={self.sprint2.id}"', content)

    def test_the_team_page_shows_the_sprint_the_link_carries(self):
        self.client.force_login(self.instructor)

        response = self.client.get(f"{team_url(self.pair.id)}?sprint={self.sprint1.id}")

        self.assertEqual(response.context["sprint"], self.sprint1)


class OtherCourseTests(OverviewSprintFixture):
    def test_a_sprint_from_another_course_is_ignored(self):
        other = Course.objects.create(name="CS 9999")
        other_project = Project.objects.create(course=other, name="Elsewhere")
        elsewhere = Sprint.objects.create(
            project=other_project, name="Sprint X", start_date=date(2026, 10, 12),
            end_date=date(2026, 10, 25),
        )

        content = self.content(elsewhere)

        self.assertEqual(self.selected(content, self.project), self.sprint2.id)
        self.assertNotIn(f'value="{elsewhere.id}"', content)
