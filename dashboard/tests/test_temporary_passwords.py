"""Tests for US-29 (#106): log in with a temporary password from my instructor.

These drive the real pages, so they check what instructors and students see. The rules inside
`set_temporary_password` and the forced password change have their own tests in
`core/tests/test_student_logins.py`.

URLs are reversed per test rather than at module level, so each criterion passes or fails
on its own instead of the whole module failing on a single NoReverseMatch.
"""

import html
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.test import Client, TestCase
from django.urls import reverse

from core import services as setup
from core.models import Course, Enrollment, Project, Team, TeamMembership

from .test_course_overview import enroll, make_user, overview_url, team_url
from .test_generate_summary import page_messages

Role = Enrollment.Role
User = get_user_model()

# The password `make_user` gives every account.
CURRENT_PASSWORD = "not-a-real-password"
TEMPORARY_PASSWORD = "river-Lantern-9317"
OWN_PASSWORD = "a-fresh-Passphrase-42"
REJECTED_PASSWORD = "short1x"

HAS_SET_OWN = "has logged in and set their own password"
HAS_NOT_SET_OWN = "hasn't logged in and set their own password yet"


def student_add_url(team_id):
    return reverse("dashboard:student_add", args=[team_id])


def membership_edit_url(membership_id):
    return reverse("dashboard:membership_edit", args=[membership_id])


def password_change_url():
    return reverse("password_change")


def rule_cases(github_login):
    """A password that breaks each of Django's rules, with the words naming the rule."""
    return [
        (REJECTED_PASSWORD, "too short"),
        ("password123", "too common"),
        ("83749283749", "entirely numeric"),
        (f"{github_login}1", "too similar"),
    ]


class TemporaryPasswordFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.other_course = Course.objects.create(name="CS 3240, Fall 2026")
        cls.project = Project.objects.create(course=cls.course, name="Project One")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.other_team = Team.objects.create(project=cls.project, name="Team Beta")

        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.grace = make_user("sample-grace", "Grace", "Example")
        enroll(cls.grace, cls.course, Role.STUDENT)
        cls.membership = TeamMembership.objects.create(user=cls.grace, team=cls.team)

    def setUp(self):
        self.client.force_login(self.instructor)

    def edit(self, membership=None, client=None, **fields):
        data = {"full_name": "Grace Example"}
        data.update(fields)
        membership = membership or self.membership
        return (client or self.client).post(
            membership_edit_url(membership.id), data, follow=True
        )

    def add(self, client=None, **fields):
        data = {"full_name": "Ada Example", "github_login": "sample-ada", "email": ""}
        data.update(fields)
        return (client or self.client).post(student_add_url(self.team.id), data, follow=True)

    def stored(self, user=None):
        return User.objects.get(pk=(user or self.grace).pk)

    def team_page(self, query=""):
        return self.client.get(team_url(self.team.id) + query)

    def edit_form(self, membership=None):
        """The edit form's HTML, with entities decoded so wording can be matched."""
        membership = membership or self.membership
        content = self.team_page(f"?edit_student={membership.id}").content.decode()
        return self.form_in(content, membership_edit_url(membership.id))

    def add_form(self):
        return self.form_in(self.team_page().content.decode(), student_add_url(self.team.id))

    def form_in(self, content, action):
        start = content.index(f'action="{action}"')
        return html.unescape(content[start : content.index("</form>", start)])

    def password_input(self, form):
        tag = re.search(r'<input[^>]*name="temporary_password"[^>]*>', form)
        self.assertIsNotNone(tag, "The form has no temporary_password field.")
        return tag.group(0)

    def assert_password_untouched(self, user=None):
        stored = self.stored(user)
        self.assertTrue(stored.check_password(CURRENT_PASSWORD))
        self.assertIs(stored.must_change_password, False)

    def assert_has_temporary_password(self, user=None):
        stored = self.stored(user)
        self.assertTrue(stored.check_password(TEMPORARY_PASSWORD))
        self.assertIs(stored.must_change_password, True)


