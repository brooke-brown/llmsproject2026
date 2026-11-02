from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import Case, Exists, OuterRef, Prefetch, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import Course, Enrollment, Team, TeamMembership
from ingest import services as ingest_services
from ingest.models import Artifact
from summaries import services as summaries_services
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from . import flags
from .models import Flag

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]
MONDAY = 0  # date.weekday() counts from Monday.


def flags_in_display_order():
    # "flag" sorts before "warning", so flags come first.
    return Flag.objects.order_by("severity", "id")


def staff_courses(user):
    """Courses where the user is an instructor or TA."""
    return Course.objects.filter(
        enrollments__user=user, enrollments__role__in=STAFF_ROLES
    ).order_by("name")


def check_course_staff(user, course):
    """Raise PermissionDenied unless the user is an instructor or TA in this course."""
    is_staff = Enrollment.objects.filter(
        user=user, course=course, role__in=STAFF_ROLES
    ).exists()
    if not is_staff:
        raise PermissionDenied


def get_course_for_staff(user, course_id):
    """The course, or 404 if it doesn't exist and 403 if the user isn't its staff."""
    course = get_object_or_404(Course, pk=course_id)
    check_course_staff(user, course)
    return course


def get_team_for_staff(user, team_id):
    team = get_object_or_404(Team.objects.select_related("project__course"), pk=team_id)
    check_course_staff(user, team.project.course)
    return team


def get_summary_for_staff(user, summary_id):
    """The summary, or 404 if it doesn't exist and 403 if the user isn't its course's staff."""
    summary = get_object_or_404(
        Summary.objects.select_related("user", "team__project__course"), pk=summary_id
    )
    check_course_staff(user, summary.team.project.course)
    return summary


def week_end(week_start):
    """The Sunday that ends the Monday-Sunday reporting week."""
    return week_start + timedelta(days=6)


def verified_claims(summary):
    """The summary's verified claims, the only ones a user may see.

    Each claim has its artifacts prefetched for the GitHub links, and
    `tagged_requirements`: a (number, text) pair per requirement_id, with text None
    when the team's project has no requirement with that number.
    """
    requirements = dict(summary.team.project.requirements.values_list("number", "text"))
    claims = list(
        summary.claims.filter(status=Claim.Status.VERIFIED)
        .order_by("id")
        .prefetch_related(
            Prefetch("artifacts", queryset=Artifact.objects.order_by("artifact_id"))
        )
    )
    for claim in claims:
        claim.tagged_requirements = [
            (number, requirements.get(number)) for number in claim.requirement_ids
        ]
    return claims


def team_repos(team):
    return team.repos.order_by("full_name")


def team_wide_flags(team):
    """The team's flags that aren't about one student."""
    return flags_in_display_order().filter(team=team, user=None)


def team_memberships_with_summaries(team):
    """The team's memberships, each user with this team's `team_summaries`, newest first,
    and `team_flags` about them."""
    summaries = Summary.objects.filter(team=team).order_by("-week_start")
    return (
        team.memberships.select_related("user")
        .order_by("user__last_name", "user__first_name", "user__github_login")
        .prefetch_related(
            Prefetch("user__summaries", queryset=summaries, to_attr="team_summaries"),
            Prefetch(
                "user__flags",
                queryset=flags_in_display_order().filter(team=team),
                to_attr="team_flags",
            ),
        )
    )


def _has_flag(severity):
    """True for a team with at least one flag of this severity, inside a Team query."""
    return Exists(Flag.objects.filter(team=OuterRef("pk"), severity=severity))


def course_projects_with_teams(course):
    """The course's projects, each with its teams, their students and their flags.

    Within a project, flagged teams come first, then warned teams, then the rest, each
    group sorted by name.
    """
    memberships = TeamMembership.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__github_login"
    )
    teams = (
        Team.objects.annotate(
            flag_rank=Case(
                When(_has_flag(Flag.Severity.FLAG), then=Value(0)),
                When(_has_flag(Flag.Severity.WARNING), then=Value(1)),
                default=Value(2),
            )
        )
        .order_by("flag_rank", "name")
        .prefetch_related(
            Prefetch("memberships", queryset=memberships),
            Prefetch("flags", queryset=flags_in_display_order()),
        )
    )
    return course.projects.order_by("id").prefetch_related(Prefetch("teams", queryset=teams))


class GenerationError(Exception):
    """A summary couldn't be generated. The message is shown to the user as is."""


def get_team_student_for_staff(user, team_id, student_id):
    """The team and one of its students, or 404 if either is missing and 403 if the
    user isn't the course's staff. Staff is checked first, so membership isn't leaked."""
    team = get_team_for_staff(user, team_id)
    membership = get_object_or_404(
        TeamMembership.objects.select_related("user"), team=team, user_id=student_id
    )
    return team, membership.user


def reporting_today(now=None):
    """Today's date in the reporting time zone."""
    return (now or timezone.now()).astimezone(ingest_services.REPORTING_TIME_ZONE).date()


def last_completed_week_start(now=None):
    """The Monday of the most recent Monday-Sunday week that has fully ended."""
    today = reporting_today(now)
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def parse_week_start(value, now=None):
    """The Monday that starts a completed reporting week, from a YYYY-MM-DD string.

    Raises GenerationError, with a message for the user, for anything else.
    """
    try:
        week_start = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise GenerationError("Pick a week to generate.") from None
    if week_start.weekday() != MONDAY:
        raise GenerationError("The week must start on a Monday.")
    if week_start > last_completed_week_start(now):
        raise GenerationError("That week hasn't ended yet. Pick a week that's already over.")
    return week_start


def generate_student_summary(student, team, week_start):
    """Ingest each of the team's repos for the week, write the student's summary, then
    recompute the team's flags.

    Raises GenerationError naming the step that failed. Flags are only recomputed after
    a summary is saved, so a failure leaves them as they were.
    """
    end = week_end(week_start)
    for repo in team_repos(team):
        try:
            ingest_services.ingest(repo, week_start, end)
        except Exception:
            # ingest talks to GitHub, which can fail in many ways. Its error isn't shown,
            # so nothing from the repo reaches the page.
            raise GenerationError(
                f"Couldn't pull GitHub activity for {repo.full_name}. No summary was generated."
            ) from None
    try:
        summary = summaries_services.generate_summary(student, team, week_start)
    except LLMError as error:
        # LLMError messages never hold student work, so they are safe to show.
        raise GenerationError(
            f"Couldn't write the summary: {error} No summary was generated."
        ) from None
    flags.compute_flags(team)
    return summary
