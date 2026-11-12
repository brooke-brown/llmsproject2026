from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Sprint, Team
from ingest.models import Artifact
from ingest.services import REPORTING_TIME_ZONE


class Summary(models.Model):
    """One student's summary for one team and one sprint.

    Regenerating a sprint reuses this row: the old claims are deleted and new ones are
    written, so the sprint's ValidationRuns survive.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="summaries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="summaries"
    )
    sprint = models.ForeignKey(Sprint, on_delete=models.CASCADE, related_name="summaries")
    # How many artifacts the student had in the sprint, so a page can tell "no activity"
    # apart from "every claim was dropped".
    artifact_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user", "sprint"], name="summaries_summary_unique_per_sprint"
            ),
        ]

    @property
    def generated_at(self):
        """When this summary was last written. Regenerating reuses the row, so created_at can't
        say, but every generation logs a ValidationRun."""
        return max((run.created_at for run in self.validation_runs.all()), default=self.created_at)

    @property
    def sprint_ended_at(self):
        """The moment its sprint's last day ended."""
        day_after = self.sprint.end_date + timedelta(days=1)
        return datetime.combine(day_after, time.min, tzinfo=REPORTING_TIME_ZONE)

    @property
    def is_partial(self):
        """Whether it was written before its sprint ended, so it may be missing activity. This
        stays true after the sprint ends, until the sprint is generated again."""
        return self.generated_at < self.sprint_ended_at

    @property
    def sprint_is_running(self):
        return timezone.now() < self.sprint_ended_at

    @property
    def unverified_count(self):
        """How many claims were dropped, for the "N couldn't be verified" note."""
        return self.claims.filter(status=Claim.Status.COULDNT_VERIFY).count()

    def __str__(self):
        return f"{self.user} on {self.team}, {self.sprint.name}"


class Claim(models.Model):
    """One claim. The fields from `text` to `significance` match schemas.Claim."""

    class Category(models.TextChoices):
        FEATURE = "feature", "Feature"
        BUG_FIX = "bug fix", "Bug fix"
        TESTS = "tests", "Tests"
        REFACTOR = "refactor", "Refactor"
        REVIEW = "review", "Review"

    class Significance(models.TextChoices):
        MINOR = "minor", "Minor"
        MODERATE = "moderate", "Moderate"
        MAJOR = "major", "Major"

    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified"
        COULDNT_VERIFY = "couldnt_verify", "Couldn't verify"

    summary = models.ForeignKey(Summary, on_delete=models.CASCADE, related_name="claims")
    text = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices)
    # The IDs the LLM cited, such as "acme/app:pr:12". A made-up ID can't be linked to
    # a row, so this keeps exactly what was cited.
    artifact_ids = models.JSONField(default=list)
    # The cited artifacts that exist. Pages turn these into GitHub links.
    artifacts = models.ManyToManyField(Artifact, related_name="claims", blank=True)
    # Requirement.number, not the database ID. Empty means not tied to a requirement.
    requirement_ids = models.JSONField(default=list, blank=True)
    significance = models.CharField(max_length=20, choices=Significance.choices)
    # Nothing counts as verified until the validator says so.
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COULDNT_VERIFY
    )
    # The validator's last errors for this claim, for debugging in admin.
    validator_errors = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(category__in=["feature", "bug fix", "tests", "refactor", "review"]),
                name="summaries_claim_category_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(significance__in=["minor", "moderate", "major"]),
                name="summaries_claim_significance_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["verified", "couldnt_verify"]),
                name="summaries_claim_status_valid",
            ),
        ]

    def __str__(self):
        return self.text[:60]


class ValidationRun(models.Model):
    """The log of one generate_sprint_summary call, for measuring the validator and the LLM."""

    summary = models.ForeignKey(Summary, on_delete=models.CASCADE, related_name="validation_runs")
    claims_total = models.PositiveIntegerField()
    # Claims that passed the validator on the first attempt.
    claims_first_pass = models.PositiveIntegerField()
    # Claims still failing after the last attempt, so left out of the summary.
    claims_dropped = models.PositiveIntegerField()
    retries = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def first_pass_rate(self):
        return self.claims_first_pass / self.claims_total if self.claims_total else 0

    @property
    def drop_rate(self):
        return self.claims_dropped / self.claims_total if self.claims_total else 0

    def __str__(self):
        return f"Run for {self.summary} at {self.created_at}"