class PasswordFieldTests(TemporaryPasswordFixture):
    def test_edit_form_has_a_temporary_password_field(self):
        form = self.edit_form()

        self.assertIn("Temporary password", form)
        self.password_input(form)

    def test_add_form_has_a_temporary_password_field(self):
        form = self.add_form()

        self.assertIn("Temporary password", form)
        self.password_input(form)

    def test_the_field_is_optional_on_both_forms(self):
        for name, form in [("edit", self.edit_form()), ("add", self.add_form())]:
            with self.subTest(form=name):
                self.assertNotRegex(self.password_input(form), r"\brequired\b")

    def test_browsers_are_told_not_to_fill_in_a_saved_password(self):
        """An autofilled instructor password would be saved as the student's."""
        for name, form in [("edit", self.edit_form()), ("add", self.add_form())]:
            with self.subTest(form=name):
                self.assertRegex(
                    self.password_input(form), r'autocomplete="(new-password|off)"'
                )


class EditFormPasswordTests(TemporaryPasswordFixture):
    def test_saving_a_typed_password_sets_it_as_temporary(self):
        self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.assert_has_temporary_password()

    def test_the_name_is_saved_along_with_the_password(self):
        self.edit(full_name="Grace Hopper", temporary_password=TEMPORARY_PASSWORD)

        self.assertEqual(self.stored().get_full_name(), "Grace Hopper")
        self.assert_has_temporary_password()

    def test_the_success_message_mentions_the_password(self):
        response = self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("password", page_messages(response).lower())

    def test_a_blank_field_keeps_the_current_password(self):
        response = self.edit(full_name="Grace Hopper", temporary_password="")

        self.assertEqual(self.stored().get_full_name(), "Grace Hopper")
        self.assert_password_untouched()
        self.assertNotIn("password", page_messages(response).lower())

    def test_a_missing_field_keeps_the_current_password(self):
        self.edit(full_name="Grace Hopper")

        self.assert_password_untouched()

    def test_a_blank_field_leaves_a_pending_temporary_password_in_place(self):
        self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.edit(temporary_password="")

        self.assert_has_temporary_password()

    def test_a_password_of_only_spaces_is_never_saved(self):
        self.edit(temporary_password="            ")

        self.assert_password_untouched()

    def test_each_failed_rule_is_named_and_nothing_is_saved(self):
        for password, rule in rule_cases("sample-grace"):
            with self.subTest(rule=rule):
                response = self.edit(full_name="Grace Hopper", temporary_password=password)

                self.assertIn(rule, page_messages(response))
                self.assertEqual(self.stored().get_full_name(), "Grace Example")
                self.assert_password_untouched()

    def test_a_refused_name_means_the_password_is_not_saved_either(self):
        response = self.edit(full_name="   ", temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("Enter the student's full name.", page_messages(response))
        self.assert_password_untouched()

    def test_it_replaces_a_password_the_student_set_themselves(self):
        self.grace.set_password(OWN_PASSWORD)
        self.grace.save()

        self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.assertFalse(self.stored().check_password(OWN_PASSWORD))
        self.assert_has_temporary_password()


class AddFormPasswordTests(TemporaryPasswordFixture):
    def added(self):
        return User.objects.get(github_login="sample-ada")

    def test_a_new_student_gets_the_typed_password_as_temporary(self):
        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assert_has_temporary_password(self.added())
        self.assertTrue(self.team.memberships.filter(user=self.added()).exists())

    def test_the_success_message_mentions_the_password(self):
        response = self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("password", page_messages(response).lower())

    def test_a_blank_field_leaves_a_new_student_unable_to_log_in(self):
        self.add(temporary_password="")

        self.assertFalse(self.added().has_usable_password())
        self.assertIs(self.added().must_change_password, False)

    def test_a_blank_field_keeps_an_existing_accounts_password(self):
        existing = make_user("sample-ada", "Ada", "Example")
        enroll(existing, self.course, Role.STUDENT)

        self.add(temporary_password="")

        self.assertTrue(self.team.memberships.filter(user=existing).exists())
        self.assert_password_untouched(existing)

    def test_an_existing_account_gets_the_typed_password(self):
        existing = make_user("sample-ada", "Ada", "Example")
        enroll(existing, self.course, Role.STUDENT)

        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertTrue(self.team.memberships.filter(user=existing).exists())
        self.assert_has_temporary_password(existing)

    def test_each_failed_rule_is_named_and_no_account_is_created(self):
        for index in range(len(rule_cases(""))):
            # A login per rule, so one failure can't change what the next rule sees.
            login = f"sample-ada-{index}"
            password, rule = rule_cases(login)[index]
            with self.subTest(rule=rule):
                response = self.add(github_login=login, temporary_password=password)

                self.assertIn(rule, page_messages(response))
                self.assertFalse(User.objects.filter(github_login=login).exists())
                self.assertEqual(self.team.memberships.count(), 1)

    def test_a_rejected_password_doesnt_add_an_existing_account(self):
        enroll(self.instructor, self.other_course, Role.INSTRUCTOR)
        existing = make_user("sample-ada", "Ada", "Example")
        enroll(existing, self.other_course, Role.STUDENT)

        response = self.add(temporary_password=REJECTED_PASSWORD)

        self.assertIn("too short", page_messages(response))
        self.assertFalse(self.team.memberships.filter(user=existing).exists())
        self.assertFalse(Enrollment.objects.filter(user=existing, course=self.course).exists())
        self.assert_password_untouched(existing)

    def test_a_refused_student_doesnt_get_the_password(self):
        response = self.add(github_login="not a login", temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("valid GitHub username", page_messages(response))
        self.assertEqual(User.objects.filter(must_change_password=True).count(), 0)

    def test_an_account_that_cant_join_this_team_keeps_its_password(self):
        ada = make_user("sample-ada", "Ada", "Example")
        enroll(ada, self.course, Role.STUDENT)
        TeamMembership.objects.create(user=ada, team=self.team)

        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assert_password_untouched(ada)


class PasswordNeverShownTests(TemporaryPasswordFixture):
    def assert_absent(self, response, secret):
        self.assertNotContains(response, secret)

    def test_the_page_after_saving_doesnt_show_it(self):
        self.assert_absent(self.edit(temporary_password=TEMPORARY_PASSWORD), TEMPORARY_PASSWORD)
        self.assert_absent(self.add(temporary_password=TEMPORARY_PASSWORD), TEMPORARY_PASSWORD)

    def test_the_edit_form_doesnt_show_it_or_its_hash(self):
        self.edit(temporary_password=TEMPORARY_PASSWORD)

        form = self.edit_form()

        self.assertNotIn(TEMPORARY_PASSWORD, form)
        self.assertNotIn(self.stored().password, form)
        self.assertNotRegex(self.password_input(form), r'value="[^"]+"')

    def test_a_rejected_password_isnt_repeated(self):
        self.assert_absent(self.edit(temporary_password=REJECTED_PASSWORD), REJECTED_PASSWORD)
        self.assert_absent(self.add(temporary_password=REJECTED_PASSWORD), REJECTED_PASSWORD)

    def assert_status(self, form, expected, other):
        form = form.lower()
        self.assertIn(expected, form)
        self.assertNotIn(other, form)

    def test_edit_form_says_when_a_temporary_password_is_still_pending(self):
        self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.assert_status(self.edit_form(), HAS_NOT_SET_OWN, HAS_SET_OWN)

    def test_edit_form_says_when_a_student_was_never_given_a_password(self):
        self.add(temporary_password="")
        membership = self.team.memberships.get(user__github_login="sample-ada")

        self.assert_status(self.edit_form(membership), HAS_NOT_SET_OWN, HAS_SET_OWN)

    def test_edit_form_says_when_the_student_has_set_their_own(self):
        Client().force_login(self.grace)

        self.assert_status(self.edit_form(), HAS_SET_OWN, HAS_NOT_SET_OWN)

    def test_a_password_nobody_has_logged_in_with_doesnt_count_as_their_own(self):
        """A usable password alone says nothing about who chose it."""
        self.assert_status(self.edit_form(), HAS_NOT_SET_OWN, HAS_SET_OWN)

    def test_a_password_set_in_the_admin_doesnt_count_as_their_own(self):
        """Even for a student who has logged in before, since the admin knows this one."""
        Client().force_login(self.grace)
        admin = Client()
        admin.force_login(
            User.objects.create_superuser(username="sample-root", github_login="sample-root")
        )

        response = admin.post(
            reverse("admin:auth_user_password_change", args=[self.grace.pk]),
            {
                "usable_password": "true",
                "password1": TEMPORARY_PASSWORD,
                "password2": TEMPORARY_PASSWORD,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assert_status(self.edit_form(), HAS_NOT_SET_OWN, HAS_SET_OWN)


class RefusedAccountTests(TemporaryPasswordFixture):
    """Staff accounts and anyone running a course are left to the admin, as in US-27."""

    def accounts(self):
        """Each kind of account that's refused, as (description, user)."""
        cases = []
        for field in ("is_staff", "is_superuser"):
            user = make_user(f"sample-{field.replace('_', '-')}")
            User.objects.filter(pk=user.pk).update(**{field: True})
            cases.append((field, user))
        for role in (Role.INSTRUCTOR, Role.TA):
            user = make_user(f"sample-other-{role}")
            enroll(user, self.other_course, role)
            cases.append((f"{role} in another course", user))
        return cases

    def test_edit_form_refuses_and_points_to_the_admin(self):
        for description, user in self.accounts():
            with self.subTest(account=description):
                enroll(user, self.course, Role.STUDENT)
                membership = TeamMembership.objects.create(user=user, team=self.team)

                response = self.edit(
                    membership, full_name="New Name", temporary_password=TEMPORARY_PASSWORD
                )

                self.assertIn("admin", page_messages(response))
                self.assert_password_untouched(user)
                self.assertEqual(self.stored(user).first_name, "")

    def test_add_form_refuses_and_points_to_the_admin(self):
        for description, user in self.accounts():
            with self.subTest(account=description):
                response = self.add(
                    github_login=user.github_login, temporary_password=TEMPORARY_PASSWORD
                )

                self.assertIn("admin", page_messages(response))
                self.assert_password_untouched(user)
                self.assertFalse(self.team.memberships.filter(user=user).exists())
                self.assertFalse(
                    Enrollment.objects.filter(user=user, course=self.course).exists()
                )

    def test_add_form_with_no_password_still_adds_them(self):
        """Refusing is about the password. Putting them on a team is unchanged."""
        for description, user in self.accounts():
            with self.subTest(account=description):
                self.add(github_login=user.github_login, temporary_password="")

                self.assertTrue(self.team.memberships.filter(user=user).exists())
                self.assert_password_untouched(user)


class StudentInAnotherCourseTests(TemporaryPasswordFixture):
    """An instructor can't take over an account that belongs to a course they don't teach."""

    def student_elsewhere(self):
        ada = make_user("sample-ada", "Ada", "Example")
        enroll(ada, self.other_course, Role.STUDENT)
        return ada

    def assert_add_refused(self, ada):
        response = self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("admin", page_messages(response))
        self.assert_password_untouched(ada)
        self.assertFalse(self.team.memberships.filter(user=ada).exists())
        self.assertFalse(Enrollment.objects.filter(user=ada, course=self.course).exists())

    def test_add_form_refuses_and_points_to_the_admin(self):
        self.assert_add_refused(self.student_elsewhere())

    def test_the_message_doesnt_name_the_other_course(self):
        self.student_elsewhere()

        response = self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertNotIn(self.other_course.name, page_messages(response))

    def test_edit_form_refuses_and_saves_nothing(self):
        enroll(self.grace, self.other_course, Role.STUDENT)

        response = self.edit(full_name="Grace Hopper", temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("admin", page_messages(response))
        self.assert_password_untouched()
        self.assertEqual(self.stored().get_full_name(), "Grace Example")

    def test_adding_them_first_doesnt_get_around_it(self):
        ada = self.student_elsewhere()
        self.add(temporary_password="")
        membership = self.team.memberships.get(user=ada)

        response = self.edit(
            membership, full_name="Ada Example", temporary_password=TEMPORARY_PASSWORD
        )

        self.assertIn("admin", page_messages(response))
        self.assert_password_untouched(ada)

    def test_being_a_ta_of_the_other_course_isnt_enough(self):
        enroll(self.instructor, self.other_course, Role.TA)

        self.assert_add_refused(self.student_elsewhere())

    def test_an_instructor_of_both_courses_can_set_it(self):
        enroll(self.instructor, self.other_course, Role.INSTRUCTOR)
        ada = self.student_elsewhere()

        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertTrue(self.team.memberships.filter(user=ada).exists())
        self.assert_has_temporary_password(ada)

    def test_with_no_password_they_can_still_be_added_and_renamed(self):
        ada = self.student_elsewhere()

        self.add(temporary_password="")
        membership = self.team.memberships.get(user=ada)
        self.edit(membership, full_name="Ada Renamed", temporary_password="")

        self.assertEqual(self.stored(ada).get_full_name(), "Ada Renamed")
        self.assert_password_untouched(ada)

    def test_the_services_refuse_a_password_when_no_instructor_is_given(self):
        ada = self.student_elsewhere()
        enroll(ada, self.course, Role.STUDENT)

        with self.assertRaises(setup.ProjectSetupError):
            setup.edit_student(ada, "Ada Example", TEMPORARY_PASSWORD)
        with self.assertRaises(setup.ProjectSetupError):
            setup.add_student(
                self.team, "Ada Example", "sample-ada", temporary_password=TEMPORARY_PASSWORD
            )

        self.assert_password_untouched(ada)


class UnenrolledAccountTests(TemporaryPasswordFixture):
    """An account with a password and no enrollments may belong to anyone. Adding it to a team
    would make it look like the instructor's own student, so an admin has to vouch for it."""

    def unenrolled(self):
        return make_user("sample-ada", "Ada", "Example")

    def assert_add_refused(self, ada, response):
        self.assertIn("admin", page_messages(response))
        self.assert_password_untouched(ada)
        self.assertFalse(self.team.memberships.filter(user=ada).exists())
        self.assertFalse(ada.enrollments.exists())

    def test_add_form_refuses_a_typed_password(self):
        ada = self.unenrolled()

        self.assert_add_refused(ada, self.add(temporary_password=TEMPORARY_PASSWORD))

    def test_adding_them_with_a_blank_field_first_doesnt_get_around_it(self):
        """Otherwise the edit form would set the password once they're enrolled here."""
        ada = self.unenrolled()

        self.assert_add_refused(ada, self.add(temporary_password=""))

    def test_a_deleted_enrollment_leaves_the_account_protected(self):
        ada = self.unenrolled()
        enroll(ada, self.other_course, Role.STUDENT).delete()

        self.assert_add_refused(ada, self.add(temporary_password=TEMPORARY_PASSWORD))

    def test_once_an_admin_enrolls_them_the_instructor_can_set_it(self):
        ada = self.unenrolled()
        enroll(ada, self.course, Role.STUDENT)

        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assert_has_temporary_password(ada)

    def test_an_unenrolled_account_with_no_password_can_be_given_one(self):
        """Nobody can log in to it, so there's nothing to take over."""
        ada = self.unenrolled()
        ada.set_unusable_password()
        ada.save()

        self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertTrue(self.team.memberships.filter(user=ada).exists())
        self.assert_has_temporary_password(ada)

    def test_the_services_refuse_it_too(self):
        ada = self.unenrolled()

        with self.assertRaises(setup.ProjectSetupError):
            setup.add_student(self.team, "Ada Example", "sample-ada", instructor=self.instructor)

        self.assertFalse(ada.enrollments.exists())


class LoginUsernameTests(TemporaryPasswordFixture):
    """Logging in uses `username`, which an older account can have different from GitHub's."""

    def jdoe(self):
        account = User.objects.create_user(
            username="jdoe", password=CURRENT_PASSWORD, github_login="sample-ada"
        )
        enroll(account, self.course, Role.STUDENT)
        return account

    def joined(self, account):
        return self.team.memberships.create(user=account)

    def test_adding_with_a_password_names_the_username_to_share(self):
        self.jdoe()

        response = self.add(temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("jdoe", page_messages(response))

    def test_editing_with_a_password_names_the_username_to_share(self):
        membership = self.joined(self.jdoe())

        response = self.edit(membership, temporary_password=TEMPORARY_PASSWORD)

        self.assertIn("jdoe", page_messages(response))

    def test_the_named_username_is_the_one_that_logs_in(self):
        self.jdoe()
        self.add(temporary_password=TEMPORARY_PASSWORD)
        student = Client()

        response = student.post(
            reverse("login"), {"username": "jdoe", "password": TEMPORARY_PASSWORD}, follow=True
        )

        self.assertTrue(response.context["user"].is_authenticated)

    def test_nothing_is_added_when_no_password_was_set(self):
        membership = self.joined(self.jdoe())

        response = self.edit(membership, temporary_password="")

        self.assertNotIn("jdoe", page_messages(response))

    def test_nothing_is_added_when_the_two_match(self):
        response = self.edit(temporary_password=TEMPORARY_PASSWORD)

        self.assertNotIn("sample-grace", page_messages(response))

    def test_the_edit_form_shows_a_username_that_differs(self):
        membership = self.joined(self.jdoe())

        self.assertIn("<code>jdoe</code>", self.edit_form(membership))

    def test_the_edit_form_shows_no_second_name_when_the_two_match(self):
        self.assertEqual(self.edit_form().count("<code>sample-grace</code>"), 1)


class WhitespaceInPasswordTests(TemporaryPasswordFixture):
    BLANKS = ["            ", "\t\t\t\t\t\t\t\t", " \n \n \n \n"]
    ONLY_SPACES = "only spaces"

    def login_works(self, username, password):
        client = Client()
        return client.login(username=username, password=password)

    def test_edit_form_refuses_a_password_of_only_whitespace(self):
        for blank in self.BLANKS:
            with self.subTest(blank=blank):
                response = self.edit(full_name="Grace Hopper", temporary_password=blank)

                self.assertIn(self.ONLY_SPACES, page_messages(response))
                self.assertNotIn("Updated", page_messages(response))
                self.assertEqual(self.stored().get_full_name(), "Grace Example")
                self.assert_password_untouched()

    def test_add_form_refuses_a_password_of_only_whitespace(self):
        for blank in self.BLANKS:
            with self.subTest(blank=blank):
                response = self.add(temporary_password=blank)

                self.assertIn(self.ONLY_SPACES, page_messages(response))
                self.assertNotIn("Added", page_messages(response))
                self.assertFalse(User.objects.filter(github_login="sample-ada").exists())

    def test_edit_form_strips_whitespace_around_a_pasted_password(self):
        self.edit(temporary_password=f"  {TEMPORARY_PASSWORD} \n")

        self.assert_has_temporary_password()
        self.assertTrue(self.login_works("sample-grace", TEMPORARY_PASSWORD))

    def test_add_form_strips_whitespace_around_a_pasted_password(self):
        self.add(temporary_password=f"\t{TEMPORARY_PASSWORD}\r\n")

        self.assert_has_temporary_password(User.objects.get(github_login="sample-ada"))
        self.assertTrue(self.login_works("sample-ada", TEMPORARY_PASSWORD))

    def test_spaces_inside_a_password_are_kept(self):
        self.edit(temporary_password="river Lantern 9317")

        self.assertTrue(self.stored().check_password("river Lantern 9317"))

    def test_the_rules_judge_the_password_without_the_whitespace(self):
        response = self.edit(temporary_password=f"     {REJECTED_PASSWORD}     ")

        self.assertIn("too short", page_messages(response))
        self.assert_password_untouched()


class StudentLoginTests(TemporaryPasswordFixture):
    def setUp(self):
        super().setUp()
        self.student = Client()

    def log_in(self, username="sample-ada", password=TEMPORARY_PASSWORD):
        return self.student.post(
            reverse("login"), {"username": username, "password": password}, follow=True
        )

    def choose_password(self, old=TEMPORARY_PASSWORD, new=OWN_PASSWORD):
        return self.student.post(
            password_change_url(),
            {"old_password": old, "new_password1": new, "new_password2": new},
            follow=True,
        )

    def path(self, response):
        return response.request["PATH_INFO"]

    def add_ada_and_log_in(self):
        self.add(temporary_password=TEMPORARY_PASSWORD)
        return self.log_in()

    def test_a_student_logs_in_with_github_username_and_temporary_password(self):
        response = self.add_ada_and_log_in()

        self.assertTrue(response.context["user"].is_authenticated)
        self.assertEqual(response.context["user"].username, "sample-ada")

    def test_they_land_on_the_password_change_page_first(self):
        self.assertEqual(self.path(self.add_ada_and_log_in()), password_change_url())

    def test_every_other_page_sends_them_back_until_they_change_it(self):
        self.add_ada_and_log_in()

        for url in [
            reverse("dashboard:home"),
            reverse("dashboard:student_home"),
            overview_url(self.course.id),
            team_url(self.team.id),
        ]:
            with self.subTest(url=url):
                response = self.student.get(url)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url.split("?")[0], password_change_url())

    def test_they_can_log_out_without_changing_it(self):
        self.add_ada_and_log_in()

        response = self.student.post(reverse("logout"))

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url.split("?")[0], password_change_url())
        self.assertNotIn("_auth_user_id", self.student.session)

    def test_after_changing_it_they_land_on_the_student_home(self):
        self.add_ada_and_log_in()

        response = self.choose_password()

        self.assertEqual(self.path(response), reverse("dashboard:student_home"))
        self.assertIs(User.objects.get(username="sample-ada").must_change_password, False)

    def test_the_edit_form_reports_the_change_to_the_instructor(self):
        self.add_ada_and_log_in()
        self.choose_password()
        membership = self.team.memberships.get(user__github_login="sample-ada")

        form = self.edit_form(membership).lower()

        self.assertIn(HAS_SET_OWN, form)
        self.assertNotIn(HAS_NOT_SET_OWN, form)

    def test_only_the_new_password_works_afterwards(self):
        self.add_ada_and_log_in()
        self.choose_password()
        self.student.post(reverse("logout"))

        self.assertFalse(self.log_in().context["user"].is_authenticated)
        response = self.log_in(password=OWN_PASSWORD)

        self.assertEqual(self.path(response), reverse("dashboard:student_home"))

    def test_a_replaced_password_must_be_changed_again_at_the_next_login(self):
        self.add_ada_and_log_in()
        self.choose_password()
        self.student.post(reverse("logout"))
        membership = self.team.memberships.get(user__github_login="sample-ada")
        replacement = "second-Harbor-5521"

        self.edit(membership, full_name="Ada Example", temporary_password=replacement)

        self.assertFalse(self.log_in(password=OWN_PASSWORD).context["user"].is_authenticated)
        response = self.log_in(password=replacement)
        self.assertEqual(self.path(response), password_change_url())

    def test_a_failed_login_never_reveals_whether_the_username_exists(self):
        self.add(temporary_password=TEMPORARY_PASSWORD)
        self.add(full_name="No Password", github_login="sample-unset", temporary_password="")
        expected = AuthenticationForm.error_messages["invalid_login"] % {"username": "username"}

        for description, username in [
            ("right username", "sample-ada"),
            ("no such username", "sample-nobody"),
            ("account with no password", "sample-unset"),
        ]:
            with self.subTest(case=description):
                response = self.log_in(username=username, password="wrong-Password-1")

                self.assertFalse(response.context["user"].is_authenticated)
                self.assertEqual(list(response.context["form"].non_field_errors()), [expected])


class AccessTests(TemporaryPasswordFixture):
    def refused_users(self):
        """Everyone but this course's instructors, as (description, user)."""
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)
        ta_and_instructor = make_user("sample-ta-both")
        enroll(ta_and_instructor, self.course, Role.TA)
        enroll(ta_and_instructor, self.other_course, Role.INSTRUCTOR)
        other_instructor = make_user("sample-other")
        enroll(other_instructor, self.other_course, Role.INSTRUCTOR)
        return [
            ("TA", ta),
            ("TA here, instructor elsewhere", ta_and_instructor),
            ("student", self.grace),
            ("another course's instructor", other_instructor),
            ("not enrolled", make_user("sample-unenrolled")),
            (
                "superuser who isn't enrolled",
                User.objects.create_superuser(
                    username="sample-admin",
                    password=CURRENT_PASSWORD,
                    github_login="sample-admin",
                ),
            ),
        ]

    def post_edit(self, client):
        return client.post(
            membership_edit_url(self.membership.id),
            {"full_name": "Grace Example", "temporary_password": TEMPORARY_PASSWORD},
        )

    def post_add(self, client):
        return client.post(
            student_add_url(self.team.id),
            {
                "full_name": "Grace Example",
                "github_login": "sample-grace",
                "email": "",
                "temporary_password": TEMPORARY_PASSWORD,
            },
        )

    def test_everyone_but_the_courses_instructors_gets_a_403(self):
        for description, user in self.refused_users():
            for form, post in [("edit", self.post_edit), ("add", self.post_add)]:
                with self.subTest(user=description, form=form):
                    client = Client()
                    client.force_login(user)

                    self.assertEqual(post(client).status_code, 403)
                    self.assert_password_untouched()

    def test_anonymous_users_are_sent_to_log_in(self):
        for form, post in [("edit", self.post_edit), ("add", self.post_add)]:
            with self.subTest(form=form):
                response = post(Client())

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url.split("?")[0], reverse("login"))
                self.assert_password_untouched()

    def test_a_get_cannot_set_a_password(self):
        url = membership_edit_url(self.membership.id)

        response = self.client.get(
            url, {"full_name": "Grace Example", "temporary_password": TEMPORARY_PASSWORD}
        )

        self.assertEqual(response.status_code, 405)
        self.assert_password_untouched()

    def strict_client(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.instructor)
        return client

    def test_a_post_without_a_csrf_token_is_refused(self):
        for form, post in [("edit", self.post_edit), ("add", self.post_add)]:
            with self.subTest(form=form):
                self.assertEqual(post(self.strict_client()).status_code, 403)
                self.assert_password_untouched()

    def test_both_forms_carry_a_csrf_token_that_lets_the_password_save(self):
        client = self.strict_client()
        query = f"?edit_student={self.membership.id}"
        content = client.get(team_url(self.team.id) + query).content.decode()
        action = membership_edit_url(self.membership.id)
        form = self.form_in(content, action)
        add_form = self.form_in(content, student_add_url(self.team.id))
        self.assertIn('name="csrfmiddlewaretoken"', add_form)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form).group(1)

        response = client.post(
            action,
            {
                "full_name": "Grace Example",
                "temporary_password": TEMPORARY_PASSWORD,
                "csrfmiddlewaretoken": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assert_has_temporary_password()
