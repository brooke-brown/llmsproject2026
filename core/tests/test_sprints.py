"""Tests for T-18 (#118): sprints, and moving requirements onto them.

Sprint is looked up per test rather than imported at module level, so each criterion
passes or fails on its own instead of the whole module failing on a single ImportError.

`Requirement.project` stays until T-21, so requirements made here set it as well as
`sprint`. The existing tests for requirements with no sprint are left as they are, since
the dashboard keeps creating those until US-39 moves it over.
"""

from datetime import UTC, date, datetime, timedelta
from unittest import mock

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse

from core.models import Course, Project, Requirement, User
from core.tests.test_requirements_pdf import text_pdf
from core.tests.test_seed_command import seed
from core.tests.test_split_requirements import SOURCE, SPLIT, SplitRequirementsTestCase

# 10pm on Sep 3 in New York, but already Sep 4 in UTC, so using UTC's date gets caught.
NOW = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)
TODAY = date(2026, 9, 3)
SEVENTH_DAY = date(2026, 9, 9)

LAST_MIGRATION_BEFORE_SPRINTS = ("core", "0002_repo_full_name")


def sprint_model():
    return apps.get_model("core", "Sprint")


def day(number):
    """A date in September 2026, which keeps the date tables below readable."""
    return date(2026, 9, number)


def make_project(name="Project 1"):
    course = Course.objects.create(name=f"Course for {name}")
    return Project.objects.create(course=course, name=name)


def make_sprint(project, name="Sprint 1", start=day(1), end=day(7)):
    return sprint_model().objects.create(
        project=project, name=name, start_date=start, end_date=end
    )


def make_requirement(sprint, number=1, text="Users can log in"):
    return Requirement.objects.create(
        project=sprint.project, sprint=sprint, number=number, text=text
    )


def freeze(moment=NOW):
    return mock.patch("django.utils.timezone.now", return_value=moment)


class SprintModelTests(TestCase):
    def setUp(self):
        self.project = make_project()

    def test_stores_its_project_name_and_dates(self):
        created = make_sprint(self.project, name="Sprint 3", start=day(2), end=day(8))

        sprint = sprint_model().objects.get(pk=created.pk)

        self.assertEqual(sprint.project, self.project)
        self.assertEqual(sprint.name, "Sprint 3")
        self.assertEqual(sprint.start_date, day(2))
        self.assertEqual(sprint.end_date, day(8))

    def test_dates_are_calendar_dates_not_moments(self):
        # A day is inclusive in America/New_York, so a time of day would have no meaning.
        for name in ("start_date", "end_date"):
            with self.subTest(field=name):
                field = sprint_model()._meta.get_field(name)
                self.assertIsInstance(field, models.DateField)
                self.assertNotIsInstance(field, models.DateTimeField)

    def test_a_project_can_have_no_sprints(self):
        self.assertEqual(sprint_model().objects.filter(project=self.project).count(), 0)
        self.assertEqual(self.project.sprints.count(), 0)

    def test_a_project_can_have_many_sprints_reached_through_related_name(self):
        first = make_sprint(self.project, "Sprint 1", day(1), day(7))
        second = make_sprint(self.project, "Sprint 2", day(7), day(13))
        third = make_sprint(self.project, "Sprint 3", day(13), day(19))

        self.assertCountEqual(self.project.sprints.all(), [first, second, third])

    def test_deleting_a_project_still_works_once_it_has_sprints(self):
        # The dashboard's delete_project relies on this.
        sprint = make_sprint(self.project)
        make_requirement(sprint)

        self.project.delete()

        self.assertFalse(sprint_model().objects.exists())
        self.assertFalse(Requirement.objects.exists())


