"""Opening, changing and deleting peer evaluations, students rating their teammates, and
what instructors and TAs see of the ratings. Views call these and show the errors."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from contributions.services import get_student_team
from core.models import Project, Team, User
from dashboard.services import check_course_instructor, parse_id, teammates

from .models import DUPLICATE_NAME_ERROR, PeerEvaluation, PeerRating
from .questions import QUESTIONS, Question


class EvaluationError(Exception):
    """A change that was refused, with a message to show the instructor."""


def is_open(evaluation: PeerEvaluation) -> bool:
    return evaluation.is_open_now()


def open_evaluation(project: Project) -> PeerEvaluation | None:
    """The project's open evaluation, if it has one. Only one can be open at a time."""
    return project.peer_evaluations.open().first()


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
    return evaluation, get_student_team(user, evaluation.project)


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
        try:
            # Such as the ratee leaving the team after get_teammate checked them.
            rating.full_clean()
        except ValidationError as error:
            raise EvaluationError(" ".join(error.messages)) from error
        rating.save()
    return rating


def team_page_evaluation(
    evaluations: list[PeerEvaluation], value: str | None
) -> PeerEvaluation | None:
    """The evaluation the team page shows, from `evaluations` (project_evaluations, newest
    first): the one whose ID is `value`, or else the newest. None if there are none."""
    chosen = parse_id(value)
    for evaluation in evaluations:
        if evaluation.pk == chosen:
            return evaluation
    return evaluations[0] if evaluations else None


@dataclass
class QuestionAverage:
    question: Question
    average: Decimal | None  # None when no rating answers the question.


@dataclass
class ReceivedRating:
    rater: User
    scores: list[tuple[Question, int | None]]  # In QUESTIONS order; None if not answered.
    comment: str


@dataclass
class StudentRatings:
    """How a student's teammates rated them in one evaluation, and how far they've got
    rating their teammates. For instructors and TAs only: never render it for a student."""

    user: User
    teammate_count: int
    ratings: list[ReceivedRating]  # Teammates' ratings of this student, by rater's name.
    averages: list[QuestionAverage]
    rated_count: int  # How many teammates this student has rated.

    @property
    def rater_count(self) -> int:
        return len(self.ratings)

    @property
    def finished_rating(self) -> bool:
        return self.rated_count >= self.teammate_count


def _average(values: list[int]) -> Decimal | None:
    if not values:
        return None
    return (Decimal(sum(values)) / len(values)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def rating_summary(team: Team, evaluation: PeerEvaluation) -> list[StudentRatings]:
    """Each student on the team, by name, with their teammates' ratings of them in the
    evaluation. Only ratings between the team's current students count."""
    students = [
        membership.user
        for membership in team.memberships.select_related("user").order_by(
            "user__first_name", "user__last_name", "user__github_login"
        )
    ]
    by_id = {student.pk: student for student in students}
    name_order = {student.pk: place for place, student in enumerate(students)}
    received = defaultdict(list)
    rated_count = Counter()
    ratings = evaluation.ratings.filter(rater__in=by_id, ratee__in=by_id)
    for rating in ratings.only("rater_id", "ratee_id", "scores", "comment"):
        rated_count[rating.rater_id] += 1
        received[rating.ratee_id].append(rating)

    rows = []
    for student in students:
        theirs = sorted(received[student.pk], key=lambda r: name_order[r.rater_id])
        rows.append(
            StudentRatings(
                user=student,
                teammate_count=len(students) - 1,
                ratings=[
                    ReceivedRating(
                        rater=by_id[rating.rater_id],
                        scores=[(q, rating.scores.get(q.key)) for q in QUESTIONS],
                        comment=rating.comment,
                    )
                    for rating in theirs
                ],
                averages=[
                    QuestionAverage(
                        q,
                        _average([r.scores[q.key] for r in theirs if q.key in r.scores]),
                    )
                    for q in QUESTIONS
                ],
                rated_count=rated_count[student.pk],
            )
        )
    return rows


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
