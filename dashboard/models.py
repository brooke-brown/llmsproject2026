from django.conf import settings
from django.db import models

from core.models import Team


class Flag(models.Model):
    """A notice that a team's work looks imbalanced, raised by a rule in flags.py.

    Flags are derived data: compute_flags replaces a team's flags each time it runs,
    so nobody edits them by hand.
    """

    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        FLAG = "flag", "Flag"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="flags")
    # The student the flag is about. Empty for a flag about the whole team.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="flags",
        null=True,
        blank=True,
    )
    # Names the rule that raised it, such as "low-share-week".
    rule = models.SlugField(max_length=50)
    # Shown to instructors. Describes observable activity only.
    reason = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices)
    # The Monday that starts the reporting week the rule looked at.
    week_start = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(severity__in=["warning", "flag"]),
                name="dashboard_flag_severity_valid",
            ),
        ]

    def __str__(self):
        return f"{self.rule} on {self.team}, week of {self.week_start}"


class FlagDispute(models.Model):
    """A student's explanation of a flag or warning about them, and the instructor's decision.

    It isn't linked to a Flag row, because compute_flags replaces a team's flags on every run.
    It matches its flag by team, student, rule and week instead (see services.dispute_for), and
    keeps the flag's reason and severity as they were when the student disputed it.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FLAG_KEPT = "flag_kept", "Flag kept"
        FLAG_WITHDRAWN = "flag_withdrawn", "Flag withdrawn"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="flag_disputes")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="flag_disputes"
    )
    # With team, student and week_start, these identify the disputed flag.
    rule = models.SlugField(max_length=50)
    week_start = models.DateField()
    # The flag as the student saw it when they disputed it.
    flag_reason = models.TextField()
    severity = models.CharField(max_length=20, choices=Flag.Severity.choices)
    # The student's own words. Never sent to the LLM.
    explanation = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_flag_disputes",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "student", "rule", "week_start"],
                name="dashboard_flagdispute_one_per_flag",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "flag_kept", "flag_withdrawn"]),
                name="dashboard_flagdispute_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(severity__in=["warning", "flag"]),
                name="dashboard_flagdispute_severity_valid",
            ),
        ]

    def __str__(self):
        # Leaves out the explanation, a student education record, so it stays out of logs.
        return (
            f"{self.student.github_login}'s dispute of {self.rule} on {self.team}, "
            f"week of {self.week_start}"
        )


class GenerationRun(models.Model):
    """Marks a team as generating summaries. It exists only while a run is going.

    The link to the team is one-to-one, so the database refuses a second run even if two
    start at the same moment. If the server dies mid-run nothing removes the row, so it stops
    counting at `expires_at`.
    """

    team = models.OneToOneField(Team, on_delete=models.CASCADE, related_name="generation_run")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"{self.team} generating since {self.started_at}"