class SprintCleanTestCase(TestCase):
    def setUp(self):
        self.project = make_project()

    def unsaved(self, name="New sprint", start=day(1), end=day(7), project=None):
        return sprint_model()(
            project=project or self.project, name=name, start_date=start, end_date=end
        )

    def assert_refused(self, sprint):
        with self.assertRaises(ValidationError):
            sprint.clean()

    def assert_allowed(self, sprint):
        try:
            sprint.clean()
        except ValidationError as error:
            self.fail(f"Expected this sprint to be allowed, but got: {error.messages}")


class SprintDatesAndNameCleanTests(SprintCleanTestCase):
    def test_a_valid_sprint_passes_full_validation(self):
        self.unsaved().full_clean()

    def test_a_sprint_can_be_a_single_day(self):
        self.assert_allowed(self.unsaved(start=day(1), end=day(1)))

    def test_a_sprint_can_start_on_any_day_of_the_week(self):
        for number in range(1, 8):
            with self.subTest(weekday=day(number).strftime("%A")):
                self.assert_allowed(self.unsaved(start=day(number), end=day(number + 6)))

    def test_refuses_an_end_before_the_start(self):
        self.assert_refused(self.unsaved(start=day(7), end=day(6)))

    def test_refuses_a_name_already_used_in_the_project(self):
        make_sprint(self.project, "Sprint 1", day(1), day(7))

        self.assert_refused(self.unsaved("Sprint 1", day(10), day(16)))

    def test_refuses_a_name_differing_only_by_case(self):
        make_sprint(self.project, "Sprint 1", day(1), day(7))

        for name in ("sprint 1", "SPRINT 1", "sPrInT 1"):
            with self.subTest(name=name):
                self.assert_refused(self.unsaved(name, day(10), day(16)))

    def test_another_project_can_use_the_same_name(self):
        make_sprint(self.project, "Sprint 1", day(1), day(7))

        other = make_project("Project 2")

        self.assert_allowed(self.unsaved("Sprint 1", day(1), day(7), project=other))

    def test_a_saved_sprint_does_not_conflict_with_itself(self):
        # Without this, no sprint could be edited in admin.
        sprint = make_sprint(self.project, "Sprint 1", day(1), day(7))

        self.assert_allowed(sprint)

    def test_changing_only_the_case_of_a_sprints_own_name_is_allowed(self):
        sprint = make_sprint(self.project, "Sprint 1", day(1), day(7))
        sprint.name = "SPRINT 1"

        self.assert_allowed(sprint)

    def test_renaming_to_another_sprints_name_is_refused(self):
        make_sprint(self.project, "Sprint 1", day(1), day(7))
        second = make_sprint(self.project, "Sprint 2", day(7), day(13))
        second.name = "sprint 1"

        self.assert_refused(second)

    def test_missing_fields_are_reported_rather_than_crashing(self):
        # Django still calls clean() when a field failed validation, so an admin form
        # with a blank date or project reaches it with these unset.
        cases = {
            "start_date": sprint_model()(project=self.project, name="A", end_date=day(7)),
            "end_date": sprint_model()(project=self.project, name="A", start_date=day(1)),
            "project": sprint_model()(name="A", start_date=day(1), end_date=day(7)),
        }
        for field, sprint in cases.items():
            with self.subTest(missing=field):
                with self.assertRaises(ValidationError) as caught:
                    sprint.full_clean()
                self.assertIn(field, caught.exception.message_dict)


