from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

REPO_FULL_NAME_PATTERN = r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"


class User(AbstractUser):
    """A person. Roles live on Enrollment, because they vary by course."""

    # 39 is GitHub's maximum login length.
    github_login = models.CharField(max_length=39)

    REQUIRED_FIELDS = ["email", "github_login"]

    @property
    def display_name(self):
        """What pages call this person: their name, or their GitHub login if they have none.
        Display only; GitHub matching uses github_login."""
        return self.get_full_name() or self.github_login

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(github_login=""),
                name="core_user_github_login_not_blank",
                violation_error_message="GitHub login is required.",
            ),
            models.UniqueConstraint(
                Lower("github_login"),
                name="core_user_github_login_unique_ci",
                violation_error_message="A user with this GitHub login already exists.",
            ),
        ]


class Course(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Enrollment(models.Model):
    class Role(models.TextChoices):
        INSTRUCTOR = "instructor", "Instructor"
        TA = "ta", "TA"
        STUDENT = "student", "Student"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "course"], name="core_enrollment_unique"),
            models.CheckConstraint(
                condition=models.Q(role__in=["instructor", "ta", "student"]),
                name="core_enrollment_role_valid",
            ),
        ]

    def clean(self):
        super().clean()
        stored = Enrollment.objects.filter(pk=self.pk).first()
        if stored is None or stored.role != self.Role.STUDENT:
            return
        unchanged = (self.user_id, self.course_id, self.role) == (
            stored.user_id,
            stored.course_id,
            stored.role,
        )
        if unchanged:
            return
        # Only a student enrolled in the course may stay on one of its teams.
        membership = (
            TeamMembership.objects.filter(
                user_id=stored.user_id, team__project__course_id=stored.course_id
            )
            .select_related("team")
            .first()
        )
        if membership is not None:
            raise ValidationError(
                f"{stored.user.github_login} is on {membership.team.name} in this course. "
                "Remove them from the team before changing this enrollment."
            )

    def __str__(self):
        return f"{self.user} ({self.role}) in {self.course}"


class Project(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.name} ({self.course})"


class Team(models.Model):
    """Exists only inside its project. Reach the course through team.project.course."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class TeamMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships"
    )
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "team"], name="core_teammembership_unique"),
        ]

    def clean(self):
        """Check the rules a database constraint can't, since they span several tables.

        On the team page, `team` can be unsaved or have a project that isn't saved yet, so
        this reads the team object and never its stored row.
        """
        super().clean()
        try:
            team = self.team
        except Team.DoesNotExist:
            return
        if self.user_id is None or team.project_id is None:
            return

        errors = []
        is_student = Enrollment.objects.filter(
            user_id=self.user_id, course_id=team.project.course_id, role=Enrollment.Role.STUDENT
        ).exists()
        if not is_student:
            errors.append(
                f"{self.user.github_login} isn't enrolled as a student in {team.project.course}."
            )
        other = (
            TeamMembership.objects.filter(user_id=self.user_id, team__project_id=team.project_id)
            .exclude(pk=self.pk)
            .select_related("team")
            .first()
        )
        if other is not None:
            errors.append(
                f"{self.user.github_login} is already on {other.team.name} in this project."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.user} on {self.team}"


class Repo(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="repos")
    url = models.URLField()
    # Artifact IDs start with this, so nothing needs to parse `url` to compare them.
    # 140 is GitHub's 39-character owner, a slash and its 100-character repo name.
    full_name = models.CharField(
        max_length=140,
        validators=[
            RegexValidator(
                rf"^{REPO_FULL_NAME_PATTERN}\Z", "Enter the repo as owner/repo, such as acme/app."
            )
        ],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(full_name__regex=rf"^{REPO_FULL_NAME_PATTERN}\Z"),
                name="core_repo_full_name_valid",
                violation_error_message="Enter the repo as owner/repo, such as acme/app.",
            ),
            # GitHub treats names differing only by case as the same repo.
            models.UniqueConstraint(
                "team",
                Lower("full_name"),
                name="core_repo_team_full_name_unique_ci",
                violation_error_message="This team already has this repo.",
            ),
        ]

    def __str__(self):
        return self.url


class Requirement(models.Model):
    """Claims' requirement_ids hold `number`, not the database ID."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="requirements")
    number = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="core_requirement_unique"),
        ]

    def __str__(self):
        return f"{self.number}. {self.text}"
