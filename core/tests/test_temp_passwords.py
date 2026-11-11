"""Tests for US-44 (#127): generating temporary passwords for a project's students.

These test the service behind the pages. Every student is made up. In the fixture, Grace
chose her own password, so she is the student who must be left alone.
"""

import csv
import io
from itertools import chain, repeat
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import connection

from core.models import Enrollment, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user
from dashboard.tests.test_team_setup import TeamSetupFixture

User = get_user_model()
Role = Enrollment.Role


def plan(project, instructor):
    from core.services import plan_temporary_passwords

    return plan_temporary_passwords(project, instructor)


def generate(project, instructor):
    from core.services import generate_temporary_passwords

    return generate_temporary_passwords(project, instructor)


class PasswordFixture(TeamSetupFixture):
    def setUp(self):
        super().setUp()
        # Never given a password, like a student made by the CSV import.
        self.new = self.add_student("new-student", self.team)
        self.new.set_unusable_password()
        self.new.save()
        # Given a temporary password that they haven't changed yet.
        self.waiting = self.add_student("waiting-student", self.empty_team)
        self.waiting.set_password("Old-temporary-1")
        self.waiting.must_change_password = True
        self.waiting.save()

    def add_student(self, login, team, first="Sam", last="Sample"):
        user = make_user(login, first, last)
        enroll(user, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=user, team=team)
        return user


class PlanTests(PasswordFixture):
    def test_it_covers_students_who_cannot_log_in_or_have_an_unchanged_temporary_password(self):
        result = plan(self.project, self.instructor)
        self.assertCountEqual([row.user for row in result.targets], [self.new, self.waiting])
        self.assertEqual(result.count, 2)

    def test_a_student_who_chose_their_own_password_is_left_alone(self):
        self.assertNotIn(self.grace, [row.user for row in plan(self.project, self.instructor).targets])
        self.assertEqual(plan(self.project, self.instructor).left_alone, 1)

    def test_students_on_other_projects_teams_are_not_included(self):
        stranger = self.add_student("later-student", self.later_team)
        stranger.set_unusable_password()
        stranger.save()
        self.assertNotIn(stranger, [row.user for row in plan(self.project, self.instructor).targets])

    def test_the_plan_counts_outstanding_temporary_passwords_that_will_be_replaced(self):
        self.assertEqual(plan(self.project, self.instructor).replaced, 1)

    def test_planning_changes_nothing(self):
        plan(self.project, self.instructor)
        self.new.refresh_from_db()
        self.assertFalse(self.new.has_usable_password())

    def test_an_account_that_cannot_be_given_a_password_is_listed_as_blocked(self):
        ta = self.add_student("busy-student", self.team)
        enroll(ta, self.other_course, Role.TA)
        ta.set_unusable_password()  # So it needs a password, but the admin must set it.
        ta.save()  # A TA in another course.
        result = plan(self.project, self.instructor)
        self.assertEqual([row.user for row in result.blocked], [ta])
        self.assertNotIn(ta, [row.user for row in result.targets])

    def test_a_student_also_in_a_course_this_instructor_does_not_teach_is_blocked(self):
        """Otherwise the instructor would know a working login for another course too."""
        enroll(self.waiting, self.other_course, Role.STUDENT)
        result = plan(self.project, self.instructor)
        self.assertNotIn(self.waiting, [row.user for row in result.targets])
        blocked = {row.user: row for row in result.blocked}
        self.assertIn(self.waiting, blocked)
        self.assertIn("course you don't teach", blocked[self.waiting].note)

    def test_a_student_also_in_another_course_this_instructor_teaches_is_included(self):
        enroll(self.instructor, self.other_course, Role.INSTRUCTOR)
        enroll(self.waiting, self.other_course, Role.STUDENT)
        result = plan(self.project, self.instructor)
        self.assertIn(self.waiting, [row.user for row in result.targets])
        self.assertEqual(result.blocked, [])


