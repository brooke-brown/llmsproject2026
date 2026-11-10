"""Tests for US-24 (#88): a team's contribution shares as a pie chart.

The team page draws an inline SVG pie, one slice per student with a share above 0%, and a
legend beside it that lists every student. A flagged student's slice uses the theme's
`--flag` colour and a warned student's uses `--warn`.
"""

import math
import re
import xml.etree.ElementTree as ET
from datetime import date

from django.test import TestCase

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag
from summaries.models import Summary

from .test_course_overview import enroll, make_user, team_url
from .sprint_helpers import give, sprint_for

COLOUR = re.compile(r"var\(--[a-z0-9-]+\)")
PATH = re.compile(r"M50 50 L(\S+) (\S+) A50 50 0 ([01]) 1 (\S+) (\S+) Z")


def pie_of(content):
    """The chart's <svg> as an element, or None if the page has no chart."""
    match = re.search(r'<svg[^>]*class="pie"[^>]*>.*?</svg>', content, re.DOTALL)
    return ET.fromstring(match.group(0)) if match else None


def slices_of(pie):
    return pie.findall(".//*[@class='slice']")


def colour_of(element):
    return COLOUR.search(element.get("style", "")).group(0)


def legend_rows(content):
    return re.findall(r'<li class="legend-row"[^>]*>(.*?)</li>', content, re.DOTALL)


def row_text(row):
    return " ".join(re.sub(r"<[^>]+>", " ", row).split())


def bearing(x, y):
    """Clockwise angle from 12 o'clock, in degrees, of a point on a pie centred at (50, 50)."""
    return math.degrees(math.atan2(x - 50, 50 - y)) % 360


class PieFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Enrollment.Role.INSTRUCTOR)
        project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=project, name="Team Alpha")

    def make_students(self, *work):
        """One student per entry, "Student N", given that list of claim significances.
        An empty entry is a student with a summary and no requirement-tied work."""
        students = []
        for number, significances in enumerate(work, start=1):
            student = make_user(f"sample-{self.team.id}-{number}", f"Student{number}", "Test")
            enroll(student, self.course, Enrollment.Role.STUDENT)
            TeamMembership.objects.create(user=student, team=self.team)
            give(student, self.team, *significances)
            students.append(student)
        return students

    def flag(self, student, severity):
        Flag.objects.create(
            team=self.team, user=student, rule="r", reason="x", severity=severity,
            sprint=sprint_for(self.team.project),
        )

    def page(self):
        self.client.force_login(self.instructor)
        return self.client.get(team_url(self.team.id)).content.decode()


class ChartTests(PieFixture):
    def test_one_slice_per_student_with_a_share_above_zero(self):
        self.make_students(["major"], ["minor"], [])

        self.assertEqual(len(slices_of(pie_of(self.page()))), 2)

    def test_slices_are_drawn_from_the_exact_shares_so_they_fill_the_circle(self):
        # Three equal shares round to 33% each, 99% in all. The slices still meet exactly.
        self.make_students(["major"], ["major"], ["major"])

        paths = [PATH.fullmatch(s.get("d")) for s in slices_of(pie_of(self.page()))]

        self.assertTrue(all(paths))
        starts = [bearing(float(p[1]), float(p[2])) for p in paths]
        ends = [bearing(float(p[4]), float(p[5])) for p in paths]
        self.assertAlmostEqual(starts[0], 0, delta=0.1)
        for end, next_start in zip(ends, starts[1:]):
            self.assertAlmostEqual(end, next_start, delta=0.1)
        self.assertAlmostEqual(ends[-1] % 360, 0, delta=0.1)
        for start, end in zip(starts, ends):
            self.assertAlmostEqual((end - start) % 360, 120, delta=0.1)

    def test_a_slice_over_half_the_circle_takes_the_large_arc(self):
        self.make_students(["major"], ["minor"])  # 75% and 25%.

        large, small = [PATH.fullmatch(s.get("d"))[3] for s in slices_of(pie_of(self.page()))]

        self.assertEqual((large, small), ("1", "0"))

    def test_a_student_with_all_the_work_is_a_full_circle(self):
        self.make_students(["major"], [])

        (only,) = slices_of(pie_of(self.page()))

        self.assertEqual(only.tag, "circle")
        self.assertEqual(float(only.get("r")), 50)

    def test_no_chart_when_there_is_nothing_to_compare(self):
        self.make_students([], [])

        self.assertIsNone(pie_of(self.page()))
        self.assertEqual(legend_rows(self.page()), [])

    def test_the_old_bar_chart_is_gone(self):
        self.make_students(["major"], ["minor"])

        content = self.page()

        for leftover in ["bar-row", 'class="track"', 'class="fill', 'class="axis"']:
            self.assertNotIn(leftover, content)


