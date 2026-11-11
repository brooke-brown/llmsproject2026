from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime

from core.models import Project, Sprint, Team
from dashboard.services import is_course_student

from .models import Contribution, Corroboration

LOCKED_MESSAGE = (
    "You can't edit or delete this because a teammate has confirmed it. This keeps a "
    "confirmation tied to what they actually read."
)

CHANGED_MESSAGE = (
    "This contribution was edited after you opened the page. Read it again before confirming."
)


class ContributionChanged(Exception):
    """The contribution isn't what the teammate read when they chose to confirm it."""


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


def get_team_contribution(user, contribution_id) -> Contribution:
    """A contribution on the user's team. 404 if it doesn't exist, 403 unless they're a
    student in the course and on its team.

    Only who may act on it at all: whether they may confirm it is `Corroboration.clean`'s
    rule, which `confirm_contribution` applies.
    """
    contribution = get_object_or_404(
        Contribution.objects.select_related("team__project__course"), pk=contribution_id
    )
    on_team = contribution.team.memberships.filter(user=user).exists()
    if not on_team or not is_course_student(user, contribution.team.project.course):
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


def _for_staff(contributions, project_sprints):
    """Evaluate `contributions` for the team and summary pages, newest work first. Each gets
    `confirmation_count`, `confirmed_by` (the confirming teammates' names, alphabetically) and
    `sprint_names` (the project's sprints that include its date, earliest first). The sprints
    come from `project_sprints`, so the number of queries doesn't grow with the rows."""
    contributions = list(
        contributions.annotate(confirmation_count=Count("corroborations"))
        .prefetch_related(
            Prefetch(
                "corroborations", queryset=Corroboration.objects.select_related("teammate")
            )
        )
        .order_by("-date", "-created_at", "-pk")
    )
    for contribution in contributions:
        contribution.confirmed_by = sorted(
            corroboration.teammate.display_name
            for corroboration in contribution.corroborations.all()
        )
        contribution.sprint_names = [
            sprint.name
            for sprint in project_sprints
            if sprint.start_date <= contribution.date <= sprint.end_date
        ]
    return contributions


def _project_sprints(team: Team):
    return list(Sprint.objects.filter(project_id=team.project_id).order_by("start_date", "pk"))


def contributions_for(team: Team, student, sprint=None) -> list[Contribution]:
    """What `student` recorded on `team`, for instructors and TAs: newest work first, each
    with `confirmation_count`, `confirmed_by` and `sprint_names` (see `_for_staff`). With a
    sprint, only work dated from its first through its last day.

    Self-reported and never checked: not evidence for a claim, and never counted toward
    shares or flags."""
    contributions = Contribution.objects.filter(team=team, student=student)
    if sprint is not None:
        contributions = contributions.filter(
            date__gte=sprint.start_date, date__lte=sprint.end_date
        )
    return _for_staff(contributions, _project_sprints(team))


def contributions_by_student(team: Team) -> dict[int, list[Contribution]]:
    """Every student's work on `team`, as `contributions_for` returns it, keyed by user ID, in
    a fixed number of queries. A student with none has no key."""
    by_student = {}
    for contribution in _for_staff(
        Contribution.objects.filter(team=team), _project_sprints(team)
    ):
        by_student.setdefault(contribution.student_id, []).append(contribution)
    return by_student


def teammates_contributions_for_list(team: Team, student):
    """What `student`'s teammates recorded on `team`, newest work first, each with
    `confirmation_count` and `confirmed_by_student`. Never who else confirmed it."""
    return (
        Contribution.objects.filter(team=team)
        .exclude(student=student)
        .select_related("student")
        .annotate(
            confirmation_count=Count("corroborations"),
            confirmed_by_student=Exists(
                Corroboration.objects.filter(contribution=OuterRef("pk"), teammate=student)
            ),
        )
        .order_by("-date", "-created_at", "-pk")
    )


def is_locked(contribution: Contribution) -> bool:
    """A confirmed contribution has to stay what the teammate read."""
    return contribution.corroborations.exists()


def _refuse_if_locked(contribution_id) -> None:
    """Call inside a transaction, so a confirmation can't land before the change commits.

    Takes the row lock, which SQLite ignores because its transactions take the write lock
    up front. `confirm_contribution` locks the same row.
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


def _same_version(seen_updated_at: str, updated_at) -> bool:
    try:
        seen = parse_datetime(seen_updated_at or "")
    except ValueError:
        return False
    return seen is not None and seen == updated_at


def confirm_contribution(contribution: Contribution, teammate, seen_updated_at: str) -> None:
    """Record a teammate's confirmation of the version they read, once however many times
    they submit it.

    `seen_updated_at` is the contribution's `updated_at` when their page loaded. Raises
    `PermissionDenied` if `Corroboration`'s rules refuse it, and `ContributionChanged` if the
    contribution has been edited since. Both checks run under the row lock, which makes this
    wait for an edit or delete already under way (see `_refuse_if_locked`). 404 if that delete
    has removed the contribution.
    """
    with transaction.atomic():
        current = get_object_or_404(
            Contribution.objects.select_for_update(), pk=contribution.pk
        )
        if Corroboration.objects.filter(contribution=current, teammate=teammate).exists():
            return
        corroboration = Corroboration(contribution=current, teammate=teammate)
        try:
            corroboration.full_clean()
        except ValidationError as error:
            raise PermissionDenied from error
        if not _same_version(seen_updated_at, current.updated_at):
            raise ContributionChanged
        corroboration.save()


def undo_confirmation(contribution: Contribution, teammate) -> None:
    """Remove this teammate's confirmation, if they made one, leaving anyone else's."""
    Corroboration.objects.filter(contribution=contribution, teammate=teammate).delete()
