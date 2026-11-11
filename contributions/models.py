from decimal import Decimal

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core.models import Sprint, Team, TeamMembership
from ingest.services import REPORTING_TIME_ZONE

MAX_DESCRIPTION_LENGTH = 1000
MIN_HOURS = Decimal("0.25")
MAX_HOURS = Decimal("24")


class Contribution(models.Model):
    """Work a student reports doing outside the code, shown to instructors as self-reported.

    Deliberately not an Artifact: a claim can't cite one, the validator can't accept one as
    evidence, and it's never passed to the LLM or counted toward shares or flags.
    """

    class Category(models.TextChoices):
        MEETING = "meeting", "Meeting"
        DESIGN = "design", "Design"
        USER_RESEARCH = "user research", "User research"
        DOCUMENTATION = "documentation", "Documentation"
        PLANNING = "planning", "Planning"
        OTHER = "other", "Other"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="contributions")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contributions"
    )
    # When the work happened, which is what places it in a sprint.
    date = models.DateField()
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField(validators=[MaxLengthValidator(MAX_DESCRIPTION_LENGTH)])
    hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_HOURS), MaxValueValidator(MAX_HOURS)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_category_display()} on {self.date}"

    @property
    def sprints(self):
        """The project's sprints whose dates include this contribution's date, earliest first.

        Worked out from the date rather than stored, so a day shared by two sprints is in
        both and a sprint set up later picks up what was already logged. Empty until the team
        and date are set, since a form can re-render a contribution that failed validation.
        """
        if self.team_id is None or self.date is None:
            return Sprint.objects.none()
        return Sprint.objects.filter(
            project_id=self.team.project_id, start_date__lte=self.date, end_date__gte=self.date
        ).order_by("start_date")

    def clean(self):
        """Forms run this even after a required field failed, so any value can be missing.

        The team error isn't tied to `student`, since a form that fills in the student
        itself has no such field and Django would refuse the error.
        """
        super().clean()
        errors = {}
        if (
            self.team_id is not None
            and self.student_id is not None
            and not TeamMembership.objects.filter(
                team_id=self.team_id, user_id=self.student_id
            ).exists()
        ):
            errors[NON_FIELD_ERRORS] = "This student isn't on this team."
        if self.date is not None and self.date > timezone.localdate(timezone=REPORTING_TIME_ZONE):
            errors["date"] = "The date can't be in the future."
        if errors:
            raise ValidationError(errors)


class Corroboration(models.Model):
    """A teammate's confirmation that a contribution happened."""

    contribution = models.ForeignKey(
        Contribution, on_delete=models.CASCADE, related_name="corroborations"
    )
    teammate = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="corroborations"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["contribution", "teammate"], name="contributions_corroboration_unique"
            ),
        ]

    def __str__(self):
        return f"Corroboration of contribution {self.contribution_id}"

    def clean(self):
        """Forms run this even after a required field failed, so either value can be missing."""
        super().clean()
        if self.contribution_id is None or self.teammate_id is None:
            return
        if self.teammate_id == self.contribution.student_id:
            raise ValidationError("A student can't corroborate their own contribution.")
        on_team = TeamMembership.objects.filter(
            team_id=self.contribution.team_id, user_id=self.teammate_id
        ).exists()
        if not on_team:
            raise ValidationError("Only someone on the contribution's team can corroborate it.")


@receiver(post_delete, sender=TeamMembership)
def delete_work_on_team(sender, instance, **kwargs):
    """Delete what a departing student logged and confirmed on the team, which needs them to
    be on it. A signal rather than a service, because deletes in the admin call no service."""
    Contribution.objects.filter(team=instance.team_id, student=instance.user_id).delete()
    Corroboration.objects.filter(
        contribution__team=instance.team_id, teammate=instance.user_id
    ).delete()
