"""Tests for US-30 (#107): a student's home lists their projects and any flags about them.

Not covered yet, because what they need isn't built: links to peer evaluations (US-37), the
"Dispute" link (US-31), and flags labelled by sprint (US-41). Until US-41, a flag is labelled
by its week. The link to offline contributions (US-33) is tested with that feature.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from core.models import Course, Enrollment, Project, Team, TeamMembership
from dashboard.models import Flag, FlagDispute

from .test_course_overview import enroll, make_user

Role = Enrollment.Role


def student_home_url():
    return reverse("dashboard:student_home")


def make_flag(team, user, week_start, reason, severity=Flag.Severity.FLAG):
    return Flag.objects.create(
        team=team,
        user=user,
        rule="low-requirement-share",
        reason=reason,
        severity=severity,
        week_start=week_start,
    )


def make_dispute(team, student, week_start, flag_reason="Disputed reason.", **fields):
    return FlagDispute.objects.create(
        team=team,
        student=student,
        rule="low-requirement-share",
        week_start=week_start,
        flag_reason=flag_reason,
        severity=Flag.Severity.FLAG,
        explanation="I paired on most of it.",
        **fields,
    )


class StudentHomeFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.other_team = Team.objects.create(project=cls.project, name="Team Beta")

        cls.student = make_user("sample-casey", "Casey", "Sample")
        cls.teammate = make_user("sample-riley", "Riley", "Sample")
        cls.stranger = make_user("sample-jordan", "Jordan", "Sample")
        for user in (cls.student, cls.teammate, cls.stranger):
            enroll(user, cls.course, Role.STUDENT)
        TeamMembership.objects.create(user=cls.student, team=cls.team)
        TeamMembership.objects.create(user=cls.teammate, team=cls.team)
        TeamMembership.objects.create(user=cls.stranger, team=cls.other_team)

    def get_home(self, user=None):
        self.client.force_login(user or self.student)
        return self.client.get(student_home_url())


class MyProjectsTests(StudentHomeFixture):
    def test_lists_my_project_with_my_team_and_teammates(self):
        response = self.get_home()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project One")
        self.assertContains(response, "Team Alpha")
        self.assertContains(response, "Riley Sample")

    def test_does_not_list_other_teams_or_their_students(self):
        response = self.get_home()
        self.assertNotContains(response, "Team Beta")
        self.assertNotContains(response, "Jordan Sample")

    def test_groups_projects_by_course(self):
        later = Course.objects.create(name="CS 3240, Spring 2027")
        enroll(self.student, later, Role.STUDENT)
        project = Project.objects.create(course=later, name="Project Two")
        team = Team.objects.create(project=project, name="Team Gamma")
        TeamMembership.objects.create(user=self.student, team=team)

        content = self.get_home().content.decode()
        # Courses by name, each followed by its own project.
        self.assertLess(content.index("CS 3240, Spring 2027"), content.index("Project Two"))
        self.assertLess(content.index("Project Two"), content.index("CS 4501, Fall 2026"))
        self.assertLess(content.index("CS 4501, Fall 2026"), content.index("Project One"))

    def test_two_projects_in_one_course_share_its_heading(self):
        project = Project.objects.create(course=self.course, name="Project Two")
        team = Team.objects.create(project=project, name="Team Gamma")
        TeamMembership.objects.create(user=self.student, team=team)

        content = self.get_home().content.decode()
        self.assertEqual(content.count("CS 4501, Fall 2026"), 1)
        self.assertLess(content.index("Project One"), content.index("Project Two"))

    def test_a_student_on_no_team_sees_a_message(self):
        loner = make_user("sample-sam")
        enroll(loner, self.course, Role.STUDENT)
        response = self.get_home(loner)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You aren't on a team yet")


class FlagsAboutMeTests(StudentHomeFixture):
    def test_lists_my_flag_with_its_severity_week_and_reason(self):
        reason = "Casey Sample did 10% of the team's requirement-tied work in the week of Nov 2."
        make_flag(self.team, self.student, date(2026, 11, 2), reason)
        response = self.get_home()
        self.assertContains(response, escape(reason))
        self.assertContains(response, "Flag")
        self.assertContains(response, "Week of Nov 2, 2026")

    def test_lists_warnings_too(self):
        make_flag(
            self.team, self.student, date(2026, 11, 2), "A warning reason.",
            Flag.Severity.WARNING,
        )
        response = self.get_home()
        self.assertContains(response, "A warning reason.")
        self.assertContains(response, "Warning")

    def test_newest_first(self):
        make_flag(self.team, self.student, date(2026, 10, 26), "Older reason.")
        make_flag(self.team, self.student, date(2026, 11, 2), "Newer reason.")
        content = self.get_home().content.decode()
        self.assertLess(content.index("Newer reason."), content.index("Older reason."))

    def test_shows_my_disputes_status(self):
        week = date(2026, 11, 2)
        make_flag(self.team, self.student, week, "Disputed reason.")
        FlagDispute.objects.create(
            team=self.team,
            student=self.student,
            rule="low-requirement-share",
            week_start=week,
            flag_reason="Disputed reason.",
            severity=Flag.Severity.FLAG,
            explanation="I paired on most of it.",
            status=FlagDispute.Status.FLAG_KEPT,
        )
        response = self.get_home()
        self.assertContains(response, "Disputed reason.")
        self.assertContains(response, "Flag kept")

    def test_a_withdrawn_flag_reads_as_instructors_see_it_without_its_reason(self):
        week = date(2026, 11, 2)
        make_flag(self.team, self.student, week, "Withdrawn reason.")
        make_dispute(self.team, self.student, week, status=FlagDispute.Status.FLAG_WITHDRAWN)
        response = self.get_home()
        self.assertContains(response, "Withdrawn after dispute")
        self.assertContains(response, "Flag for the week of Nov 2, 2026")
        self.assertNotContains(response, "Withdrawn reason.")

    def test_shows_the_instructors_note_on_a_decided_dispute(self):
        week = date(2026, 11, 2)
        make_flag(self.team, self.student, week, "Kept reason.")
        make_dispute(
            self.team, self.student, week,
            status=FlagDispute.Status.FLAG_KEPT,
            resolution_note="Your commits that week were all formatting.",
        )
        response = self.get_home()
        self.assertContains(response, "Note from your instructor")
        self.assertContains(response, "Your commits that week were all formatting.")

    def test_an_open_dispute_shows_no_note(self):
        week = date(2026, 11, 2)
        make_flag(self.team, self.student, week, "Open reason.")
        make_dispute(self.team, self.student, week)
        self.assertNotContains(self.get_home(), "Note from your instructor")

    def test_a_dispute_whose_flag_is_no_longer_raised_still_shows(self):
        make_dispute(self.team, self.student, date(2026, 11, 2), flag_reason="Old reason.")
        response = self.get_home()
        self.assertContains(response, "Flag no longer raised")
        self.assertContains(response, "Week of Nov 2, 2026")
        self.assertContains(response, "Old reason.")
        self.assertContains(response, "Disputed · Open")
        self.assertNotContains(response, "No flags about you")

    def test_an_unraised_decided_dispute_shows_its_note_and_hides_a_withdrawn_reason(self):
        make_dispute(
            self.team, self.student, date(2026, 11, 2),
            flag_reason="Old reason.",
            status=FlagDispute.Status.FLAG_WITHDRAWN,
            resolution_note="Agreed, you paired on it.",
        )
        response = self.get_home()
        self.assertContains(response, "Flag no longer raised")
        self.assertNotContains(response, "Old reason.")
        self.assertContains(response, "Disputed · Flag withdrawn")
        self.assertContains(response, "Agreed, you paired on it.")

    def test_never_shows_a_teammates_unraised_dispute(self):
        make_dispute(self.team, self.teammate, date(2026, 11, 2), flag_reason="Teammate's.")
        response = self.get_home()
        self.assertNotContains(response, "Teammate&#x27;s.")
        self.assertNotContains(response, "Flag no longer raised")
        self.assertContains(response, "No flags about you")

    def test_never_shows_flags_about_teammates_or_the_whole_team(self):
        week = date(2026, 11, 2)
        make_flag(self.team, self.teammate, week, "About the teammate.")
        make_flag(self.team, None, week, "About the whole team.")
        make_flag(self.other_team, self.stranger, week, "About another team.")
        response = self.get_home()
        self.assertNotContains(response, "About the teammate.")
        self.assertNotContains(response, "About the whole team.")
        self.assertNotContains(response, "About another team.")

    def test_a_student_with_no_flags_sees_no_flags_about_you(self):
        make_flag(self.team, self.teammate, date(2026, 11, 2), "About the teammate.")
        self.assertContains(self.get_home(), "No flags about you")


class StudentHomeAccessTests(StudentHomeFixture):
    def test_nav_links_only_to_the_student_home_and_logout(self):
        response = self.get_home()
        # The brand goes to the home page, which sends a student straight to the student home.
        self.assertContains(response, f'href="{reverse("dashboard:home")}"')
        self.assertContains(response, reverse("logout"))
        self.assertNotContains(
            response, reverse("dashboard:course_overview", args=[self.course.id])
        )
        self.assertRedirects(self.client.get(reverse("dashboard:home")), student_home_url())

    def test_someone_staff_in_another_course_can_get_back_to_it(self):
        staffed = Course.objects.create(name="CS 2100, Fall 2026")
        enroll(self.student, staffed, Role.TA)
        response = self.get_home()
        self.assertContains(response, f'href="{reverse("dashboard:home")}"')
        home = self.client.get(reverse("dashboard:home"))
        self.assertContains(home, reverse("dashboard:course_overview", args=[staffed.id]))

    def test_never_shows_a_team_in_a_course_im_not_a_student_in(self):
        # A team membership alone isn't enough: the course enrollment must say student.
        other_course = Course.objects.create(name="CS 2100, Fall 2026")
        project = Project.objects.create(course=other_course, name="Elsewhere Project")
        team = Team.objects.create(project=project, name="Team Elsewhere")
        TeamMembership.objects.create(user=self.student, team=team)
        make_flag(team, self.student, date(2026, 11, 2), "Flag from elsewhere.")
        response = self.get_home()
        self.assertNotContains(response, "Elsewhere Project")
        self.assertNotContains(response, "Flag from elsewhere.")

    def test_403_for_instructors_and_tas_who_are_not_students(self):
        for role in (Role.INSTRUCTOR, Role.TA):
            with self.subTest(role=role):
                user = make_user(f"sample-{role}")
                enroll(user, self.course, role)
                self.assertEqual(self.get_home(user).status_code, 403)

    def test_403_for_someone_enrolled_nowhere(self):
        self.assertEqual(self.get_home(make_user("sample-none")).status_code, 403)

    def test_someone_staff_in_one_course_sees_only_their_student_courses(self):
        staffed = Course.objects.create(name="CS 2100, Fall 2026")
        project = Project.objects.create(course=staffed, name="Staffed Project")
        Team.objects.create(project=project, name="Team Staffed")
        enroll(self.student, staffed, Role.TA)
        response = self.get_home()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project One")
        self.assertNotContains(response, "CS 2100, Fall 2026")
        self.assertNotContains(response, "Staffed Project")

    def test_logged_out_visitors_are_sent_to_login(self):
        response = self.client.get(student_home_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
