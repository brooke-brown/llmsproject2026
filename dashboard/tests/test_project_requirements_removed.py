"""Tests for T-21 (#125), part 2: the dashboard reaches requirements through sprints only.

The alpha's project-wide requirements page is gone; each sprint's page manages its own. The
course overview's Requirements rail shows the requirements of the sprint the overview is on.
Sprint 2 is the default there, being the latest that has ended (see test_overview_sprint).
"""

from django.urls import NoReverseMatch, reverse

from core.services import add_sprint_requirement

from .test_overview_sprint import OverviewSprintFixture


class ProjectRequirementsPageIsGoneTests(OverviewSprintFixture):
    def test_its_urls_are_removed(self):
        for name in ("project_requirements", "requirements_import", "requirement_add"):
            with self.assertRaises(NoReverseMatch, msg=name):
                reverse(f"dashboard:{name}", args=[self.project.id])

    def test_editing_and_deleting_a_requirement_still_work_from_its_sprint(self):
        requirement = add_sprint_requirement(self.sprint2, "Log in")
        self.client.force_login(self.instructor)
        response = self.client.post(
            reverse("dashboard:requirement_edit", args=[requirement.id]), {"text": "Sign in"}
        )
        self.assertRedirects(response, reverse("dashboard:sprint", args=[self.sprint2.id]))
        requirement.refresh_from_db()
        self.assertEqual(requirement.text, "Sign in")


class OverviewRequirementsRailTests(OverviewSprintFixture):
    def setUp(self):
        super().setUp()
        add_sprint_requirement(self.sprint1, "Sprint one requirement")
        add_sprint_requirement(self.sprint2, "Sprint two requirement")

    def rail(self, *sprints):
        section = self.project_section(self.content(*sprints), self.project)
        start = section.index('<aside class="rail">')
        return section[start : section.index("</aside>", start)]

    def test_shows_the_overview_sprints_requirements(self):
        rail = self.rail()
        self.assertIn("Sprint two requirement", rail)
        self.assertNotIn("Sprint one requirement", rail)

    def test_names_the_sprint_they_belong_to(self):
        self.assertIn("Sprint 2", self.rail())

    def test_follows_the_sprint_chosen_in_the_menu(self):
        rail = self.rail(self.sprint1)
        self.assertIn("Sprint one requirement", rail)
        self.assertNotIn("Sprint two requirement", rail)

    def test_links_to_that_sprints_page_to_edit_them(self):
        self.assertIn(reverse("dashboard:sprint", args=[self.sprint1.id]), self.rail(self.sprint1))

    def test_a_sprint_with_no_requirements_says_so(self):
        rail = self.rail(self.sprint3)
        self.assertNotIn("<table>", rail)
        self.assertIn("No requirements", rail)
