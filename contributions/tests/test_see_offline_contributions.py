"""Tests for US-35 (#112): see students' offline contributions, labelled as self-reported.

These drive the real team and summary pages, so they check what an instructor sees.

The issue leaves some names and wording open. These tests expect:
- `services.contributions_for(team, student, sprint=None)`, newest work first (by date, then
  most recently added, as in US-33), each with `confirmation_count` and `confirmed_by`, the
  confirming teammates' `User.display_name`s in alphabetical order. With a sprint, only work
  dated from its first through its last day, inclusive.
- On the team page, each student's section in an element with `id="offline-<user id>"`,
  headed "Offline contributions", and each contribution in it with `id="contribution-<pk>"`.
  It lists the student's work for the whole project, whichever sprint the page shows.
- On a summary page, the section in an element with `id="offline-contributions"`, after the
  last claim, and each contribution in it with `id="contribution-<pk>"`.
- A contribution's sprints shown by name, or "No sprint", as on the student's own list.
- "Confirmed by 1 teammate" / "Confirmed by N teammates", and "Not confirmed by teammates"
  for none, never "Confirmed by 0".
- Contributions never styled as claims: no `claim`, `chip` or `llm` class.
- The team page's query count not growing with the number of contributions.
"""

from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from contributions import services
from core.models import Sprint
from dashboard.flags import compute_flags
from dashboard.tests.sprint_helpers import give

from .test_offline_contributions import OfflineContributionsTestCase, page_text

SELF_REPORTED = "Self-reported"
SUMMARY_LABEL = "Self-reported, not checked against GitHub"
NONE_RECORDED = "No offline contributions recorded"
NOT_CONFIRMED = "Not confirmed by teammates"
CLAIM_CLASSES = {"claim", "chip", "llm"}

# After Sprint 1 and Sprint 2 end (Aug 17 and Aug 24), before the frozen NOW (Sep 3).
GENERATED = datetime(2026, 8, 30, 9, 0, tzinfo=ZoneInfo("America/New_York"))
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "source", "track", "wbr"}


def team_url(team_id):
    return reverse("dashboard:team", args=[team_id])


def summary_url(summary_id):
    return reverse("dashboard:summary", args=[summary_id])


class _Element(HTMLParser):
    """Collects the text, classes and descendant IDs of the element with one ID."""

    def __init__(self, element_id):
        super().__init__(convert_charrefs=True)
        self.element_id = element_id
        self.found = False
        self.depth = 0
        self.text = []
        self.classes = set()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth:
            self.classes.update((attrs.get("class") or "").split())
            if attrs.get("id"):
                self.ids.append(attrs["id"])
            if tag not in VOID_TAGS:
                self.depth += 1
        elif not self.found and attrs.get("id") == self.element_id:
            self.found = True
            self.classes.update((attrs.get("class") or "").split())
            self.depth = 0 if tag in VOID_TAGS else 1

    def handle_endtag(self, tag):
        if self.depth and tag not in VOID_TAGS:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


class Element:
    def __init__(self, parser):
        self.text = " ".join(" ".join(parser.text).split())
        self.classes = parser.classes
        self.ids = parser.ids

    @property
    def contribution_ids(self):
        prefix = "contribution-"
        return [int(i[len(prefix):]) for i in self.ids if i.startswith(prefix)]


