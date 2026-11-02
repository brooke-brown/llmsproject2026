"""Tests for US-17 (#54): read a summary with every claim linked to GitHub.

URLs are reversed per test rather than at module level, so each criterion passes
or fails on its own instead of the whole module failing on a single
NoReverseMatch.
"""

from datetime import date, datetime, timezone

from django.test import TestCase
from django.urls import reverse

from core.models import Course, Enrollment, Project, Repo, Requirement, Team, TeamMembership
from ingest.models import Artifact
from summaries.models import Claim, Summary

from .test_course_overview import enroll, make_user, team_url

Role = Enrollment.Role
Status = Claim.Status


def summary_url(summary_id):
    return reverse("dashboard:summary", args=[summary_id])


def make_artifact(repo, type, id, url):
    return Artifact.objects.create(
        repo=repo,
        artifact_id=f"{repo.full_name}:{type}:{id}",
        type=type,
        url=url,
        timestamp=datetime(2026, 10, 28, 15, tzinfo=timezone.utc),
    )


def make_claim(summary, text, artifacts=(), status=Status.VERIFIED, **fields):
    fields = {"category": "feature", "significance": "moderate", "requirement_ids": [], **fields}
    claim = Claim.objects.create(
        summary=summary,
        text=text,
        artifact_ids=[artifact.artifact_id for artifact in artifacts],
        status=status,
        **fields,
    )
    claim.artifacts.set(artifacts)
    return claim


class SummaryPageFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(name="CS 4501, Fall 2026")
        cls.instructor = make_user("sample-instructor")
        enroll(cls.instructor, cls.course, Role.INSTRUCTOR)

        cls.project = Project.objects.create(course=cls.course, name="Project One")
        Requirement.objects.create(project=cls.project, number=1, text="Users can log in")
        Requirement.objects.create(project=cls.project, number=2, text="Users can reset a password")
        cls.team = Team.objects.create(project=cls.project, name="Team Alpha")
        cls.repo = Repo.objects.create(
            team=cls.team, url="https://github.com/sample-org/app", full_name="sample-org/app"
        )

        cls.ada = make_user("sample-ada", "Ada", "Sample")
        enroll(cls.ada, cls.course, Role.STUDENT)
        TeamMembership.objects.create(user=cls.ada, team=cls.team)

        cls.pr = make_artifact(cls.repo, "pr", "12", "https://github.com/sample-org/app/pull/12")
        cls.commit = make_artifact(
            cls.repo, "commit", "abc123", "https://github.com/sample-org/app/commit/abc123"
        )
        cls.fix = make_artifact(
            cls.repo, "commit", "def456", "https://github.com/sample-org/app/commit/def456"
        )

        # The week runs Monday Oct 26 to Sunday Nov 1, so it crosses a month.
        cls.summary = Summary.objects.create(
            team=cls.team, user=cls.ada, week_start=date(2026, 10, 26), artifact_count=3
        )
        cls.login_claim = make_claim(
            cls.summary,
            "Added the login form",
            [cls.pr, cls.commit],
            category="feature",
            requirement_ids=[1],
            significance="major",
        )
        cls.fix_claim = make_claim(
            cls.summary,
            "Fixed the logout redirect",
            [cls.fix],
            category="bug fix",
            significance="minor",
        )
        cls.dropped_claim = make_claim(
            cls.summary, "Rewrote the whole backend", status=Status.COULDNT_VERIFY
        )

    def get_summary_page(self, user=None, summary=None):
        self.client.force_login(user or self.instructor)
        return self.client.get(summary_url((summary or self.summary).id))

    def make_summary(self, artifact_count=0):
        """Another summary for Ada, in the week before the fixture's."""
        return Summary.objects.create(
            team=self.team,
            user=self.ada,
            week_start=date(2026, 10, 19),
            artifact_count=artifact_count,
        )


class SummaryPageHeaderTests(SummaryPageFixture):
    def test_shows_the_student_and_team(self):
        response = self.get_summary_page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Sample")
        self.assertContains(response, "sample-ada")
        self.assertContains(response, "Team Alpha")

    def test_shows_the_reporting_week_as_monday_to_sunday_dates(self):
        response = self.get_summary_page()

        self.assertContains(response, "Oct 26, 2026")
        self.assertContains(response, "Nov 1, 2026")

    def test_links_back_to_the_team_page(self):
        response = self.get_summary_page()

        self.assertContains(response, f'href="{team_url(self.team.id)}"')


