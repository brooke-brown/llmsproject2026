"""Tests for how instructors reach the setup actions: the New project button and page, the
project setup page, editing a name in place, and confirming before anything is deleted.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from summaries.models import Summary

from .test_course_overview import enroll, make_user, overview_url, team_url

Role = Enrollment.Role


def new_project_url(course_id):
    return reverse("dashboard:project_create", args=[course_id])


def setup_url(project_id):
    return reverse("dashboard:project_setup", args=[project_id])


def project_rename_url(project_id):
    return reverse("dashboard:project_rename", args=[project_id])


def team_rename_url(team_id):
    return reverse("dashboard:team_rename", args=[team_id])


def team_create_url(project_id):
    return reverse("dashboard:team_create", args=[project_id])


def requirements_url(project_id):
    return reverse("dashboard:project_requirements", args=[project_id])


def form_for(content, action):
    """The opening tag and body of the form that posts to this URL."""
    marker = f'action="{action}"'
    start = content.index(marker)
    start = content.rindex("<form", 0, start)
    return content[start : content.index("</form>", start)]


class SetupUiFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)
        cls.ta = make_user("sample-ta")
        enroll(cls.ta, cls.course, Role.TA)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.other_project = Project.objects.create(course=cls.course, name="Project Two")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.empty_team = Team.objects.create(project=cls.project, name="Team Beta")

        cls.grace = make_user("sample-grace", "Grace", "Example")
        enroll(cls.grace, cls.course, Role.STUDENT)
        cls.membership = TeamMembership.objects.create(user=cls.grace, team=cls.team)
        cls.repo = Repo.objects.create(
            team=cls.team, url="https://github.com/acme/app", full_name="acme/app"
        )

    def setUp(self):
        self.client.force_login(self.instructor)

    def overview(self, query=""):
        return self.client.get(overview_url(self.course.id) + query)

    def messages_of(self, response):
        return " ".join(str(message) for message in response.context["messages"])


class NewProjectButtonTests(SetupUiFixture):
    def test_overview_has_a_button_and_no_name_box(self):
        response = self.overview()

        self.assertContains(response, f'<a role="button" class="primary" href="{new_project_url(self.course.id)}"')
        self.assertNotContains(response, f'action="{new_project_url(self.course.id)}"')
        self.assertNotContains(response, 'placeholder="Project name"')

    def test_the_page_asks_for_a_name_and_can_be_cancelled(self):
        response = self.client.get(new_project_url(self.course.id))

        self.assertEqual(response.status_code, 200)
        form = form_for(response.content.decode(), new_project_url(self.course.id))
        self.assertIn('name="name"', form)
        self.assertIn("csrfmiddlewaretoken", form)
        self.assertIn("Create project", form)
        self.assertContains(response, f'href="{overview_url(self.course.id)}"')

    def test_a_refused_name_stays_on_the_page_with_what_was_typed(self):
        response = self.client.post(
            new_project_url(self.course.id), {"name": "project one"}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain, [])
        self.assertIn("already has a project with this name", self.messages_of(response))
        self.assertContains(response, 'value="project one"')


class ProjectSetupPageTests(SetupUiFixture):
    def page(self, project=None):
        return self.client.get(setup_url((project or self.project).id))

    def test_overview_has_an_edit_project_link_for_each_project(self):
        response = self.overview()

        self.assertContains(response, "Edit project", count=2)
        self.assertNotContains(response, "Project setup")
        self.assertContains(response, f'href="{setup_url(self.project.id)}"')
        self.assertContains(response, f'href="{setup_url(self.other_project.id)}"')

    def test_it_lists_this_projects_teams_with_links(self):
        Team.objects.create(project=self.other_project, name="Team Elsewhere")

        response = self.page()

        self.assertContains(response, f'href="{team_url(self.team.id)}"')
        self.assertContains(response, f'href="{team_url(self.empty_team.id)}"')
        self.assertNotContains(response, "Team Elsewhere")

    def test_it_says_what_each_team_is_missing(self):
        content = self.page().content.decode()

        alpha = content[content.index("Team Alpha") : content.index("Team Beta")]
        beta = content[content.index("Team Beta") :]
        self.assertNotIn("No repo yet", alpha)
        self.assertIn("No repo yet", beta)
        self.assertIn("No students yet", beta)

    def test_it_says_when_the_project_has_no_teams(self):
        self.assertContains(self.page(self.other_project), "No teams yet")

    def test_it_links_to_add_requirements_when_there_are_none(self):
        response = self.page()

        self.assertContains(response, f'href="{requirements_url(self.project.id)}"')
        self.assertContains(response, "Add requirements")
        self.assertContains(response, "No requirements yet")

    def test_it_counts_requirements_and_links_to_edit_them(self):
        Requirement.objects.create(project=self.project, number=1, text="Log in")
        Requirement.objects.create(project=self.project, number=2, text="Log out")

        response = self.page()

        self.assertContains(response, "2 requirements")
        self.assertContains(response, "Edit requirements")

    def test_it_links_back_to_the_course(self):
        self.assertContains(self.page(), f'href="{overview_url(self.course.id)}"')

    def test_adding_a_team_there_returns_there(self):
        form = form_for(self.page().content.decode(), team_create_url(self.project.id))
        self.assertIn(f'name="next" value="{setup_url(self.project.id)}"', form)

        response = self.client.post(
            team_create_url(self.project.id),
            {"name": "Team Gamma", "next": setup_url(self.project.id)},
            follow=True,
        )

        self.assertRedirects(response, setup_url(self.project.id))
        self.assertContains(response, "Team Gamma")

    def test_a_refused_team_name_returns_there_with_the_message(self):
        response = self.client.post(
            team_create_url(self.project.id),
            {"name": "team alpha", "next": setup_url(self.project.id)},
            follow=True,
        )

        self.assertRedirects(response, setup_url(self.project.id))
        self.assertIn("already has a team with this name", self.messages_of(response))

    def test_renaming_there_returns_there(self):
        response = self.client.post(
            project_rename_url(self.project.id),
            {"name": "Renamed", "next": setup_url(self.project.id)},
        )

        self.assertRedirects(response, setup_url(self.project.id))

    def test_a_next_url_on_another_site_is_ignored(self):
        for url in ["https://example.com/", "//example.com/"]:
            with self.subTest(url=url):
                response = self.client.post(
                    team_create_url(self.project.id), {"name": "Team Gamma", "next": url}
                )

                self.assertRedirects(response, overview_url(self.course.id))
                Team.objects.filter(name="Team Gamma").delete()

    def test_team_page_links_to_it_for_instructors_only(self):
        self.assertContains(
            self.client.get(team_url(self.team.id)), f'href="{setup_url(self.project.id)}"'
        )

        self.client.force_login(self.ta)

        self.assertNotContains(self.client.get(team_url(self.team.id)), setup_url(self.project.id))

    def test_it_is_for_the_courses_instructors_only(self):
        other = make_user("sample-other")
        for user in [self.ta, self.grace, other]:
            with self.subTest(user=user.username):
                self.client.force_login(user)

                self.assertEqual(self.page().status_code, 403)

    def test_a_missing_project_is_a_404(self):
        self.assertEqual(self.client.get(setup_url(999999)).status_code, 404)


class EditProjectNameTests(SetupUiFixture):
    def setup_page(self, query=""):
        return self.client.get(setup_url(self.project.id) + query)

    def test_overview_has_no_way_to_rename(self):
        for query in ["", f"?edit={self.project.id}"]:
            with self.subTest(query=query):
                response = self.overview(query)

                self.assertNotContains(response, ">edit</a>")
                self.assertNotContains(response, project_rename_url(self.project.id))
                self.assertContains(response, "<h1>Project One</h1>")

    def test_setup_page_shows_an_edit_link_and_no_form_or_dropdown(self):
        response = self.setup_page()

        self.assertContains(response, 'href="?edit=1"')
        self.assertContains(response, ">edit</a>")
        self.assertContains(response, "<h1>Project One</h1>")
        self.assertNotContains(response, f'action="{project_rename_url(self.project.id)}"')
        self.assertNotContains(response, "<details")

    def test_edit_turns_the_name_into_a_prefilled_text_box(self):
        response = self.setup_page("?edit=1")

        form = form_for(response.content.decode(), project_rename_url(self.project.id))
        self.assertIn('value="Project One"', form)
        self.assertIn("autofocus", form)
        self.assertIn("csrfmiddlewaretoken", form)
        self.assertIn(f'name="next" value="{setup_url(self.project.id)}"', form)
        self.assertNotContains(response, "<h1>Project One</h1>")

    def test_edit_can_be_cancelled(self):
        response = self.setup_page("?edit=1")

        self.assertContains(response, f'href="{setup_url(self.project.id)}"')
        self.assertContains(response, "Cancel")


class EditTeamNameTests(SetupUiFixture):
    def test_team_page_shows_an_edit_link_and_no_form_or_dropdown(self):
        response = self.client.get(team_url(self.team.id))

        self.assertContains(response, 'href="?edit=1"')
        self.assertContains(response, ">edit</a>")
        self.assertContains(response, "<h1>Team Alpha</h1>")
        self.assertNotContains(response, f'action="{team_rename_url(self.team.id)}"')
        self.assertNotContains(response, "<details")

    def test_edit_turns_the_name_into_a_prefilled_text_box(self):
        response = self.client.get(team_url(self.team.id) + "?edit=1")

        form = form_for(response.content.decode(), team_rename_url(self.team.id))
        self.assertIn('value="Team Alpha"', form)
        self.assertIn("autofocus", form)
        self.assertNotContains(response, "<h1>Team Alpha</h1>")
        self.assertContains(response, f'href="{team_url(self.team.id)}"')

    def test_a_ta_gets_neither_the_link_nor_the_box(self):
        self.client.force_login(self.ta)

        response = self.client.get(team_url(self.team.id) + "?edit=1")

        self.assertNotContains(response, ">edit</a>")
        self.assertNotContains(response, team_rename_url(self.team.id))


class CompactFormTests(SetupUiFixture):
    def test_add_team_is_an_always_visible_compact_box(self):
        form = form_for(self.overview().content.decode(), team_create_url(self.project.id))

        self.assertIn('class="compact"', form)
        self.assertIn('placeholder="Team name"', form)


class ConfirmBeforeDeletingTests(SetupUiFixture):
    def assert_confirms(self, content, action):
        form = form_for(content, action)
        self.assertIn('onsubmit="return confirm(', form.split(">")[0])

    def test_deleting_a_team_and_removing_a_student_or_repo_ask_first(self):
        content = self.client.get(team_url(self.team.id)).content.decode()

        for action in [
            reverse("dashboard:team_delete", args=[self.team.id]),
            reverse("dashboard:membership_remove", args=[self.membership.id]),
            reverse("dashboard:repo_remove", args=[self.repo.id]),
        ]:
            with self.subTest(action=action):
                self.assert_confirms(content, action)

    def test_deleting_a_requirement_asks_first(self):
        requirement = Requirement.objects.create(project=self.project, number=1, text="Log in")

        content = self.client.get(requirements_url(self.project.id)).content.decode()

        self.assert_confirms(content, reverse("dashboard:requirement_delete", args=[requirement.id]))

    def test_deleting_a_requirement_explains_the_numbering(self):
        requirement = Requirement.objects.create(project=self.project, number=3, text="Log in")

        content = self.client.get(requirements_url(self.project.id)).content.decode()

        form = form_for(content, reverse("dashboard:requirement_delete", args=[requirement.id]))
        self.assertIn("keep their numbers", form.split(">")[0])

    def test_a_quote_in_a_name_cant_break_out_of_the_confirmation(self):
        team = Team.objects.create(project=self.project, name="Ada's \"Team\"')")

        content = self.client.get(team_url(team.id)).content.decode()

        tag = form_for(content, reverse("dashboard:team_delete", args=[team.id])).split(">")[0]
        self.assertNotIn("Ada's", tag)
        self.assertIn("Ada\\u0027s", tag)


class StaticRequirementsTests(SetupUiFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.first = Requirement.objects.create(project=cls.project, number=1, text="Log in")
        cls.second = Requirement.objects.create(project=cls.project, number=2, text="Export")

    def page(self, query=""):
        return self.client.get(requirements_url(self.project.id) + query)

    def edit_url(self, requirement):
        return reverse("dashboard:requirement_edit", args=[requirement.id])

    def test_requirements_are_static_text_with_an_edit_link_each(self):
        response = self.page()

        self.assertContains(response, "Log in</p>")
        self.assertContains(response, "Export</p>")
        self.assertContains(response, f'href="?edit={self.first.id}"')
        self.assertContains(response, f'href="?edit={self.second.id}"')
        self.assertContains(response, ">edit</a>", count=2)
        self.assertNotContains(response, f'action="{self.edit_url(self.first)}"')
        self.assertNotContains(response, "Log in</textarea>")

    def test_edit_opens_a_prefilled_box_for_that_requirement_only(self):
        response = self.page(f"?edit={self.first.id}")

        form = form_for(response.content.decode(), self.edit_url(self.first))
        self.assertIn("Log in</textarea>", form)
        self.assertIn("autofocus", form)
        self.assertIn("Save", form)
        self.assertNotContains(response, "Log in</p>")
        self.assertContains(response, "Export</p>")
        self.assertNotContains(response, f'action="{self.edit_url(self.second)}"')

    def test_edit_can_be_cancelled(self):
        response = self.page(f"?edit={self.first.id}")

        self.assertContains(response, f'href="{requirements_url(self.project.id)}"')
        self.assertContains(response, "Cancel")

    def test_a_meaningless_edit_value_shows_the_normal_page(self):
        elsewhere = Requirement.objects.create(project=self.other_project, number=1, text="Other")
        for query in ["?edit=abc", "?edit=", "?edit=999999", f"?edit={elsewhere.id}"]:
            with self.subTest(query=query):
                response = self.page(query)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, f'action="{self.edit_url(self.first)}"')
                self.assertNotContains(response, f'action="{self.edit_url(elsewhere)}"')
                self.assertContains(response, "Log in</p>")

    def test_delete_is_small_text_not_a_big_button(self):
        action = reverse("dashboard:requirement_delete", args=[self.first.id])

        form = form_for(self.page().content.decode(), action)

        self.assertIn('class="text-button"', form)

    def test_adding_a_requirement_stays_available(self):
        self.assertContains(
            self.page(),
            f'action="{reverse("dashboard:requirement_add", args=[self.project.id])}"',
        )


class DeleteProjectTests(SetupUiFixture):
    def delete_url(self, project=None):
        return reverse("dashboard:project_delete", args=[(project or self.project).id])

    def delete(self, project=None):
        return self.client.post(self.delete_url(project), follow=True)

    def add_summary(self):
        return Summary.objects.create(
            team=self.team, user=self.grace, week_start=date(2026, 10, 26), artifact_count=1
        )

    def test_setup_page_offers_it_and_asks_first(self):
        content = self.client.get(setup_url(self.project.id)).content.decode()

        form = form_for(content, self.delete_url())
        self.assertIn('onsubmit="return confirm(', form.split(">")[0])
        self.assertIn("Project One", form.split(">")[0])
        self.assertIn("Delete project", form)

    def test_overview_doesnt_offer_it(self):
        self.assertNotContains(self.overview(), self.delete_url())

    def test_a_quote_in_the_name_cant_break_out_of_the_confirmation(self):
        project = Project.objects.create(course=self.course, name="Ada's \"Project\"')")

        content = self.client.get(setup_url(project.id)).content.decode()

        tag = form_for(content, self.delete_url(project)).split(">")[0]
        self.assertNotIn("Ada's", tag)
        self.assertIn("Ada\\u0027s", tag)

    def test_it_deletes_the_project_and_returns_to_the_overview(self):
        Requirement.objects.create(project=self.project, number=1, text="Log in")

        response = self.delete()

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertIn("Deleted Project One.", self.messages_of(response))
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertFalse(Requirement.objects.filter(project_id=self.project.pk).exists())
        self.assertTrue(Project.objects.filter(pk=self.other_project.pk).exists())

    def test_it_keeps_students_accounts_and_enrollments(self):
        self.delete()

        self.assertTrue(get_user_model().objects.filter(pk=self.grace.pk).exists())
        self.assertTrue(Enrollment.objects.filter(user=self.grace, course=self.course).exists())

    def test_a_project_with_summaries_is_deleted_along_with_them(self):
        summary = self.add_summary()

        response = self.delete()

        self.assertRedirects(response, overview_url(self.course.id))
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertFalse(Summary.objects.filter(pk=summary.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.grace.pk).exists())

    def test_the_confirmation_warns_how_much_goes_with_it(self):
        self.add_summary()

        content = self.client.get(setup_url(self.project.id)).content.decode()

        tag = form_for(content, self.delete_url()).split(">")[0]
        self.assertIn("2 teams", tag)
        self.assertIn("1 summary", tag)
        self.assertIn("permanently", tag)

    def test_the_confirmation_doesnt_mention_summaries_when_there_are_none(self):
        content = self.client.get(setup_url(self.project.id)).content.decode()

        tag = form_for(content, self.delete_url()).split(">")[0]
        self.assertNotIn("summar", tag)
    def test_another_projects_summaries_dont_block_it(self):
        self.add_summary()

        self.delete(self.other_project)

        self.assertFalse(Project.objects.filter(pk=self.other_project.pk).exists())

    def test_only_this_courses_instructors_can_do_it(self):
        other = make_user("sample-other")
        for user in [self.ta, self.grace, other]:
            with self.subTest(user=user.username):
                self.client.force_login(user)

                self.assertEqual(self.client.post(self.delete_url()).status_code, 403)
                self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_anonymous_user_is_sent_to_log_in(self):
        self.client.logout()

        response = self.client.post(self.delete_url())

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_a_missing_project_is_a_404(self):
        url = reverse("dashboard:project_delete", args=[999999])

        self.assertEqual(self.client.post(url).status_code, 404)

    def test_it_refuses_get(self):
        self.assertEqual(self.client.get(self.delete_url()).status_code, 405)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_it_needs_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)

        self.assertEqual(client.post(self.delete_url()).status_code, 403)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
