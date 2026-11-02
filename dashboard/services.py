from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import Case, Exists, OuterRef, Prefetch, Value, When
from django.shortcuts import get_object_or_404

from core.models import Course, Enrollment, Team, TeamMembership
from ingest.models import Artifact
from summaries.models import Claim, Summary

from .models import Flag

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]


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
