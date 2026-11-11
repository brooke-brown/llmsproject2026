from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Lower
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core.models import Project, TeamMembership

from .questions import MAX_SCORE, MIN_SCORE, QUESTION_KEYS

# Shared by the model's constraint and the service's check, so the two never word it differently.
DUPLICATE_NAME_ERROR = "This project already has a peer evaluation with this name."


class PeerEvaluationQuerySet(models.QuerySet):
    def open(self):
        """Evaluations still open: now is before `closes_at`, as `PeerEvaluation.is_open_now`."""
        return self.filter(closes_at__gt=timezone.now())


class PeerEvaluation(models.Model):
    """A round of peer ratings an instructor opens for a project, such as "Midpoint"."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="peer_evaluations"
    )
    name = models.CharField(max_length=100)
    closes_at = models.DateTimeField()
    # PROTECT so removing an instructor's account can't silently delete students' ratings.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="peer_evaluations_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PeerEvaluationQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "project",
                Lower("name"),
                name="peereval_evaluation_project_name_unique_ci",
                violation_error_message=DUPLICATE_NAME_ERROR,
            ),
        ]

    def __str__(self):
        return self.name

    def is_open_now(self) -> bool:
        """Now is before `closes_at`, as `PeerEvaluation.objects.open()`."""
        return timezone.now() < self.closes_at


class PeerRating(models.Model):
    """One student's ratings of one teammate in an evaluation.

    Confidential: students never see ratings, including ones about themselves, and ratings are
    never passed to the LLM or counted toward flags. `__str__` leaves out scores and the
    comment so they can't end up in logs.
    """

    evaluation = models.ForeignKey(
        PeerEvaluation, on_delete=models.CASCADE, related_name="ratings"
    )
    rater = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="peer_ratings_given"
    )
    ratee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="peer_ratings_received"
    )
    # Question key -> integer score from MIN_SCORE to MAX_SCORE, for every question asked
    # when the rating was first saved. A question added later doesn't make it invalid.
    scores = models.JSONField()
    comment = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation", "rater", "ratee"], name="peereval_rating_unique"
            ),
        ]

    def __str__(self):
        return f"Peer rating {self.pk}"

    def clean(self):
        """Forms run this even after a required field failed, so any value can be missing."""
        super().clean()
        errors = {}
        if self.rater_id is not None and self.rater_id == self.ratee_id:
            errors["ratee"] = "A student can't rate themselves."
        elif (
            self.evaluation_id is not None
            and self.rater_id is not None
            and self.ratee_id is not None
            and not self._share_a_team()
        ):
            errors[NON_FIELD_ERRORS] = "The rater and ratee aren't on the same team in this project."
        score_error = self._scores_error()
        if score_error:
            errors["scores"] = score_error
        if errors:
            raise ValidationError(errors)

    def _share_a_team(self):
        rater_teams = TeamMembership.objects.filter(
            user_id=self.rater_id, team__project_id=self.evaluation.project_id
        ).values("team_id")
        return TeamMembership.objects.filter(
            user_id=self.ratee_id, team_id__in=rater_teams
        ).exists()

    def _scores_error(self):
        if not isinstance(self.scores, dict):
            return "Scores must map each question to a score."
        answered = self._saved_keys()
        # A new rating answers every question; a saved one keeps the questions it answered,
        # so adding a question later doesn't make earlier ratings fail.
        required = QUESTION_KEYS if answered is None else answered
        missing = required - self.scores.keys()
        if missing:
            return f"Missing a score for: {', '.join(sorted(missing))}."
        unknown = self.scores.keys() - QUESTION_KEYS - (answered or set())
        if unknown:
            return f"Unknown question: {', '.join(sorted(unknown))}."
        for value in self.scores.values():
            # bool is a subclass of int, and True would otherwise pass as 1.
            if type(value) is not int or not MIN_SCORE <= value <= MAX_SCORE:
                return f"Each score must be a whole number from {MIN_SCORE} to {MAX_SCORE}."
        return None

    def _saved_keys(self):
        """The question keys this rating had when it was last saved, or None if it's new."""
        if self.pk is None:
            return None
        saved = PeerRating.objects.filter(pk=self.pk).values_list("scores", flat=True).first()
        return set(saved) if isinstance(saved, dict) else None


def ratings_without_a_shared_team():
    """Ratings whose rater and ratee are no longer on a team together in the project."""
    shared_team = TeamMembership.objects.filter(
        user=OuterRef("rater"),
        team__project=OuterRef("evaluation__project"),
        team__memberships__user=OuterRef("ratee"),
    )
    return PeerRating.objects.exclude(Exists(shared_team))


@receiver(post_delete, sender=TeamMembership)
def delete_ratings_on_team(sender, instance, **kwargs):
    """Delete the ratings a departing student gave or got that needed them on this team.

    A signal rather than a service, because deletes in the admin call no service. Deleting a
    team removes its memberships before the team itself, so this runs for every student.
    """
    ratings_without_a_shared_team().filter(
        Q(rater=instance.user_id) | Q(ratee=instance.user_id),
        evaluation__project__teams=instance.team_id,
    ).delete()