class GenerateTests(PasswordFixture):
    def test_each_target_gets_a_different_random_password_that_passes_the_rules(self):
        rows = [row for row in generate(self.project, self.instructor) if row.password]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row.password for row in rows}), 2)
        for row in rows:
            validate_password(row.password, user=row.user)
            row.user.refresh_from_db()
            self.assertTrue(row.user.check_password(row.password))

    def test_they_must_change_it_at_first_login(self):
        generate(self.project, self.instructor)
        self.new.refresh_from_db()
        self.assertTrue(self.new.must_change_password)

    def test_an_outstanding_temporary_password_is_replaced(self):
        generate(self.project, self.instructor)
        self.waiting.refresh_from_db()
        self.assertFalse(self.waiting.check_password("Old-temporary-1"))

    def test_a_student_who_chose_their_own_password_keeps_it(self):
        generate(self.project, self.instructor)
        self.grace.refresh_from_db()
        self.assertTrue(self.grace.check_password("not-a-real-password"))
        self.assertFalse(self.grace.must_change_password)

    def test_it_returns_a_row_for_each_target_with_team_name_login_and_email(self):
        rows = {row.github_login: row for row in generate(self.project, self.instructor)}
        self.assertEqual(rows["new-student"].team, self.team.name)
        self.assertEqual(rows["waiting-student"].team, self.empty_team.name)
        self.assertEqual(rows["new-student"].name, "Sam Sample")

    def test_a_blocked_account_gets_a_blank_password_and_a_note_to_use_the_admin(self):
        ta = self.add_student("busy-student", self.team)
        enroll(ta, self.other_course, Role.TA)
        ta.set_unusable_password()  # So it needs a password, but the admin must set it.
        ta.save()
        row = next(row for row in generate(self.project, self.instructor) if row.github_login == "busy-student")
        self.assertEqual(row.password, "")
        self.assertIn("admin", row.note)
        ta.refresh_from_db()
        self.assertFalse(ta.has_usable_password())

    def test_a_student_also_in_a_course_this_instructor_does_not_teach_keeps_their_password(self):
        enroll(self.waiting, self.other_course, Role.STUDENT)
        row = next(r for r in generate(self.project, self.instructor) if r.github_login == "waiting-student")
        self.assertEqual(row.password, "")
        self.assertIn("admin", row.note)
        self.waiting.refresh_from_db()
        self.assertTrue(self.waiting.check_password("Old-temporary-1"))

    def test_passwords_are_hashed_before_the_transaction_that_saves_them(self):
        """Hashing is slow, and SQLite holds its write lock for the whole transaction, so
        hashing inside it would make every other write in the app wait."""
        from django.contrib.auth import hashers

        outside = len(connection.atomic_blocks)
        depths = []

        def recording(*args, **kwargs):
            depths.append(len(connection.atomic_blocks))
            return hashers.make_password(*args, **kwargs)

        with (
            mock.patch("core.services.make_password", side_effect=recording, create=True),
            mock.patch("django.contrib.auth.base_user.make_password", side_effect=recording),
        ):
            generate(self.project, self.instructor)
        self.assertEqual(len(depths), 2)
        self.assertEqual(depths, [outside, outside])

    def test_a_password_a_student_chooses_while_hashing_is_kept_and_nothing_is_saved(self):
        """Between hashing and saving, a student may log in and choose their own password."""
        from django.contrib.auth import hashers

        from core.services import ProjectSetupError

        def student_changes_theirs(*args, **kwargs):
            User.objects.filter(pk=self.waiting.pk).update(
                password=hashers.make_password("My-own-choice-1"), must_change_password=False
            )
            return hashers.make_password(*args, **kwargs)

        with mock.patch("core.services.make_password", side_effect=student_changes_theirs, create=True):
            with self.assertRaises(ProjectSetupError):
                generate(self.project, self.instructor)
        self.waiting.refresh_from_db()
        self.new.refresh_from_db()
        self.assertTrue(self.waiting.check_password("My-own-choice-1"))
        self.assertFalse(self.waiting.must_change_password)
        self.assertFalse(self.new.has_usable_password())

    def test_all_the_passwords_are_saved_or_none(self):
        from core.services import ProjectSetupError

        strong = ["Strong-password-1"]
        # The first student gets a good password, then every attempt for the next one is too weak.
        with mock.patch("core.services._random_password", side_effect=chain(strong, repeat("1234"))):
            with self.assertRaises(ProjectSetupError):
                generate(self.project, self.instructor)
        self.new.refresh_from_db()
        self.waiting.refresh_from_db()
        self.assertFalse(self.new.has_usable_password())
        self.assertTrue(self.waiting.check_password("Old-temporary-1"))

    def test_the_plain_passwords_are_never_logged(self):
        with self.assertNoLogs():
            rows = generate(self.project, self.instructor)
        self.assertTrue(all(row.password for row in rows))


class CsvTests(PasswordFixture):
    def render(self, rows):
        from core.services import temporary_passwords_csv

        return list(csv.reader(io.StringIO(temporary_passwords_csv(rows))))

    def test_the_columns(self):
        header = self.render(generate(self.project, self.instructor))[0]
        self.assertEqual(
            header, ["team", "name", "github_login", "email", "temporary_password", "note"]
        )

    def test_each_student_has_a_row_with_their_password(self):
        rows = generate(self.project, self.instructor)
        body = {line[2]: line for line in self.render(rows)[1:]}
        by_login = {row.github_login: row for row in rows}
        for login, line in body.items():
            self.assertEqual(line[4], by_login[login].password)

    def test_cells_that_start_like_formulas_are_neutralized_for_excel(self):
        from core.services import PasswordRow, temporary_passwords_csv

        row = PasswordRow(
            team="=SUM(1)", name="+cmd", github_login="x", email="@a.b", password="Pw-1-abcdefgh", note="-1"
        )
        line = list(csv.reader(io.StringIO(temporary_passwords_csv([row]))))[1]
        self.assertEqual([line[0], line[1], line[3], line[5]], ["'=SUM(1)", "'+cmd", "'@a.b", "'-1"])
        self.assertEqual(line[4], "Pw-1-abcdefgh")

    def test_a_password_never_starts_with_a_formula_character(self):
        from core.services import _random_password

        for _ in range(200):
            self.assertNotIn(_random_password()[0], "=+-@")
