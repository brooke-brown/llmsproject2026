from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from core.models import REPO_FULL_NAME_PATTERN, Repo

ARTIFACT_ID_PATTERN = rf"^{REPO_FULL_NAME_PATTERN}:(commit|pr|review|issue):[A-Za-z0-9]+\Z"


class Artifact(models.Model):
    """A commit, PR, review or issue pulled from a repo.

    `artifact_id` is unique per Repo, not globally: a later project reusing a GitHub
    repo has its own Repo row and its own copies. Look IDs up within a team's repos.
    """

    class Type(models.TextChoices):
        COMMIT = "commit", "Commit"
        PR = "pr", "Pull request"
        REVIEW = "review", "Review"
        ISSUE = "issue", "Issue"

    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name="artifacts")
    artifact_id = models.CharField(
        max_length=255,
        validators=[
            RegexValidator(
                ARTIFACT_ID_PATTERN, "Use the format owner/repo:type:id, such as acme/app:pr:12."
            )
        ],
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    # Long enough for a commit URL with GitHub's longest owner and repo names.
    url = models.URLField(max_length=500)
    timestamp = models.DateTimeField()
    # A PR or issue title, or a commit message. Reviews have none.
    title = models.TextField(blank=True)
    body = models.TextField(blank=True)
    # Empty when a commit's git email isn't linked to a GitHub account.
    author_login = models.CharField(max_length=39, blank=True)
    # Exactly what git recorded, which needn't be a valid name or email address.
    git_author_name = models.TextField(blank=True)
    git_author_email = models.TextField(blank=True)
    co_author_logins = models.JSONField(default=list, blank=True)
    # Each entry is {"name": ..., "email": ...} from a Co-authored-by trailer.
    unmatched_co_authors = models.JSONField(default=list, blank=True)
    diff = models.TextField(blank=True)
    diff_truncated = models.BooleanField(default=False)
    # GitHub's value for a review, such as APPROVED. Empty for every other type.
    review_state = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["repo", "artifact_id"], name="ingest_artifact_repo_artifact_id_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=["commit", "pr", "review", "issue"]),
                name="ingest_artifact_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(artifact_id__regex=ARTIFACT_ID_PATTERN),
                name="ingest_artifact_id_format_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.repo_id is None:
            return
        # Compared exactly, so every ID for a repo is spelled the same way. A database
        # constraint can't reach across tables, so ingest must build IDs from full_name.
        prefix = f"{self.repo.full_name}:{self.type}:"
        if not self.artifact_id.startswith(prefix):
            raise ValidationError({"artifact_id": f"This artifact's ID must start with {prefix}"})

    @property
    def review_state_label(self) -> str:
        """The review state as a page shows it: CHANGES_REQUESTED is "changes requested"."""
        return self.review_state.replace("_", " ").lower()

    def __str__(self):
        return self.artifact_id
