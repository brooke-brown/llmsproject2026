from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """A person. Roles live on Enrollment, because they vary by course."""

    # 39 is GitHub's maximum login length.
    github_login = models.CharField(max_length=39)

    REQUIRED_FIELDS = ["email", "github_login"]

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

    def __str__(self):
        return f"{self.user} on {self.team}"


class Repo(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="repos")
    url = models.URLField()

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