class SprintOverlapCleanTests(SprintCleanTestCase):
    """Everything is checked against one existing sprint, Sep 10-16, unless it says otherwise."""

    def setUp(self):
        super().setUp()
        self.existing = make_sprint(self.project, "Existing", day(10), day(16))

    def test_refuses_overlaps_of_more_than_the_shared_day(self):
        cases = {
            "the same dates": (day(10), day(16)),
            "starts inside and runs past the end": (day(13), day(20)),
            "starts before and ends inside": (day(5), day(12)),
            "sits inside": (day(12), day(14)),
            "surrounds": (day(5), day(20)),
            "one day inside": (day(13), day(13)),
        }
        for label, (start, end) in cases.items():
            with self.subTest(label):
                self.assert_refused(self.unsaved(start=start, end=end))

    def test_refuses_a_sprint_sharing_two_days_at_either_end(self):
        # One day either side of the exception, which a < in place of a <= would allow.
        self.assert_refused(self.unsaved(start=day(15), end=day(21)))
        self.assert_refused(self.unsaved(start=day(5), end=day(11)))

    def test_allows_starting_on_the_last_day_of_another_sprint(self):
        self.assert_allowed(self.unsaved(start=day(16), end=day(22)))

    def test_allows_ending_on_the_first_day_of_a_later_sprint(self):
        # Not a same-start clash, since this sprint starts earlier.
        self.assert_allowed(self.unsaved(start=day(4), end=day(10)))

    def test_allows_sprints_with_days_between_them(self):
        self.assert_allowed(self.unsaved(start=day(17), end=day(23)))
        self.assert_allowed(self.unsaved(start=day(3), end=day(9)))

    def test_allows_a_chain_of_sprints_each_sharing_one_day(self):
        make_sprint(self.project, "Next", day(16), day(22))

        self.assert_allowed(self.unsaved(start=day(22), end=day(28)))

    def test_refuses_starting_the_same_day_as_another_sprint(self):
        self.assert_refused(self.unsaved(start=day(10), end=day(20)))

    def test_refuses_a_single_day_on_another_sprints_first_day(self):
        # Its last day is the other's first day, which the shared-day exception would
        # allow. The same-start rule still refuses it.
        self.assert_refused(self.unsaved(start=day(10), end=day(10)))

    def test_refuses_starting_on_the_same_day_as_a_single_day_sprint(self):
        # The new sprint's first day is the other's last day, as above, the other way round.
        make_sprint(self.project, "One day", day(20), day(20))

        self.assert_refused(self.unsaved(start=day(20), end=day(26)))

    def test_allows_a_single_day_sprint_on_another_sprints_last_day(self):
        self.assert_allowed(self.unsaved(start=day(16), end=day(16)))

    def test_refuses_a_second_sprint_starting_on_a_shared_last_day(self):
        make_sprint(self.project, "One day", day(16), day(16))

        self.assert_refused(self.unsaved(start=day(16), end=day(22)))

    def test_ignores_sprints_in_other_projects(self):
        other = make_project("Project 2")

        self.assert_allowed(self.unsaved(start=day(10), end=day(16), project=other))

    def test_a_saved_sprint_cannot_be_moved_onto_another(self):
        later = make_sprint(self.project, "Later", day(20), day(26))
        later.start_date = day(15)

        self.assert_refused(later)


class CoveredThroughTests(TestCase):
    def setUp(self):
        self.sprint = make_sprint(make_project(), start=day(1), end=day(7))

    def test_is_today_while_the_sprint_is_running(self):
        self.assertEqual(self.sprint.covered_through(day(4)), day(4))

    def test_is_the_last_day_once_the_sprint_has_ended(self):
        self.assertEqual(self.sprint.covered_through(day(20)), day(7))

    def test_on_the_last_day_both_are_the_same(self):
        self.assertEqual(self.sprint.covered_through(day(7)), day(7))

    def test_is_today_before_the_sprint_has_started(self):
        # The earlier of the two, as the issue states. Callers refuse an unstarted sprint.
        self.assertEqual(self.sprint.covered_through(date(2026, 8, 20)), date(2026, 8, 20))


