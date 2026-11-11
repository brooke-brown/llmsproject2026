"""Opening, changing and deleting peer evaluations, and students rating their teammates.
Views call these and show the errors."""

from datetime import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from contributions.services import get_team_for_project_student
from core.models import Project, Team, User
from dashboard.services import check_course_instructor

from .models import DUPLICATE_NAME_ERROR, PeerEvaluation, PeerRating


class EvaluationError(Exception):
    """A change that was refused, with a message to show the instructor."""


def is_open(evaluation: PeerEvaluation) -> bool:
    return timezone.now() < evaluation.closes_at


def open_evaluation(project: Project) -> PeerEvaluation | None:
    """The project's open evaluation, if it has one. Only one can be open at a time."""
    return project.peer_evaluations.filter(closes_at__gt=timezone.now()).first()


def project_evaluations(project: Project) -> list[PeerEvaluation]:
    """The project's evaluations, newest first, each with `is_open` and `rating_count`."""
    evaluations = list(
        project.peer_evaluations.annotate(rating_count=Count("ratings")).order_by(
            "-created_at", "-pk"
        )
    )
    for evaluation in evaluations:
        evaluation.is_open = is_open(evaluation)
    return evaluations


def get_evaluation_for_instructor(user: User, evaluation_id: int) -> PeerEvaluation:
    """The evaluation, or 404 if it doesn't exist and 403 if the user isn't its instructor."""
    evaluation = get_object_or_404(
        PeerEvaluation.objects.select_related("project__course"), pk=evaluation_id
    )
    check_course_instructor(user, evaluation.project.course)
    return evaluation


def _check_future(closes_at: datetime) -> None:
    if closes_at <= timezone.now():
        raise EvaluationError("The closing time must be in the future.")


def _check_still_open(evaluation: PeerEvaluation) -> None:
    if not is_open(evaluation):
        raise EvaluationError(f"{evaluation.name} has closed, so it can't be changed.")


def _locked(evaluation: PeerEvaluation) -> PeerEvaluation:
    """The evaluation re-read under a row lock, so two changes at once can't both pass a check."""
    return PeerEvaluation.objects.select_for_update().get(pk=evaluation.pk)


def create_evaluation(
    project: Project, created_by: User, name: str, closes_at: datetime
) -> PeerEvaluation:
    name = name.strip()
    if not name:
        raise EvaluationError("Enter a name.")
    _check_future(closes_at)
    with transaction.atomic():
        # Locking the project stops two instructors opening an evaluation at once.
        Project.objects.select_for_update().get(pk=project.pk)
        current = open_evaluation(project)
        if current is not None:
            raise EvaluationError(
                f"{current.name} is still open. Close it before opening another."
            )
        if project.peer_evaluations.filter(name__iexact=name).exists():
            raise EvaluationError(DUPLICATE_NAME_ERROR)
        return PeerEvaluation.objects.create(
            project=project, name=name, closes_at=closes_at, created_by=created_by
        )


def change_closing_time(evaluation: PeerEvaluation, closes_at: datetime) -> PeerEvaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation)
        _check_still_open(evaluation)
        _check_future(closes_at)
        evaluation.closes_at = closes_at
        evaluation.save(update_fields=["closes_at"])
    return evaluation


def close_now(evaluation: PeerEvaluation) -> PeerEvaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation)
        _check_still_open(evaluation)
        evaluation.closes_at = timezone.now()
        evaluation.save(update_fields=["closes_at"])
    return evaluation


def get_evaluation_for_student(user: User, evaluation_id: int) -> tuple[PeerEvaluation, Team]:
    """The evaluation and the user's team in its project. 404 if the evaluation doesn't exist,
    403 unless they're a student in its course and on one of its project's teams."""
    evaluation = get_object_or_404(
        PeerEvaluation.objects.select_related("project__course"), pk=evaluation_id
    )
    return evaluation, get_team_for_project_student(user, evaluation.project_id)


def teammates(team: Team, user: User) -> list[User]:
    """Everyone on the team but the user, by name, as the student home lists them."""
    return list(
        User.objects.filter(team_memberships__team=team)
        .exclude(pk=user.pk)
        .order_by("first_name", "last_name", "github_login")
    )


def get_teammate(team: Team, user: User, ratee_id: int) -> User:
    """A teammate the user can rate. 404 if no such user, 403 if it's them or not a teammate."""
    ratee = get_object_or_404(User, pk=ratee_id)
    if ratee == user or not team.memberships.filter(user=ratee).exists():
        raise PermissionDenied
    return ratee


def teammates_with_status(evaluation: PeerEvaluation, team: Team, user: User) -> list[User]:
    """The user's teammates, each with `is_rated`: whether the user has rated them. Only the
    user's own ratings are read, and never their scores or comments."""
    rated_ids = set(
        evaluation.ratings.filter(rater=user).values_list("ratee_id", flat=True)
    )
    people = teammates(team, user)
    for person in people:
        person.is_rated = person.pk in rated_ids
    return people


def own_rating(evaluation: PeerEvaluation, rater: User, ratee: User) -> PeerRating | None:
    return evaluation.ratings.filter(rater=rater, ratee=ratee).first()


def closed_message(evaluation: PeerEvaluation) -> str:
    return f"{evaluation.name} has closed, so ratings can't be submitted or changed."


def save_rating(
    evaluation: PeerEvaluation, rater: User, ratee: User, scores: dict[str, int], comment: str
) -> PeerRating:
    """Save the rater's rating of a teammate, replacing an earlier one. The closing time is
    checked here, under the evaluation's lock, so a page left open past it can't save and a
    rating can't land while the evaluation is being closed or deleted."""
    with transaction.atomic():
        try:
            evaluation = _locked(evaluation)
        except PeerEvaluation.DoesNotExist:
            raise Http404
        if not is_open(evaluation):
            raise EvaluationError(closed_message(evaluation))
        rating = own_rating(evaluation, rater, ratee) or PeerRating(
            evaluation=evaluation, rater=rater, ratee=ratee
        )
        rating.scores = scores
        rating.comment = comment
        rating.full_clean()
        rating.save()
    return rating


def delete_evaluation(evaluation: PeerEvaluation) -> None:
    """Delete an evaluation nobody has rated in, so students' answers are never lost."""
    with transaction.atomic():
        evaluation = _locked(evaluation)
        if evaluation.ratings.exists():
            raise EvaluationError(
                f"{evaluation.name} has ratings, so it can't be deleted: "
                "students' answers would be lost."
            )
        evaluation.delete()