class SeeContributionsTestCase(OfflineContributionsTestCase):
    """The instructor is logged in. A, B and F share Team A; C is on Team B.

    Sprint 1 is Aug 10-17 and Sprint 2 Aug 17-24, sharing Aug 17. Both have ended.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sprint1 = Sprint.objects.get(project=cls.project, name="Sprint 1")
        cls.sprint2 = Sprint.objects.get(project=cls.project, name="Sprint 2")

    def setUp(self):
        super().setUp()
        self.login(self.instructor)

    def element(self, response, element_id):
        self.assertEqual(response.status_code, 200)
        parser = _Element(element_id)
        parser.feed(response.content.decode())
        self.assertTrue(parser.found, f'nothing on the page has id="{element_id}"')
        return Element(parser)

    def team_page(self, **query):
        return self.client.get(team_url(self.team.id), query)

    def student_section(self, student, response=None):
        return self.element(response or self.team_page(), f"offline-{student.id}")

    def team_row(self, contribution, response=None):
        return self.element(response or self.team_page(), f"contribution-{contribution.pk}")

    def summary_for(self, student, sprint=None, *significances):
        return give(
            student, self.team, *(significances or ("major",)),
            sprint=sprint or self.sprint1, generated=GENERATED,
        )

    def summary_page(self, summary):
        return self.client.get(summary_url(summary.id))

    def summary_section(self, summary):
        return self.element(self.summary_page(summary), "offline-contributions")


class ContributionsForTests(SeeContributionsTestCase):
    def test_it_returns_only_the_students_work_on_the_team_newest_first(self):
        older = self.make_contribution(date=date(2026, 8, 11))
        newer = self.make_contribution(date=date(2026, 8, 20))
        same_day_added_later = self.make_contribution(date=date(2026, 8, 20))
        self.make_contribution(student=self.student_b)
        self.make_contribution(team=self.rival_team)

        found = list(services.contributions_for(self.team, self.student_a))

        self.assertEqual(found, [same_day_added_later, newer, older])

    def test_each_has_its_confirmation_count_and_who_confirmed_it(self):
        contribution = self.make_contribution()
        self.confirm(contribution, self.student_f)
        self.confirm(contribution, self.student_b)
        unconfirmed = self.make_contribution(date=date(2026, 8, 11))

        found = {c.pk: c for c in services.contributions_for(self.team, self.student_a)}

        self.assertEqual(found[contribution.pk].confirmation_count, 2)
        self.assertEqual(found[contribution.pk].confirmed_by, ["student-b", "student-f"])
        self.assertEqual(found[unconfirmed.pk].confirmation_count, 0)
        self.assertEqual(found[unconfirmed.pk].confirmed_by, [])

    def test_a_sprint_keeps_only_work_dated_within_it_inclusive(self):
        before = self.make_contribution(date=date(2026, 8, 9))
        first_day = self.make_contribution(date=date(2026, 8, 10))
        last_day = self.make_contribution(date=date(2026, 8, 17))
        after = self.make_contribution(date=date(2026, 8, 18))

        found = list(services.contributions_for(self.team, self.student_a, sprint=self.sprint1))

        self.assertEqual(found, [last_day, first_day])
        self.assertNotIn(before, found)
        self.assertNotIn(after, found)

    def test_a_day_shared_by_two_sprints_is_in_both(self):
        shared = self.make_contribution(date=date(2026, 8, 17))

        for sprint in (self.sprint1, self.sprint2):
            with self.subTest(sprint=sprint.name):
                found = services.contributions_for(self.team, self.student_a, sprint=sprint)
                self.assertEqual(list(found), [shared])


class TeamPageTests(SeeContributionsTestCase):
    def test_each_student_has_an_offline_contributions_section(self):
        response = self.team_page()

        for student in (self.student_a, self.student_b, self.student_f):
            with self.subTest(student=student.username):
                section = self.student_section(student, response).text
                self.assertIn("offline contributions", section.lower())

    def test_a_section_lists_only_that_students_work_newest_first(self):
        older = self.make_contribution(date=date(2026, 8, 11))
        newer = self.make_contribution(date=date(2026, 8, 20))
        teammates = self.make_contribution(student=self.student_b)
        elsewhere = self.make_contribution(team=self.rival_team, date=date(2026, 8, 21))

        section = self.student_section(self.student_a)

        self.assertEqual(section.contribution_ids, [newer.pk, older.pk])
        self.assertNotIn(teammates.pk, section.contribution_ids)
        self.assertNotIn(elsewhere.pk, section.contribution_ids)

    def test_each_row_shows_date_sprint_category_description_hours_and_confirmers(self):
        contribution = self.make_contribution(
            category="user research", description="Interviewed two users.",
            hours=Decimal("1.50"),
        )
        self.confirm(contribution, self.student_b)

        row = self.team_row(contribution).text

        for expected in ("Aug 12, 2026", "Sprint 1", "User research", "Interviewed two users.",
                         "1.50 hours", "student-b"):
            with self.subTest(expected=expected):
                self.assertIn(expected, row)

    def test_a_day_two_sprints_share_names_both_and_a_day_outside_any_says_so(self):
        shared = self.make_contribution(date=date(2026, 8, 17))
        outside = self.make_contribution(date=date(2026, 8, 5))
        response = self.team_page()

        self.assertIn("Sprint 1, Sprint 2", self.team_row(shared, response).text)
        self.assertIn("No sprint", self.team_row(outside, response).text)

    def test_it_lists_the_whole_projects_work_whichever_sprint_is_shown(self):
        in_sprint1 = self.make_contribution(date=date(2026, 8, 12))
        in_sprint2 = self.make_contribution(date=date(2026, 8, 20))

        section = self.student_section(self.student_a, self.team_page(sprint=self.sprint2.id))

        self.assertEqual(section.contribution_ids, [in_sprint2.pk, in_sprint1.pk])

    def test_each_row_is_labelled_self_reported(self):
        contribution = self.make_contribution()

        self.assertIn(SELF_REPORTED, self.team_row(contribution).text)

    def test_the_confirmation_label_counts_teammates(self):
        none = self.make_contribution(date=date(2026, 8, 11))
        one = self.make_contribution(date=date(2026, 8, 12))
        self.confirm(one, self.student_b)
        two = self.make_contribution(date=date(2026, 8, 13))
        self.confirm(two, self.student_b)
        self.confirm(two, self.student_f)
        response = self.team_page()

        self.assertIn(NOT_CONFIRMED, self.team_row(none, response).text)
        self.assertNotIn("Confirmed by 0", self.team_row(none, response).text)
        self.assertRegex(self.team_row(one, response).text, r"Confirmed by 1 teammate\b")
        self.assertRegex(self.team_row(two, response).text, r"Confirmed by 2 teammates\b")

    def test_a_student_with_none_says_so_instead_of_an_empty_section(self):
        self.make_contribution(student=self.student_b)

        section = self.student_section(self.student_a)

        self.assertIn(NONE_RECORDED, section.text)
        self.assertEqual(section.contribution_ids, [])

    def test_a_student_with_some_doesnt_say_none_recorded(self):
        self.make_contribution()

        self.assertNotIn(NONE_RECORDED, self.student_section(self.student_a).text)

    def test_contributions_arent_styled_as_claims(self):
        self.confirm(self.make_contribution(), self.student_b)

        section = self.student_section(self.student_a)

        self.assertFalse(section.classes & CLAIM_CLASSES, section.classes & CLAIM_CLASSES)

    def test_the_query_count_doesnt_grow_with_contributions(self):
        self.confirm(self.make_contribution(), self.student_b)
        with CaptureQueriesContext(connection) as one:
            self.team_page()

        for day in (5, 13, 17, 20):
            contribution = self.make_contribution(date=date(2026, 8, day))
            self.confirm(contribution, self.student_b)
            self.confirm(contribution, self.student_f)
        self.make_contribution(student=self.student_b)
        with CaptureQueriesContext(connection) as many:
            self.team_page()

        self.assertEqual(len(many.captured_queries), len(one.captured_queries))


class SummaryPageTests(SeeContributionsTestCase):
    def test_it_shows_the_students_work_dated_within_the_summarys_sprint(self):
        summary = self.summary_for(self.student_a, self.sprint1)
        before = self.make_contribution(date=date(2026, 8, 9))
        inside = self.make_contribution(date=date(2026, 8, 12))
        last_day = self.make_contribution(date=date(2026, 8, 17))
        after = self.make_contribution(date=date(2026, 8, 20))
        teammates = self.make_contribution(student=self.student_b)

        section = self.summary_section(summary)

        self.assertEqual(section.contribution_ids, [last_day.pk, inside.pk])
        for left_out in (before, after, teammates):
            self.assertNotIn(left_out.pk, section.contribution_ids)

    def test_the_section_is_labelled_self_reported_not_checked_against_github(self):
        summary = self.summary_for(self.student_a)
        self.make_contribution()

        self.assertIn(SUMMARY_LABEL, self.summary_section(summary).text)

    def test_the_section_comes_after_the_claims(self):
        summary = self.summary_for(self.student_a, self.sprint1, "major", "minor")
        self.make_contribution()

        content = self.summary_page(summary).content.decode()

        self.assertGreater(
            content.index('id="offline-contributions"'), content.rindex('class="claim"')
        )

    def test_each_row_shows_its_details_and_confirmations(self):
        summary = self.summary_for(self.student_a)
        contribution = self.make_contribution(description="Sketched the onboarding flow.")
        self.confirm(contribution, self.student_f)
        unconfirmed = self.make_contribution(date=date(2026, 8, 11))
        response = self.summary_page(summary)

        row = self.element(response, f"contribution-{contribution.pk}").text
        for expected in ("Aug 12, 2026", "Meeting", "Sketched the onboarding flow.",
                         "1.50 hours", SELF_REPORTED, "Confirmed by 1 teammate", "student-f"):
            with self.subTest(expected=expected):
                self.assertIn(expected, row)
        self.assertIn(NOT_CONFIRMED, self.element(response, f"contribution-{unconfirmed.pk}").text)

    def test_none_in_the_sprint_says_so(self):
        summary = self.summary_for(self.student_a, self.sprint1)
        self.make_contribution(date=date(2026, 8, 20))

        section = self.summary_section(summary)

        self.assertIn(NONE_RECORDED, section.text)
        self.assertEqual(section.contribution_ids, [])

    def test_contributions_never_appear_among_the_claims(self):
        summary = self.summary_for(self.student_a, self.sprint1, "major")
        self.make_contribution(description="Ran the planning meeting.")

        response = self.summary_page(summary)
        content = page_text(response)

        self.assertEqual(len(response.context["claims"]), 1)
        self.assertEqual(content.count('class="claim"'), 1)
        claim_start = content.index('class="claim"')
        claim = content[claim_start : content.index("</article>", claim_start)]
        self.assertNotIn("Ran the planning meeting.", claim)

    def test_contributions_arent_styled_as_claims(self):
        summary = self.summary_for(self.student_a)
        self.confirm(self.make_contribution(), self.student_b)

        section = self.summary_section(summary)

        self.assertFalse(section.classes & CLAIM_CLASSES, section.classes & CLAIM_CLASSES)


class NeverCountedTests(SeeContributionsTestCase):
    """Offline work never changes shares, the pie chart or flags."""

    def give_sprint1(self, a=("major",), b=("major",), f=("major",)):
        self.summary_for(self.student_a, self.sprint1, *a)
        self.summary_for(self.student_b, self.sprint1, *b)
        self.summary_for(self.student_f, self.sprint1, *f)

    def pile_of_confirmed_work(self, student):
        for day in range(10, 18):
            contribution = self.make_contribution(
                student=student, date=date(2026, 8, day), hours=Decimal("8")
            )
            for teammate in (self.student_a, self.student_b, self.student_f):
                if teammate != student:
                    self.confirm(contribution, teammate)

    def shares_and_pie(self):
        response = self.team_page(sprint=self.sprint1.id)
        memberships = response.context["memberships"]
        shares = {m.user_id: m.contribution_share for m in memberships}
        pie = [(m.user_id, m.pie and m.pie.get("path")) for m in response.context["legend"]]
        return shares, pie

    def flags(self):
        return sorted(
            (f.user_id, f.rule, f.severity, f.sprint_id) for f in compute_flags(self.team)
        )

    def test_shares_and_the_pie_chart_ignore_offline_work(self):
        self.give_sprint1(f=("minor",))
        shares_before, pie_before = self.shares_and_pie()
        self.assertTrue(shares_before[self.student_f.id])

        self.pile_of_confirmed_work(self.student_f)

        self.assertEqual(self.shares_and_pie(), (shares_before, pie_before))

    def test_offline_work_doesnt_clear_a_flag(self):
        self.give_sprint1(a=("major", "major"), b=("major", "major"), f=("minor",))
        before = self.flags()
        self.assertIn(self.student_f.id, [user_id for user_id, *_ in before])

        self.pile_of_confirmed_work(self.student_f)

        self.assertEqual(self.flags(), before)

    def test_offline_work_doesnt_raise_a_flag(self):
        self.give_sprint1()
        self.assertEqual(self.flags(), [])

        self.pile_of_confirmed_work(self.student_a)

        self.assertEqual(self.flags(), [])


class AccessTests(SeeContributionsTestCase):
    def test_the_courses_instructors_and_tas_see_them(self):
        contribution = self.make_contribution()
        summary = self.summary_for(self.student_a)

        for user in (self.instructor, self.ta):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertIn(contribution.pk, self.student_section(self.student_a).contribution_ids)
                self.assertIn(contribution.pk, self.summary_section(summary).contribution_ids)

    def test_everyone_else_gets_a_403(self):
        self.make_contribution()
        summary = self.summary_for(self.student_a)

        for user in (self.student_a, self.student_b, self.student_c, self.outsider):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.team_page().status_code, 403)
                self.assertEqual(self.summary_page(summary).status_code, 403)


class ReviewFixTests(SeeContributionsTestCase):
    """Fixes from the #156 review.

    - The team page's list covers every sprint, unlike the rest of the page, which follows the
      sprint menu, so it says "all sprints".
    - A summary written before its sprint ended only lists offline work up to the day its
      claims cover, and says so, as `contributions_for(..., through=<date>)`.
    """

    def test_the_team_pages_list_says_it_covers_all_sprints(self):
        self.make_contribution()

        section = self.student_section(self.student_a, self.team_page(sprint=self.sprint1.id))

        self.assertIn("all sprints", section.text.lower())

    def test_contributions_for_can_stop_at_a_day(self):
        kept = self.make_contribution(date=date(2026, 8, 13))
        self.make_contribution(date=date(2026, 8, 14))

        found = services.contributions_for(
            self.team, self.student_a, sprint=self.sprint1, through=date(2026, 8, 13)
        )

        self.assertEqual(list(found), [kept])

    def test_a_partial_summary_lists_only_work_up_to_the_day_it_covers(self):
        # Written on Aug 13, during Sprint 1 (Aug 10-17), so it covers Aug 10-13.
        summary = give(
            self.student_a, self.team, "major", sprint=self.sprint1,
            generated=datetime(2026, 8, 13, 15, 0, tzinfo=ZoneInfo("America/New_York")),
        )
        covered = self.make_contribution(date=date(2026, 8, 13))
        later = self.make_contribution(date=date(2026, 8, 15))

        section = self.summary_section(summary)

        self.assertEqual(section.contribution_ids, [covered.pk])
        self.assertNotIn(later.pk, section.contribution_ids)
        self.assertIn("through Aug 13", section.text)

    def test_a_complete_summary_doesnt_say_through(self):
        summary = self.summary_for(self.student_a)
        self.make_contribution()

        self.assertNotIn("through", self.summary_section(summary).text)