class RequirementSprintTests(TestCase):
    def setUp(self):
        self.project = make_project()
        self.first = make_sprint(self.project, "Sprint 1", day(1), day(7))
        self.second = make_sprint(self.project, "Sprint 2", day(7), day(13))

    def test_sprint_is_a_foreign_key_to_sprint(self):
        field = Requirement._meta.get_field("sprint")

        self.assertIsInstance(field, models.ForeignKey)
        self.assertIs(field.related_model, sprint_model())

    def test_stores_its_sprint(self):
        created = make_requirement(self.first)

        self.assertEqual(Requirement.objects.get(pk=created.pk).sprint, self.first)

    def test_each_sprint_in_a_project_can_start_at_one(self):
        make_requirement(self.first, number=1)
        make_requirement(self.second, number=1)

        numbered_one = Requirement.objects.filter(project=self.project, number=1)
        self.assertEqual(numbered_one.count(), 2)

    def test_two_requirements_with_the_same_number_pass_validation_in_different_sprints(self):
        make_requirement(self.first, number=1)

        Requirement(
            project=self.project, sprint=self.second, number=1, text="Something else"
        ).full_clean()

    def test_database_rejects_a_number_used_twice_in_a_sprint(self):
        make_requirement(self.first, number=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_requirement(self.first, number=1, text="Something else")

    def test_validation_rejects_a_number_used_twice_in_a_sprint(self):
        make_requirement(self.first, number=1)

        with self.assertRaises(ValidationError):
            Requirement(
                project=self.project, sprint=self.first, number=1, text="Something else"
            ).full_clean()


class SprintDataMigrationTests(TransactionTestCase):
    """Starts from the schema before sprints and runs every migration from there."""

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([LAST_MIGRATION_BEFORE_SPRINTS])
        self.addCleanup(self.migrate_to_latest)
        self.old_apps = executor.loader.project_state([LAST_MIGRATION_BEFORE_SPRINTS]).apps
        self.old_course = self.old_apps.get_model("core", "Course").objects.create(name="CS 4501")

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate(latest)
        return MigrationExecutor(connection).loader.project_state(latest).apps

    def migrate(self):
        with freeze():
            new_apps = self.migrate_to_latest()
        return new_apps.get_model("core", "Sprint"), new_apps.get_model("core", "Requirement")

    def old_project(self, name, requirements=()):
        project = self.old_apps.get_model("core", "Project").objects.create(
            course=self.old_course, name=name
        )
        Requirement_ = self.old_apps.get_model("core", "Requirement")
        for number, text in requirements:
            Requirement_.objects.create(project=project, number=number, text=text)
        return project

    def test_a_project_with_requirements_gets_one_sprint_named_sprint_1(self):
        project = self.old_project("Has requirements", [(1, "Log in")])

        Sprint, _ = self.migrate()

        sprint = Sprint.objects.get(project_id=project.pk)
        self.assertEqual(sprint.name, "Sprint 1")

    def test_the_sprint_runs_seven_days_from_the_migration_date_in_new_york(self):
        project = self.old_project("Has requirements", [(1, "Log in")])

        Sprint, _ = self.migrate()

        sprint = Sprint.objects.get(project_id=project.pk)
        self.assertEqual(sprint.start_date, TODAY)
        self.assertEqual(sprint.end_date, SEVENTH_DAY)
        self.assertEqual(sprint.end_date - sprint.start_date, timedelta(days=6))

    def test_moves_every_requirement_keeping_its_number_and_text(self):
        # Claims point at requirements by number. The gap is one left by a deleted requirement.
        project = self.old_project("Has requirements", [(1, "Log in"), (2, "Log out"), (5, "Pay")])

        Sprint, Requirement_ = self.migrate()

        sprint = Sprint.objects.get(project_id=project.pk)
        moved = Requirement_.objects.filter(sprint_id=sprint.pk).order_by("number")
        self.assertEqual(
            list(moved.values_list("number", "text")),
            [(1, "Log in"), (2, "Log out"), (5, "Pay")],
        )

    def test_moved_requirements_keep_their_project_until_t21(self):
        project = self.old_project("Has requirements", [(1, "Log in")])

        _, Requirement_ = self.migrate()

        self.assertEqual(Requirement_.objects.get().project_id, project.pk)

    def test_a_project_with_no_requirements_gets_no_sprint(self):
        project = self.old_project("No requirements")

        Sprint, _ = self.migrate()

        self.assertFalse(Sprint.objects.filter(project_id=project.pk).exists())

    def test_each_project_gets_its_own_sprint(self):
        first = self.old_project("First", [(1, "Log in")])
        second = self.old_project("Second", [(1, "Log in"), (2, "Log out")])

        Sprint, Requirement_ = self.migrate()

        for project, expected in ((first, 1), (second, 2)):
            with self.subTest(project=project.name):
                sprint = Sprint.objects.get(project_id=project.pk)
                self.assertEqual(Requirement_.objects.filter(sprint_id=sprint.pk).count(), expected)


class SprintAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser(
                username="admin",
                password="not-a-real-password",
                email="admin@example.com",
                github_login="course-admin",
            )
        )
        self.project = make_project()

    def post_add(self, name="Sprint 2", start="2026-09-20", end="2026-09-26"):
        return self.client.post(
            reverse("admin:core_sprint_add"),
            {"project": self.project.pk, "name": name, "start_date": start, "end_date": end},
        )

    def test_sprint_is_registered(self):
        self.assertTrue(admin.site.is_registered(sprint_model()))

    def test_the_list_page_shows_sprints(self):
        make_sprint(self.project, "The first sprint")

        response = self.client.get(reverse("admin:core_sprint_changelist"))

        self.assertContains(response, "The first sprint")

    def test_a_valid_sprint_can_be_added(self):
        response = self.post_add()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(sprint_model().objects.filter(name="Sprint 2").exists())

    def test_the_add_page_applies_the_overlap_rule(self):
        make_sprint(self.project, "Sprint 1", day(1), day(7))

        response = self.post_add(start="2026-09-03", end="2026-09-09")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        self.assertFalse(sprint_model().objects.filter(name="Sprint 2").exists())

    def test_a_blank_date_shows_a_field_error_rather_than_crashing(self):
        response = self.post_add(start="")

        self.assertEqual(response.status_code, 200)
        self.assertIn("start_date", response.context["adminform"].form.errors)

    def test_a_requirement_can_be_assigned_a_sprint_in_admin(self):
        response = self.client.get(reverse("admin:core_requirement_add"))

        self.assertContains(response, 'name="sprint"')