class SummaryPageClaimTests(SummaryPageFixture):
    def test_shows_each_verified_claims_text_and_category(self):
        response = self.get_summary_page()

        self.assertContains(response, "Added the login form")
        self.assertContains(response, "Feature")
        self.assertContains(response, "Fixed the logout redirect")
        self.assertContains(response, "Bug fix")

    def test_never_shows_claims_that_couldnt_be_verified(self):
        response = self.get_summary_page()

        self.assertNotContains(response, "Rewrote the whole backend")

    def test_shows_only_this_summarys_claims(self):
        make_claim(self.make_summary(artifact_count=1), "Wrote the README")

        response = self.get_summary_page()

        self.assertContains(response, "Added the login form")
        self.assertNotContains(response, "Wrote the README")

    def test_links_every_cited_artifact_with_its_id_as_the_link_text(self):
        response = self.get_summary_page()

        self.assertContains(
            response,
            '<a href="https://github.com/sample-org/app/pull/12">sample-org/app:pr:12</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="https://github.com/sample-org/app/commit/abc123">'
            "sample-org/app:commit:abc123</a>",
            html=True,
        )
        self.assertContains(
            response,
            '<a href="https://github.com/sample-org/app/commit/def456">'
            "sample-org/app:commit:def456</a>",
            html=True,
        )

    def test_links_come_from_the_artifacts_not_the_cited_id_strings(self):
        # A cited ID with no matching artifact can't be linked, so it isn't shown.
        self.fix_claim.artifact_ids = [self.fix.artifact_id, "sample-org/app:pr:999"]
        self.fix_claim.save()

        response = self.get_summary_page()

        self.assertContains(response, "sample-org/app:commit:def456")
        self.assertNotContains(response, "sample-org/app:pr:999")

    def test_shows_each_claims_significance(self):
        response = self.get_summary_page()

        self.assertContains(response, "Major")
        self.assertContains(response, "Minor")


class SummaryPageRequirementTests(SummaryPageFixture):
    def test_shows_the_requirement_number_and_text_for_each_tag(self):
        response = self.get_summary_page()

        self.assertContains(response, "Requirement 1")
        self.assertContains(response, "Users can log in")

    def test_shows_only_the_requirements_a_claim_is_tied_to(self):
        response = self.get_summary_page()

        self.assertNotContains(response, "Users can reset a password")

    def test_takes_requirement_text_from_the_teams_own_project(self):
        other_project = Project.objects.create(course=self.course, name="Project Two")
        Requirement.objects.create(project=other_project, number=1, text="Ships a mobile app")

        response = self.get_summary_page()

        self.assertContains(response, "Users can log in")
        self.assertNotContains(response, "Ships a mobile app")

    def test_requirement_missing_from_the_project_still_shows_its_number(self):
        self.fix_claim.requirement_ids = [7]
        self.fix_claim.save()

        response = self.get_summary_page()

        self.assertContains(response, "Requirement 7")

    def test_claim_with_no_requirements_says_it_isnt_tied_to_one(self):
        response = self.get_summary_page()

        self.assertContains(response, "Not tied to a requirement", count=1)


class SummaryPageLabelTests(SummaryPageFixture):
    def test_labels_requirements_and_significance_as_llm_judgments(self):
        response = self.get_summary_page()

        self.assertContains(response, "LLM judgment")

    def test_labels_artifact_links_as_verified_against_github(self):
        response = self.get_summary_page()

        self.assertContains(response, "Verified against GitHub")


class SummaryPageNoteTests(SummaryPageFixture):
    def test_says_how_many_claims_couldnt_be_verified(self):
        make_claim(self.summary, "Deployed to production", status=Status.COULDNT_VERIFY)

        response = self.get_summary_page()

        self.assertContains(response, "2 couldn't be verified.")

    def test_hides_the_note_when_every_claim_was_verified(self):
        self.dropped_claim.delete()

        response = self.get_summary_page()

        self.assertNotContains(response, "couldn't be verified")

    def test_summary_with_no_claims_says_there_was_no_activity(self):
        response = self.get_summary_page(summary=self.make_summary(artifact_count=0))

        self.assertContains(response, "No activity that week.")

    def test_summary_with_claims_doesnt_say_there_was_no_activity(self):
        response = self.get_summary_page()

        self.assertNotContains(response, "No activity that week.")

    def test_summary_whose_claims_were_all_dropped_doesnt_say_there_was_no_activity(self):
        summary = self.make_summary(artifact_count=2)
        make_claim(summary, "Rewrote the whole backend", status=Status.COULDNT_VERIFY)

        response = self.get_summary_page(summary=summary)

        self.assertContains(response, "1 couldn't be verified.")
        self.assertNotContains(response, "No activity that week.")


class SummaryPageAccessTests(SummaryPageFixture):
    def test_instructor_enrolled_in_course_sees_the_summary(self):
        response = self.get_summary_page(self.instructor)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Added the login form")

    def test_ta_enrolled_in_course_sees_the_summary(self):
        ta = make_user("sample-ta")
        enroll(ta, self.course, Role.TA)

        response = self.get_summary_page(ta)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Added the login form")

    def test_student_gets_403(self):
        response = self.get_summary_page(self.ada)

        self.assertEqual(response.status_code, 403)

    def test_user_not_enrolled_gets_403(self):
        response = self.get_summary_page(make_user("sample-outsider"))

        self.assertEqual(response.status_code, 403)

    def test_instructor_of_another_course_gets_403(self):
        other_instructor = make_user("sample-other-instructor")
        enroll(other_instructor, Course.objects.create(name="CS 2100"), Role.INSTRUCTOR)

        response = self.get_summary_page(other_instructor)

        self.assertEqual(response.status_code, 403)

    def test_missing_summary_returns_404(self):
        self.client.force_login(self.instructor)

        response = self.client.get(summary_url(self.summary.id + 1000))

        self.assertEqual(response.status_code, 404)

    def test_logged_out_visitor_is_redirected_to_login(self):
        url = summary_url(self.summary.id)

        response = self.client.get(url)

        self.assertRedirects(
            response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
        )