class LegendTests(PieFixture):
    def test_names_and_percentages_are_not_drawn_on_the_chart(self):
        self.make_students(["major"], ["minor"])

        svg = ET.tostring(pie_of(self.page()), encoding="unicode")

        self.assertNotIn("<text", svg)

    def test_lists_every_student_largest_share_first_in_slice_order(self):
        self.make_students(["minor"], [], ["major"], ["moderate"])

        rows = [row_text(row) for row in legend_rows(self.page())]
        titles = [s.find("title").text for s in slices_of(pie_of(self.page()))]

        self.assertEqual(
            rows,
            [
                "Student3 Test 50%",
                "Student4 Test 33%",
                "Student1 Test 17%",
                "Student2 Test 0%",
            ],
        )
        self.assertEqual(titles, ["Student3 Test: 50%", "Student4 Test: 33%", "Student1 Test: 17%"])

    def test_each_row_has_the_colour_of_its_slice(self):
        self.make_students(["major"], ["moderate"], ["minor"])

        content = self.page()
        slice_colours = [colour_of(s) for s in slices_of(pie_of(content))]
        swatches = [COLOUR.search(row).group(0) for row in legend_rows(content)]

        self.assertEqual(swatches, slice_colours)

    def test_a_flagged_or_warned_student_has_a_tag_in_their_row(self):
        first, second, third = self.make_students(["major"], ["moderate"], ["minor"])
        self.flag(second, "warning")
        self.flag(third, "flag")

        rows = [row_text(row) for row in legend_rows(self.page())]

        self.assertEqual(rows, ["Student1 Test 50%", "Student2 Test 33% Warning", "Student3 Test 17% Flag"])

    def test_a_student_at_zero_is_listed_with_their_flag(self):
        first, second = self.make_students(["major"], [])
        self.flag(second, "flag")

        rows = [row_text(row) for row in legend_rows(self.page())]

        self.assertEqual(rows, ["Student1 Test 100%", "Student2 Test 0% Flag"])

    def test_a_name_links_to_the_students_newest_summary(self):
        first, second = self.make_students(["major"], ["minor"])
        sprint_2 = sprint_for(
            self.team.project, "Sprint 2", start=date(2026, 11, 2), end=date(2026, 11, 8)
        )
        newest = Summary.objects.create(team=self.team, user=first, sprint=sprint_2)

        row = legend_rows(self.page())[0]

        self.assertIn(f'href="/summaries/{newest.id}/"', row)

    def test_hovering_a_slice_shows_the_name_and_percentage(self):
        self.make_students(["major"], ["minor"])

        titles = [s.find("title").text for s in slices_of(pie_of(self.page()))]

        self.assertEqual(titles, ["Student1 Test: 75%", "Student2 Test: 25%"])

    def test_a_team_of_twelve_has_a_slice_and_a_row_for_each_student(self):
        self.make_students(*[["minor"]] * 12)

        content = self.page()

        self.assertEqual(len(slices_of(pie_of(content))), 12)
        self.assertEqual(len(legend_rows(content)), 12)


class FlaggedColourTests(PieFixture):
    def test_a_flagged_student_is_red_and_a_warned_student_is_amber(self):
        flagged, warned, fine = self.make_students(["minor"], ["moderate"], ["major"])
        self.flag(flagged, "flag")
        self.flag(warned, "warning")

        colours = [colour_of(s) for s in slices_of(pie_of(self.page()))]

        # Largest first: fine, warned, flagged.
        self.assertEqual(colours[1:], ["var(--warn)", "var(--flag)"])
        self.assertNotIn(colours[0], ["var(--flag)", "var(--warn)"])

    def test_a_student_with_a_flag_and_a_warning_is_red(self):
        student, other = self.make_students(["minor"], ["major"])
        self.flag(student, "warning")
        self.flag(student, "flag")

        colours = [colour_of(s) for s in slices_of(pie_of(self.page()))]

        self.assertEqual(colours[1], "var(--flag)")

    def test_no_other_slice_is_red_or_amber_and_neighbours_differ(self):
        students = self.make_students(*[["minor"]] * 9)
        self.flag(students[3], "flag")

        colours = [colour_of(s) for s in slices_of(pie_of(self.page()))]

        self.assertEqual(colours.count("var(--flag)"), 1)
        self.assertNotIn("var(--warn)", colours)
        for colour, neighbour in zip(colours, colours[1:] + colours[:1]):
            self.assertNotEqual(colour, neighbour)

    def test_neighbours_differ_for_every_team_size(self):
        for size in range(2, 14):
            with self.subTest(size=size):
                self.team = Team.objects.create(
                    project=self.team.project, name=f"Team of {size}"
                )
                self.make_students(*[["minor"]] * size)

                colours = [colour_of(s) for s in slices_of(pie_of(self.page()))]

                for colour, neighbour in zip(colours, colours[1:] + colours[:1]):
                    self.assertNotEqual(colour, neighbour)


class EqualShareNoteTests(PieFixture):
    def test_says_what_an_equal_share_is(self):
        self.make_students(["major"], ["minor"], ["minor"], [])

        self.assertIn("An equal share is 25% (4 students).", self.page())

    def test_the_existing_note_about_llm_judgments_stays(self):
        self.make_students(["major"], ["minor"])

        content = self.page()

        self.assertIn("requirement-tied work in Sprint 1 (Oct 26 – Nov 1, 2026), weighted by significance", content)
        self.assertIn("LLM judgments", content)