class ImportRequirementsIntoSprintTests(SplitRequirementsTestCase):
    """The admin import, on a page per sprint.

    The project's own import page stays until T-21 and keeps its existing tests.
    """

    def setUp(self):
        super().setUp()
        self.sprint = make_sprint(self.project, "Sprint 1", day(1), day(7))

    def sprint_import_url(self, sprint=None):
        pk = sprint.pk if sprint else self.sprint.pk
        try:
            return reverse("admin:core_sprint_import_requirements", args=[pk])
        except NoReverseMatch:
            self.fail("Sprint has no admin page for importing requirements")

    def paste_into(self, sprint=None, text=SOURCE, **extra):
        return self.client.post(self.sprint_import_url(sprint), {"text": text}, **extra)

    def stored_in(self, sprint=None):
        requirements = Requirement.objects.filter(sprint=sprint or self.sprint)
        return list(requirements.order_by("number").values_list("number", "text"))

    def expected_split(self):
        return list(enumerate(SPLIT, start=1))

    def test_the_sprints_admin_page_links_to_the_import_page(self):
        url = self.sprint_import_url()

        response = self.client.get(reverse("admin:core_sprint_change", args=[self.sprint.pk]))

        self.assertContains(response, url)

    def test_the_page_for_adding_a_sprint_still_loads(self):
        # It shares the template that holds the link, but has no sprint to link to yet.
        response = self.client.get(reverse("admin:core_sprint_add"))

        self.assertEqual(response.status_code, 200)

    def test_pasted_text_becomes_the_sprints_requirements_numbered_from_one(self):
        self.paste_into()

        self.assertEqual(self.stored_in(), self.expected_split())

    def test_a_pdf_becomes_the_sprints_requirements(self):
        pdf = SimpleUploadedFile("requirements.pdf", text_pdf(), "application/pdf")

        self.client.post(self.sprint_import_url(), {"pdf": pdf, "text": ""})

        self.assertEqual(self.stored_in(), self.expected_split())

    def test_imported_requirements_keep_their_project_until_t21(self):
        # The dashboard, validator and prompt still read requirements through the project.
        self.paste_into()

        self.assertEqual(
            self.stored(), self.expected_split(), "Imported requirements have no project"
        )

    def test_after_importing_the_imported_requirements_are_listed(self):
        response = self.paste_into(follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SPLIT[0])

    def test_each_sprint_in_a_project_can_import_starting_at_one(self):
        second = make_sprint(self.project, "Sprint 2", day(7), day(13))

        self.paste_into(self.sprint)
        self.paste_into(second)

        self.assertEqual(self.stored_in(self.sprint), self.expected_split())
        self.assertEqual(self.stored_in(second), self.expected_split())

    def test_a_sprint_that_has_requirements_is_refused(self):
        # Claims point to requirements by number, as with the project import.
        make_requirement(self.sprint, number=1, text="Already here")

        response = self.paste_into()

        self.assert_import_error(response)
        self.assertEqual(self.stored_in(), [(1, "Already here")])

    def test_an_import_finishing_during_the_llm_call_is_not_doubled_up(self):
        self.llm.during_call = lambda: make_requirement(self.sprint, 1, "Got here first")

        response = self.paste_into()

        self.assert_import_error(response)
        self.assertEqual(self.stored_in(), [(1, "Got here first")])

    def test_staff_without_permission_to_add_requirements_are_refused(self):
        self.client.force_login(
            User.objects.create_user(
                username="ta", password="not-a-real-password", github_login="a-ta", is_staff=True
            )
        )

        response = self.paste_into()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored_in(), [])

    def test_an_unknown_sprint_is_not_found(self):
        url = self.sprint_import_url().replace(f"/{self.sprint.pk}/", "/999999/")

        self.assertEqual(self.client.post(url, {"text": SOURCE}).status_code, 404)


