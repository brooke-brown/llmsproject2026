"""Tests for US-43 (#126): importing a project's teams and students from a CSV.

These test the service behind the pages: reading the file, planning (the preview) and
importing. GitHub is mocked on PyGithub's own class, so nothing touches the network.
Every student here is made up.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from github import UnknownObjectException

from core.models import Enrollment, Repo, Team, TeamMembership
from dashboard.tests.test_course_overview import enroll, make_user
from dashboard.tests.test_team_setup import TeamSetupFixture

User = get_user_model()
HEADER = "team,name,github_login,email,repo_url"


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def plan(project, text):
    from core.services import plan_team_import

    return plan_team_import(project, text)


def do_import(project, text):
    from core.services import import_teams

    return import_teams(project, text)


def messages(result):
    return [error.message for error in result.errors]


class ReadingTests(TeamSetupFixture):
    def test_columns_can_be_in_any_order_and_headers_ignore_case(self):
        text = "Repo_URL , GitHub_Login,TEAM,Name\nhttps://github.com/acme/new,ada-dev,Team Gamma,Ada Lovelace\n"
        result = plan(self.project, text)
        self.assertEqual(result.errors, [])
        (team,) = result.teams
        self.assertEqual((team.name, team.repo), ("Team Gamma", "acme/new"))
        self.assertEqual(team.students[0].github_login, "ada-dev")

    def test_email_and_repo_url_are_optional_columns(self):
        result = plan(self.project, "team,name,github_login\nTeam Gamma,Ada Lovelace,ada-dev\n")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.teams[0].repo, "")

    def test_a_file_exported_from_excel_works(self):
        from core.services import read_team_csv

        data = "﻿" + csv_text("Team Gamma,Ada Lovelace,ada-dev,,").replace("\n", "\r\n")
        text = read_team_csv(data.encode("utf-8"))
        self.assertEqual(plan(self.project, text).errors, [])

    def test_a_windows_1252_export_works(self):
        from core.services import read_team_csv

        text = read_team_csv(csv_text("Team Gamma,José Núñez,jose-dev,,").encode("cp1252"))
        self.assertEqual(plan(self.project, text).teams[0].students[0].name, "José Núñez")

    def test_blank_lines_are_ignored(self):
        text = csv_text("Team Gamma,Ada Lovelace,ada-dev,,", ",,,,", "")
        self.assertEqual(len(plan(self.project, text).teams[0].students), 1)

    def test_a_missing_required_column_is_named(self):
        from core.services import TeamImportError

        with self.assertRaisesMessage(TeamImportError, "github_login"):
            plan(self.project, "team,name\nTeam Gamma,Ada Lovelace\n")

    def test_a_file_with_no_rows_is_rejected(self):
        from core.services import TeamImportError

        with self.assertRaisesMessage(TeamImportError, "no rows"):
            plan(self.project, HEADER + "\n")

    def test_an_empty_file_is_rejected(self):
        from core.services import TeamImportError

        with self.assertRaises(TeamImportError):
            plan(self.project, "")

    def test_a_file_that_is_not_csv_is_rejected(self):
        from core.services import TeamImportError, read_team_csv

        with self.assertRaisesMessage(TeamImportError, "CSV"):
            read_team_csv(b"%PDF-1.7\x00\x01\x02binary")

    def test_a_file_over_one_megabyte_is_rejected(self):
        from core.services import TeamImportError, read_team_csv

        with self.assertRaisesMessage(TeamImportError, "1 MB"):
            read_team_csv(b"a" * (1024 * 1024 + 1))

    def test_pasted_text_over_one_megabyte_is_rejected_too(self):
        from core.services import TeamImportError

        with self.assertRaisesMessage(TeamImportError, "1 MB"):
            plan(self.project, HEADER + "\n" + "x" * (1024 * 1024))

    def test_a_row_with_more_cells_than_the_header_is_an_error(self):
        result = plan(
            self.project, "team,name,github_login\nTeam Gamma,Smith, John,jsmith\n"
        )
        self.assertEqual([e.row for e in result.errors], [2])
        self.assertIn("more cells", messages(result)[0])
        self.assertIn("quotes", messages(result)[0])
        self.assertEqual(result.teams[0].students[0].status, "error")

    def test_a_row_with_more_cells_than_the_header_is_not_imported(self):
        from core.services import TeamImportError

        with self.assertRaises(TeamImportError):
            do_import(self.project, "team,name,github_login\nTeam Gamma,Smith, John,jsmith\n")
        self.assertFalse(User.objects.filter(github_login__iexact="John").exists())

    def test_a_row_with_extra_cells_reports_only_that(self):
        # Its cells are shifted, so "John Q" isn't really a username to complain about.
        result = plan(self.project, "team,name,github_login\nTeam Gamma,Smith, John Q,jsmith\n")
        self.assertEqual(len(result.errors), 1)

    def test_a_row_with_extra_cells_does_not_claim_the_shifted_username(self):
        result = plan(
            self.project,
            "team,name,github_login\nTeam Gamma,Smith, John,jsmith\nTeam Gamma,John Real,John\n",
        )
        self.assertEqual([e.row for e in result.errors], [2])

    def test_a_shifted_repo_cell_is_not_used(self):
        result = plan(
            self.project,
            f"{HEADER}\nTeam Gamma,Smith, John,jsmith,,https://github.com/acme/shifted\n",
        )
        self.assertEqual([e.row for e in result.errors], [2])
        self.assertEqual(result.teams[0].repo, "")
        self.get_repo.assert_not_called()

    def test_trailing_blank_cells_past_the_header_are_fine(self):
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,,,,  ,"))
        self.assertEqual(result.errors, [])

    def test_a_file_with_bare_carriage_returns_works(self):
        from core.services import read_team_csv

        data = csv_text("Team Gamma,Ada Lovelace,ada-dev,,", "Team Gamma,Bo Byte,-bad-,,")
        result = plan(self.project, read_team_csv(data.replace("\n", "\r").encode("utf-8")))
        self.assertEqual([s.name for s in result.teams[0].students], ["Ada Lovelace", "Bo Byte"])
        self.assertEqual([e.row for e in result.errors], [3])

    def test_a_file_at_the_limit_still_fits_once_a_browser_doubles_its_newlines(self):
        from core.services import MAX_TEAM_CSV_BYTES, read_team_csv

        start = csv_text("Team Gamma,Ada Lovelace,ada-dev,,")
        data = (start + "\n" * (MAX_TEAM_CSV_BYTES - len(start))).encode("utf-8")
        text = read_team_csv(data)
        # What the hidden textarea sends back: every newline becomes CRLF.
        self.assertEqual(plan(self.project, text.replace("\n", "\r\n")).errors, [])

    def test_a_windows_1252_file_at_the_limit_is_not_rejected_for_growing_in_utf_8(self):
        from core.services import MAX_TEAM_CSV_BYTES, read_team_csv

        start = csv_text("Team Gamma,José Núñez,jose-dev,,").encode("cp1252")
        # Lines of non-breaking spaces count as blank, and each byte is two in UTF-8.
        line = b"\xa0" * 99 + b"\n"
        data = start + line * ((MAX_TEAM_CSV_BYTES - len(start)) // len(line))
        self.assertEqual(plan(self.project, read_team_csv(data)).errors, [])


class PreviewTests(TeamSetupFixture):
    def test_nothing_is_saved_by_a_preview(self):
        counts = (Team.objects.count(), User.objects.count(), Repo.objects.count())
        plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/new"))
        self.assertEqual((Team.objects.count(), User.objects.count(), Repo.objects.count()), counts)

    def test_it_lists_each_team_with_its_students_repo_and_what_is_new(self):
        result = plan(
            self.project,
            csv_text(
                "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/new",
                "Team Gamma,Bo Byte,bo-dev,,",
                "Team Alpha,Cy Code,cy-dev,,",
            ),
        )
        self.assertEqual(result.errors, [])
        gamma, alpha = result.teams
        self.assertTrue(gamma.is_new)
        self.assertEqual((gamma.repo, gamma.repo_is_new), ("acme/new", True))
        self.assertEqual([s.status for s in gamma.students], ["new", "new"])
        self.assertFalse(alpha.is_new)
        self.assertEqual(alpha.students[0].status, "new")

    def test_a_student_already_on_the_team_is_marked_already_there(self):
        result = plan(self.project, csv_text("team alpha,Grace Example,sample-grace,,"))
        (team,) = result.teams
        self.assertEqual(team.name, self.team.name)  # Matched regardless of case.
        self.assertEqual(team.students[0].status, "on_team")
        self.assertTrue(result.nothing_to_add)

    def test_a_name_that_differs_from_the_account_is_noted_even_on_the_team(self):
        result = plan(self.project, csv_text("Team Alpha,Gracie E.,sample-grace,,"))
        student = result.teams[0].students[0]
        self.assertEqual(student.status, "on_team")
        self.assertIn("Grace Example", student.note)

    def test_an_existing_account_not_on_a_team_is_marked_existing_with_a_name_note(self):
        loose = make_user("loose-student", "Lou", "Loose")
        enroll(loose, self.course, Enrollment.Role.STUDENT)
        result = plan(self.project, csv_text("Team Beta,Louise Other,loose-student,,"))
        self.assertEqual(result.errors, [])
        student = result.teams[0].students[0]
        self.assertEqual(student.status, "existing")
        self.assertIn("Lou Loose", student.note)

    def test_errors_name_the_row_and_the_problem(self):
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,-bad-,,", "Team Gamma,Bo Byte,bo-dev,,"))
        self.assertEqual([e.row for e in result.errors], [2])
        self.assertIn("GitHub username", messages(result)[0])
        self.assertFalse(result.ok)

    def test_a_student_listed_twice_is_an_error(self):
        result = plan(
            self.project,
            csv_text("Team Gamma,Ada Lovelace,ada-dev,,", "Team Delta,Ada L,ADA-dev,,"),
        )
        self.assertEqual([e.row for e in result.errors], [3])
        self.assertIn("twice", messages(result)[0])

    def test_a_student_on_a_different_team_in_this_project_is_an_error(self):
        result = plan(self.project, csv_text("Team Beta,Grace Example,sample-grace,,"))
        self.assertEqual([e.row for e in result.errors], [2])
        self.assertIn("Team Alpha", messages(result)[0])

    def test_a_student_on_a_team_in_another_project_is_fine(self):
        from core.models import TeamMembership

        TeamMembership.objects.filter(user=self.grace).delete()
        TeamMembership.objects.create(user=self.grace, team=self.later_team)
        result = plan(self.project, csv_text("Team Beta,Grace Example,sample-grace,,"))
        self.assertEqual(result.errors, [])

    def test_an_account_with_a_password_and_no_enrollments_is_an_error(self):
        from core.services import ProjectSetupError, add_student

        make_user("unclaimed-dev", "Una", "Claimed")  # Has a password and no enrollments.
        with self.assertRaises(ProjectSetupError) as by_hand:
            add_student(self.empty_team, "Una Claimed", "unclaimed-dev")
        result = plan(self.project, csv_text("Team Gamma,Una Claimed,UNCLAIMED-dev,,"))
        self.assertEqual(messages(result), [str(by_hand.exception)])
        self.assertEqual([e.row for e in result.errors], [2])
        self.assertEqual(result.teams[0].students[0].status, "error")

    def test_an_account_with_a_password_enrolled_in_another_course_is_fine(self):
        other = make_user("elsewhere-dev", "Elle", "Where")
        enroll(other, self.other_course, Enrollment.Role.STUDENT)
        result = plan(self.project, csv_text("Team Gamma,Elle Where,elsewhere-dev,,"))
        self.assertEqual(result.errors, [])
        self.assertEqual(result.teams[0].students[0].status, "existing")

    def test_an_account_without_a_password_and_no_enrollments_is_fine(self):
        loose = make_user("no-password-dev", "Nora", "Pass")
        loose.set_unusable_password()
        loose.save()
        result = plan(self.project, csv_text("Team Gamma,Nora Pass,no-password-dev,,"))
        self.assertEqual(result.errors, [])

    def test_a_staff_account_with_no_enrollments_is_not_called_unclaimed(self):
        User.objects.filter(pk=make_user("staff-dev", "Stan", "Staff").pk).update(is_staff=True)
        result = plan(self.project, csv_text("Team Gamma,Stan Staff,staff-dev,,"))
        self.assertNotIn("isn't enrolled", " ".join(messages(result)))

    def test_an_instructor_or_ta_in_this_course_is_an_error(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Enrollment.Role.TA)
        for login in ("sample-instructor", "sample-ta"):
            result = plan(self.project, csv_text(f"Team Gamma,Some One,{login},,"))
            self.assertEqual([e.row for e in result.errors], [2], login)
            self.assertIn("can't be added to a team", messages(result)[0])

    def test_an_account_whose_username_is_taken_by_someone_else_is_an_error(self):
        make_user("taken-name")  # Username "taken-name", but a different GitHub login.
        User.objects.filter(username="taken-name").update(github_login="someone-else")
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,taken-name,,"))
        self.assertEqual([e.row for e in result.errors], [2])

    def test_a_team_whose_rows_disagree_about_its_repo_is_an_error(self):
        result = plan(
            self.project,
            csv_text(
                "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/one",
                "Team Gamma,Bo Byte,bo-dev,,https://github.com/acme/two",
            ),
        )
        self.assertEqual([e.row for e in result.errors], [3])
        self.assertIn("repo", messages(result)[0])

    def test_a_blank_repo_on_one_row_does_not_disagree(self):
        result = plan(
            self.project,
            csv_text(
                "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/one",
                "Team Gamma,Bo Byte,bo-dev,,",
            ),
        )
        self.assertEqual(result.errors, [])

    def test_blank_cells_and_bad_emails_and_bad_repo_urls_are_errors(self):
        result = plan(
            self.project,
            csv_text(
                ",Ada Lovelace,ada-dev,,",
                "Team Gamma,,bo-dev,,",
                "Team Gamma,Cy Code,cy-dev,not-an-email,",
                "Team Gamma,Di Data,di-dev,,https://example.com/x",
            ),
        )
        self.assertEqual([e.row for e in result.errors], [2, 3, 4, 5])

    def test_an_unreadable_repo_is_an_error_with_the_same_message_as_adding_it_by_hand(self):
        from core.services import ProjectSetupError, add_repo

        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        with self.assertRaises(ProjectSetupError) as by_hand:
            add_repo(self.empty_team, "https://github.com/acme/private")
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/private"))
        self.assertEqual(messages(result), [str(by_hand.exception)])
        self.assertEqual([e.row for e in result.errors], [2])

    def test_each_distinct_repo_is_checked_once_not_once_per_row(self):
        url = "https://github.com/acme/shared"
        plan(
            self.project,
            csv_text(
                f"Team Gamma,Ada Lovelace,ada-dev,,{url}",
                f"Team Gamma,Bo Byte,bo-dev,,{url}",
                f"Team Gamma,Cy Code,cy-dev,,{url.upper().replace('HTTPS', 'https')}",
            ),
        )
        self.assertEqual(self.get_repo.call_count, 1)

    def test_an_existing_team_with_the_same_repo_is_fine_and_a_different_one_is_an_error(self):
        same = plan(self.project, csv_text("Team Alpha,Cy Code,cy-dev,,https://github.com/ACME/existing"))
        self.assertEqual(same.errors, [])
        self.assertFalse(same.teams[0].repo_is_new)
        different = plan(self.project, csv_text("Team Alpha,Cy Code,cy-dev,,https://github.com/acme/other"))
        self.assertEqual([e.row for e in different.errors], [2])

    def test_an_existing_team_without_a_repo_gets_the_new_one(self):
        result = plan(self.project, csv_text("Team Beta,Cy Code,cy-dev,,https://github.com/acme/beta"))
        self.assertEqual(result.errors, [])
        self.assertTrue(result.teams[0].repo_is_new)

    def test_a_repo_the_team_already_has_is_shown_as_already_there(self):
        result = plan(self.project, csv_text("Team Alpha,Cy Code,cy-dev,,https://github.com/ACME/existing"))
        (team,) = result.teams
        self.assertEqual((team.repo, team.repo_is_new), ("acme/existing", False))
        self.get_repo.assert_not_called()

    def test_an_existing_team_name_with_a_double_space_matches(self):
        spaced = Team.objects.create(project=self.project, name="Team  Spaced")
        for name in ("Team  Spaced", "team spaced"):
            result = plan(self.project, csv_text(f"{name},Cy Code,cy-dev,,"))
            self.assertEqual(result.errors, [], name)
            self.assertEqual((result.teams[0].name, result.teams[0].is_new), (spaced.name, False), name)

    def test_a_member_of_a_team_with_a_double_space_is_already_there(self):
        spaced = Team.objects.create(project=self.project, name="Team  Spaced")
        member = make_user("spaced-student", "Sam", "Spaced")
        enroll(member, self.course, Enrollment.Role.STUDENT)
        TeamMembership.objects.create(user=member, team=spaced)
        result = plan(self.project, csv_text("Team  Spaced,Sam Spaced,spaced-student,,"))
        self.assertEqual(result.errors, [])
        self.assertEqual(result.teams[0].students[0].status, "on_team")

    def test_importing_into_a_team_with_a_double_space_creates_no_new_team(self):
        Team.objects.create(project=self.project, name="Team  Spaced")
        result = do_import(self.project, csv_text("Team  Spaced,Cy Code,cy-dev,,"))
        self.assertEqual(result.teams, 0)
        self.assertEqual(TeamMembership.objects.get(user__github_login="cy-dev").team.name, "Team  Spaced")

    def test_an_email_an_existing_account_will_not_take_is_noted(self):
        result = plan(self.project, csv_text("Team Alpha,Grace Example,sample-grace,grace@new.example,"))
        student = result.teams[0].students[0]
        self.assertIn("grace@new.example", student.note)
        self.assertIn("isn't saved", student.note)

    def test_the_email_and_name_notes_can_both_appear(self):
        result = plan(self.project, csv_text("Team Alpha,Gracie E.,sample-grace,grace@new.example,"))
        note = result.teams[0].students[0].note
        self.assertIn("Grace Example", note)
        self.assertIn("grace@new.example", note)

    def test_a_matching_or_blank_email_gets_no_note(self):
        User.objects.filter(pk=self.grace.pk).update(email="grace@example.com")
        for email in ("GRACE@example.com", ""):
            result = plan(self.project, csv_text(f"Team Alpha,Grace Example,sample-grace,{email},"))
            self.assertEqual(result.teams[0].students[0].note, "", email)

    def test_the_note_does_not_show_the_accounts_own_email(self):
        User.objects.filter(pk=self.grace.pk).update(email="private@example.com")
        result = plan(self.project, csv_text("Team Alpha,Grace Example,sample-grace,grace@new.example,"))
        self.assertNotIn("private@example.com", result.teams[0].students[0].note)

    def test_a_new_account_has_no_email_note(self):
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,ada@example.com,"))
        self.assertEqual(result.teams[0].students[0].note, "")

    def test_an_unreadable_repo_marks_its_row_and_its_team(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        result = plan(
            self.project,
            csv_text(
                "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/private",
                "Team Gamma,Bo Byte,bo-dev,,",
            ),
        )
        (team,) = result.teams
        self.assertEqual([s.status for s in team.students], ["error", "new"])
        self.assertEqual(team.repo_error_row, 2)

    def test_every_team_naming_an_unreadable_repo_is_marked(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        url = "https://github.com/acme/private"
        result = plan(
            self.project,
            csv_text(f"Team Gamma,Ada Lovelace,ada-dev,,{url}", f"Team Delta,Bo Byte,bo-dev,,{url}"),
        )
        self.assertEqual([team.repo_error_row for team in result.teams], [2, 2])
        self.assertEqual(self.get_repo.call_count, 1)

    def test_a_readable_repo_marks_nothing(self):
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/fine"))
        self.assertIsNone(result.teams[0].repo_error_row)

    def test_repos_already_checked_are_not_checked_again(self):
        from core.services import plan_team_import

        text = csv_text(
            "Team Gamma,Ada Lovelace,ada-dev,,https://github.com/ACME/one",
            "Team Delta,Bo Byte,bo-dev,,https://github.com/acme/two",
        )
        result = plan_team_import(self.project, text, checked_repos={"acme/one"})
        self.assertEqual(result.errors, [])
        self.get_repo.assert_called_once_with("acme/two")
        self.assertEqual(result.checked_repos, {"acme/one", "acme/two"})

    def test_a_repo_that_fails_its_check_is_not_listed_as_checked(self):
        self.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,ada-dev,,https://github.com/acme/private"))
        self.assertEqual(result.checked_repos, set())

    def test_the_number_of_queries_does_not_grow_with_the_rows(self):
        def queries_for(count):
            rows = ["Team Alpha,Grace Example,sample-grace,,"]
            rows += [f"Team Gamma,Student Number{n},student-{n},," for n in range(count)]
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(plan(self.project, csv_text(*rows)).errors, [])
            return len(queries)

        self.assertEqual(queries_for(2), queries_for(40))

    def test_existing_accounts_are_matched_regardless_of_case(self):
        result = plan(self.project, csv_text("Team Alpha,Grace Example,SAMPLE-GRACE,,"))
        self.assertEqual(result.teams[0].students[0].status, "on_team")

    def test_a_username_taken_in_another_case_is_still_an_error(self):
        make_user("taken-name")
        User.objects.filter(username="taken-name").update(github_login="someone-else")
        result = plan(self.project, csv_text("Team Gamma,Ada Lovelace,TAKEN-name,,"))
        self.assertEqual([e.row for e in result.errors], [2])

    def test_more_rows_than_one_lookup_can_hold_are_all_matched(self):
        rows = [f"Team Gamma,Student Number{n},student-{n},," for n in range(1200)]
        rows.append("Team Alpha,Grace Example,sample-grace,,")
        statuses = [s.status for team in plan(self.project, csv_text(*rows)).teams for s in team.students]
        self.assertEqual(statuses.count("new"), 1200)
        self.assertEqual(statuses.count("on_team"), 1)


class ImportTests(TeamSetupFixture):
    def setUp(self):
        super().setUp()
        self.text = csv_text(
            "Team Gamma,Ada Lovelace,ada-dev,ada@example.com,https://github.com/acme/gamma",
            "Team Gamma,Bo Byte,bo-dev,,",
            "Team Alpha,Cy Code,cy-dev,,",
        )

    def test_it_creates_the_teams_students_enrollments_and_repos(self):
        result = do_import(self.project, self.text)
        self.assertEqual((result.teams, result.students, result.repos), (1, 3, 1))
        gamma = Team.objects.get(project=self.project, name="Team Gamma")
        self.assertEqual(gamma.repos.get().full_name, "acme/gamma")
        ada = User.objects.get(github_login="ada-dev")
        self.assertEqual((ada.first_name, ada.last_name, ada.email), ("Ada", "Lovelace", "ada@example.com"))
        self.assertFalse(ada.has_usable_password())
        self.assertTrue(Enrollment.objects.filter(user=ada, course=self.course, role="student").exists())
        self.assertTrue(TeamMembership.objects.filter(user__github_login="cy-dev", team=self.team).exists())

    def test_it_rechecks_the_file_and_refuses_if_it_now_has_errors(self):
        from core.services import TeamImportError

        make_user("ada-dev")  # Appears after the preview...
        User.objects.filter(username="ada-dev").update(github_login="somebody-else")
        with self.assertRaises(TeamImportError):
            do_import(self.project, self.text)
        self.assertFalse(Team.objects.filter(name="Team Gamma").exists())

    def test_a_failure_part_way_saves_nothing(self):
        from core.services import ProjectSetupError, TeamImportError, add_student

        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ProjectSetupError("Boom.")
            return add_student(*args, **kwargs)

        with mock.patch("core.services.add_student", side_effect=flaky):
            with self.assertRaisesMessage(TeamImportError, "Boom."):
                do_import(self.project, self.text)
        self.assertFalse(Team.objects.filter(name="Team Gamma").exists())
        self.assertFalse(User.objects.filter(github_login="ada-dev").exists())
        self.assertFalse(Repo.objects.filter(full_name="acme/gamma").exists())

    def test_importing_the_same_file_twice_changes_nothing(self):
        do_import(self.project, self.text)
        before = (Team.objects.count(), User.objects.count(), Repo.objects.count(), TeamMembership.objects.count())
        self.assertTrue(plan(self.project, self.text).nothing_to_add)
        result = do_import(self.project, self.text)
        self.assertEqual((result.teams, result.students, result.repos), (0, 0, 0))
        after = (Team.objects.count(), User.objects.count(), Repo.objects.count(), TeamMembership.objects.count())
        self.assertEqual(before, after)

    def test_a_team_name_already_in_the_project_adds_to_that_team(self):
        do_import(self.project, csv_text("TEAM ALPHA,Cy Code,cy-dev,,"))
        self.assertEqual(Team.objects.filter(project=self.project, name__iexact="team alpha").count(), 1)
        self.assertEqual(self.team.memberships.count(), 2)

    def test_importing_checks_each_new_repo_once(self):
        do_import(self.project, self.text)
        self.assertEqual(self.get_repo.call_count, 1)
