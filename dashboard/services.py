from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404

from core.models import Course, Enrollment, Team, TeamMembership
from summaries.models import Summary

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]


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


def team_repos(team):
    return team.repos.order_by("full_name")


def team_memberships_with_summaries(team):
    """The team's memberships, each user with `team_summaries` for this team, newest first."""
    summaries = Summary.objects.filter(team=team).order_by("-week_start")
    return (
        team.memberships.select_related("user")
        .order_by("user__last_name", "user__first_name", "user__github_login")
        .prefetch_related(Prefetch("user__summaries", queryset=summaries, to_attr="team_summaries"))
    )


def course_projects_with_teams(course):
    """The course's projects, each with its teams and their students prefetched."""
    memberships = TeamMembership.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__github_login"
    )
    teams = Team.objects.order_by("name").prefetch_related(
        Prefetch("memberships", queryset=memberships)
    )
    return course.projects.order_by("id").prefetch_related(Prefetch("teams", queryset=teams))