class SeedSprintTests(TestCase):
    """Seeded data has to fit the sprint model too. See test_seed_command.py for the rest."""

    def seeded_sprint(self):
        return Project.objects.get().sprints.get()

    def test_the_project_gets_a_sprint_that_passes_validation(self):
        seed()

        self.seeded_sprint().full_clean()

    def test_the_sprint_has_the_same_name_as_the_data_migrations(self):
        # A database seeded before sprints gets "Sprint 1" from the data migration, so
        # any other name would give it a second sprint on the next seed.
        seed()

        self.assertEqual(self.seeded_sprint().name, "Sprint 1")

    def test_the_sprint_runs_seven_days_from_today_in_new_york(self):
        with freeze():
            seed()

        sprint = self.seeded_sprint()
        self.assertEqual((sprint.start_date, sprint.end_date), (TODAY, SEVENTH_DAY))

    def test_every_seeded_requirement_is_on_the_sprint(self):
        seed()

        sprint = self.seeded_sprint()
        requirements = Requirement.objects.all()
        self.assertGreaterEqual(requirements.count(), 2)
        for requirement in requirements:
            with self.subTest(number=requirement.number):
                self.assertEqual(requirement.sprint, sprint)

    def test_a_later_run_reuses_the_sprint_and_leaves_its_dates_alone(self):
        # Moving the dates on each run would pull artifacts in and out of the sprint.
        with freeze():
            seed()
        sprint = self.seeded_sprint()

        with freeze(NOW + timedelta(days=30)):
            seed()

        self.assertEqual(self.seeded_sprint().pk, sprint.pk)
        self.assertEqual(self.seeded_sprint().start_date, sprint.start_date)
        self.assertEqual(self.seeded_sprint().end_date, sprint.end_date)
