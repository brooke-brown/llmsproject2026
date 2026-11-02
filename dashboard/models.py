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
