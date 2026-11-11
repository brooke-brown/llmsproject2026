from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404

from core.models import Project, Team
from dashboard.services import is_course_student

from .models import Contribution, Corroboration

LOCKED_MESSAGE = (
    "You can't edit or delete this because a teammate has confirmed it. This keeps a "
    "confirmation tied to what they actually read."
)


class ContributionLocked(Exception):
    """A teammate has confirmed this contribution, so it can't change."""


def get_team_for_project_student(user, project_id) -> Team:
    """The user's team in the project. 404 if the project doesn't exist, 403 unless they're
    a student in its course and on one of its teams."""
    project = get_object_or_404(Project.objects.select_related("course"), pk=project_id)
    team = (
        Team.objects.select_related("project")
        .filter(project=project, memberships__user=user)
        .first()
    )
    if team is None or not is_course_student(user, project.course):
        raise PermissionDenied
    return team


def get_own_contribution(user, contribution_id) -> Contribution:
    """The user's contribution. 404 if it doesn't exist, 403 if it isn't theirs."""
    contribution = get_object_or_404(
        Contribution.objects.select_related("team__project__course"), pk=contribution_id
    )
    is_theirs = contribution.student_id == user.id
    if not is_theirs or not is_course_student(user, contribution.team.project.course):
        raise PermissionDenied
    return contribution


def contributions_for_list(team: Team, student):
    """What `student` recorded on `team`, newest work first, each with `confirmation_count`."""
    return (
        Contribution.objects.filter(team=team, student=student)
        .select_related("team")
        .annotate(confirmation_count=Count("corroborations"))
        .order_by("-date", "-created_at", "-pk")
    )


def is_locked(contribution: Contribution) -> bool:
    """A confirmed contribution has to stay what the teammate read."""
    return contribution.corroborations.exists()


def _refuse_if_locked(contribution_id) -> None:
    """Call inside a transaction, so a confirmation can't land before the change commits.

    Takes the row lock, which SQLite ignores because its transactions take the write lock
    up front. Confirming a contribution (US-34) has to run in a transaction that locks the
    same row.
    """
    get_object_or_404(Contribution.objects.select_for_update(), pk=contribution_id)
    if Corroboration.objects.filter(contribution_id=contribution_id).exists():
        raise ContributionLocked


def save_contribution(form) -> Contribution:
    """Store a validated form: a new contribution, or an edit unless it has been confirmed."""
    with transaction.atomic():
        if form.instance.pk is not None:
            _refuse_if_locked(form.instance.pk)
        return form.save()


def delete_contribution(contribution: Contribution) -> None:
    """Delete a contribution unless it has been confirmed."""
    with transaction.atomic():
        _refuse_if_locked(contribution.pk)
        contribution.delete()
