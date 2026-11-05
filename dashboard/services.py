import re
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, Count, Exists, Max, OuterRef, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import Course, Enrollment, Requirement, Team, TeamMembership, User
from ingest import services as ingest_services
from ingest.models import Artifact
from summaries import services as summaries_services
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from . import flags
from .models import Flag

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]
MONDAY = 0  # date.weekday() counts from Monday.
RETRY_SESSION_KEY = "failed_summaries"  # {team ID: {"week_start": ..., "student_ids": [...]}}


def flags_in_display_order():
    # "flag" sorts before "warning", so flags come first.
    return Flag.objects.order_by("severity", "id")


def staff_courses(user):
    """Courses where the user is an instructor or TA."""
    return Course.objects.filter(
        enrollments__user=user, enrollments__role__in=STAFF_ROLES
    ).order_by("name")


def course_cards(user):
    """The courses the user staffs, each with `role_label` and counts of its teams, so the
    course picker can show where to look first."""
    enrollments = (
        Enrollment.objects.filter(user=user, role__in=STAFF_ROLES)
        .select_related("course")
        .order_by("course__name")
    )
    courses = []
    for enrollment in enrollments:
        course = enrollment.course
        teams = Team.objects.filter(project__course=course)
        course.role_label = enrollment.get_role_display()
        course.team_count = teams.count()
        course.flagged_count = teams.filter(_has_flag(Flag.Severity.FLAG)).count()
        course.warned_count = (
            teams.filter(_has_flag(Flag.Severity.WARNING))
            .exclude(_has_flag(Flag.Severity.FLAG))
            .count()
        )
        courses.append(course)
    return courses


def check_course_staff(user, course):
    """Raise PermissionDenied unless the user is an instructor or TA in this course."""
    is_staff = Enrollment.objects.filter(
        user=user, course=course, role__in=STAFF_ROLES
    ).exists()
    if not is_staff:
        raise PermissionDenied


def get_course_for_staff(user, course_id):
    """The course, or 404 if it doesn't exist and 403 if the user isn't its staff."""
    course = get_object_or_404(Course, pk=course_id)
    check_course_staff(user, course)
    return course


def get_team_for_staff(user, team_id):
    team = get_object_or_404(Team.objects.select_related("project__course"), pk=team_id)
    check_course_staff(user, team.project.course)
    return team


def get_summary_for_staff(user, summary_id):
    """The summary, or 404 if it doesn't exist and 403 if the user isn't its course's staff."""
    summary = get_object_or_404(
        Summary.objects.select_related("user", "team__project__course"), pk=summary_id
    )
    check_course_staff(user, summary.team.project.course)
    return summary


def week_end(week_start):
    """The Sunday that ends the Monday-Sunday reporting week."""
    return week_start + timedelta(days=6)


def evidence_label(artifact):
    """The short text of an artifact's GitHub link, such as "PR 45"."""
    identifier = artifact.artifact_id.rsplit(":", 1)[-1]
    if artifact.type == Artifact.Type.COMMIT:
        return f"Commit {identifier[:7]}"
    if artifact.type == Artifact.Type.PR:
        return f"PR {identifier}"
    if artifact.type == Artifact.Type.ISSUE:
        return f"Issue {identifier}"
    # A review's own ID is GitHub's internal one, so its PR number comes from its URL.
    pull = re.search(r"/pull/(\d+)", artifact.url)
    return f"Review on PR {pull.group(1)}" if pull else "Review"


def verified_claims(summary):
    """The summary's verified claims, the only ones a user may see.

    Each claim has its artifacts prefetched for the GitHub links, each with an
    `evidence_label`, and `tagged_requirements`: a (number, text) pair per distinct
    requirement_id, sorted by number, with text None when the team's project has no
    requirement with that number.
    """
    requirements = dict(summary.team.project.requirements.values_list("number", "text"))
    claims = list(
        summary.claims.filter(status=Claim.Status.VERIFIED)
        .order_by("id")
        .prefetch_related(
            Prefetch("artifacts", queryset=Artifact.objects.order_by("artifact_id"))
        )
    )
    for claim in claims:
        for artifact in claim.artifacts.all():
            artifact.evidence_label = evidence_label(artifact)
        claim.tagged_requirements = [
            (number, requirements.get(number)) for number in sorted(set(claim.requirement_ids))
        ]
    return claims


def student_weeks(summary):
    """All of this student's summaries for the team, newest first, for the week switcher."""
    return Summary.objects.filter(team=summary.team, user=summary.user).order_by("-week_start")


def team_status(team):
    """'flag' or 'warning' for a flagged team, 'ok' for one that has summaries and no flags,
    and 'none' for one nothing has been generated for yet."""
    severities = set(Flag.objects.filter(team=team).values_list("severity", flat=True))
    if Flag.Severity.FLAG in severities:
        return "flag"
    if Flag.Severity.WARNING in severities:
        return "warning"
    return "ok" if Summary.objects.filter(team=team).exists() else "none"


def team_repos(team):
    return team.repos.order_by("full_name")


def team_wide_flags(team):
    """The team's flags that aren't about one student."""
    return flags_in_display_order().filter(team=team, user=None)


def team_memberships_with_summaries(team):
    """The team's memberships, each user with this team's `team_summaries`, newest first,
    and `team_flags` about them."""
    summaries = (
        Summary.objects.filter(team=team)
        .annotate(claim_count=Count("claims", filter=Q(claims__status=Claim.Status.VERIFIED)))
        .order_by("-week_start")
    )
    return (
        team.memberships.select_related("user")
        .order_by("user__last_name", "user__first_name", "user__github_login")
        .prefetch_related(
            Prefetch("user__summaries", queryset=summaries, to_attr="team_summaries"),
            Prefetch(
                "user__flags",
                queryset=flags_in_display_order().filter(team=team),
                to_attr="team_flags",
            ),
        )
    )


def add_contribution_shares(team, memberships):
    """Give each of the team's memberships `contribution_share`, the student's whole-percent
    share of the team's requirement-tied work, and return the memberships and the week the
    shares are for.

    It's the share the low-share flag rule uses, rounded the same way as flag reasons.
    Before there's anything to compare, the week and every share are None.
    """
    memberships = list(memberships)
    shares = flags.requirement_shares(team)
    week, shares = shares if shares is not None else (None, {})
    for membership in memberships:
        share = shares.get(membership.user_id)
        membership.contribution_share = None if share is None else round(share * 100)
        # An equal share is drawn at the middle of the track, so the bar is twice the share.
        membership.contribution_bar = (
            None if share is None else min(membership.contribution_share * 2, 100)
        )
    return memberships, week


def _has_flag(severity):
    """True for a team with at least one flag of this severity, inside a Team query."""
    return Exists(Flag.objects.filter(team=OuterRef("pk"), severity=severity))


def course_projects_with_teams(course):
    """The course's projects, each with its requirements by number, and its teams and their
    students.

    Within a project, flagged teams come first, then warned teams, then the rest, each
    group sorted by name. Each team's `flag_rank` is 0 if it has a flag, 1 if its worst is a
    warning, and 2 otherwise.
    """
    memberships = TeamMembership.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__github_login"
    )
    teams = (
        Team.objects.annotate(
            latest_week=Max("summaries__week_start"),
            flag_rank=Case(
                When(_has_flag(Flag.Severity.FLAG), then=Value(0)),
                When(_has_flag(Flag.Severity.WARNING), then=Value(1)),
                default=Value(2),
            )
        )
        .order_by("flag_rank", "name")
        .prefetch_related(Prefetch("memberships", queryset=memberships))
    )
    return course.projects.order_by("id").prefetch_related(
        Prefetch("teams", queryset=teams),
        Prefetch("requirements", queryset=Requirement.objects.order_by("number")),
    )


class GenerationError(Exception):
    """A summary couldn't be generated. The message is shown to the user as is."""


def reporting_today(now=None):
    """Today's date in the reporting time zone."""
    return (now or timezone.now()).astimezone(ingest_services.REPORTING_TIME_ZONE).date()


def last_completed_week_start(now=None):
    """The Monday of the most recent Monday-Sunday week that has fully ended."""
    today = reporting_today(now)
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def parse_week_start(value, now=None):
    """The Monday that starts a completed reporting week, from a YYYY-MM-DD string.

    Raises GenerationError, with a message for the user, for anything else.
    """
    try:
        week_start = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise GenerationError("Pick a week to generate.") from None
    if week_start.weekday() != MONDAY:
        raise GenerationError("The week must start on a Monday.")
    if week_start > last_completed_week_start(now):
        raise GenerationError("That week hasn't ended yet. Pick a week that's already over.")
    return week_start


def student_name(student):
    return student.get_full_name() or student.github_login


@dataclass(frozen=True)
class SummaryFailure:
    student: User
    reason: str  # Shown to staff, so it never holds student work.


@dataclass(frozen=True)
class TeamGeneration:
    """How one run of writing a team's summaries went."""

    team: Team
    week_start: date
    attempted: int
    failures: list[SummaryFailure]

    @property
    def succeeded(self):
        return not self.failures

    @property
    def message(self):
        week = f"the week of {self.week_start:%b} {self.week_start.day}, {self.week_start.year}"
        if not self.attempted:
            return (
                f"Nobody left to retry on {self.team.name} for {week}: the students whose "
                "summaries failed have left the team."
            )
        if self.succeeded:
            students = "1 student" if self.attempted == 1 else f"{self.attempted} students"
            return f"Generated summaries for {students} on {self.team.name} for {week}."
        done = self.attempted - len(self.failures)
        failed = "; ".join(
            f"{student_name(failure.student)} ({failure.reason})" for failure in self.failures
        )
        return (
            f"Generated {done} of {self.attempted} summaries for {self.team.name} for {week}. "
            f"Couldn't generate: {failed}. Flags weren't updated."
        )


def _team_students(team, only_ids=None):
    """The team's students in display order, limited to `only_ids` if it's given."""
    memberships = team.memberships.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__github_login"
    )
    if only_ids is not None:
        memberships = memberships.filter(user_id__in=only_ids)
    return [membership.user for membership in memberships]


def _ingest_team_week(team, week_start):
    """Ingest each of the team's repos for the week, or raise GenerationError naming the
    repo. It's all-or-nothing, so a failure on one repo leaves the others as they were."""
    end = week_end(week_start)
    with transaction.atomic():
        for repo in team_repos(team):
            try:
                ingest_services.ingest(repo, week_start, end)
            except ingest_services.IngestError as error:
                # IngestError messages are written for users and never hold the token.
                raise GenerationError(
                    f"Couldn't pull GitHub activity for {repo.full_name}: {error} "
                    "No summaries were generated."
                ) from None
            except Exception:
                # Anything else is unexpected. Its error isn't shown, so nothing from the
                # repo reaches the page.
                raise GenerationError(
                    f"Couldn't pull GitHub activity for {repo.full_name}. "
                    "No summaries were generated."
                ) from None


def _write_summaries(team, students, week_start):
    """Write each student's summary, carrying on past a failure, and recompute the team's
    flags only if none failed.

    A partial run could leave a failed student's old summary next to everyone's new one, so
    flags wait until every student has succeeded.
    """
    failures = []
    for student in students:
        try:
            summaries_services.generate_summary(student, team, week_start)
        except LLMError as error:
            # LLMError messages never hold student work, so they are safe to show.
            failures.append(SummaryFailure(student, str(error)))
        except Exception:
            # Anything else is unexpected. Its error isn't shown, so no student work can
            # reach the page.
            failures.append(SummaryFailure(student, "an unexpected error"))
    if not failures:
        flags.compute_flags(team)
    return TeamGeneration(team, week_start, len(students), failures)


def generate_team_summaries(team, week_start):
    """Ingest the team's repos once, then write a summary for each student on the team.

    Raises GenerationError, with nothing run, for a team with no students, and for a failed
    ingest, with no LLM calls made. Otherwise returns a TeamGeneration, which lists any
    students whose summaries failed. They keep any earlier summary for the week.
    """
    students = _team_students(team)
    if not students:
        raise GenerationError("This team has no students yet, so nothing was generated.")
    _ingest_team_week(team, week_start)
    return _write_summaries(team, students, week_start)


def retry_failed_summaries(team, session):
    """Write summaries again for only the students whose summaries failed, or return None
    if the session has nothing to retry for this team.

    Ingest doesn't run again, so every student's summary for the week uses the same GitHub
    data. Anyone who has left the team since is skipped.
    """
    retry = session.get(RETRY_SESSION_KEY, {}).get(str(team.id))
    if retry is None:
        return None
    students = _team_students(team, only_ids=retry["student_ids"])
    return _write_summaries(team, students, date.fromisoformat(retry["week_start"]))


def remember_retry(session, generation):
    """Keep this team's failed students in the session, replacing any it had, or clear them
    if none failed."""
    retries = session.get(RETRY_SESSION_KEY, {})
    key = str(generation.team.id)
    if generation.failures:
        retries[key] = {
            "week_start": generation.week_start.isoformat(),
            "student_ids": [failure.student.id for failure in generation.failures],
        }
    else:
        retries.pop(key, None)
    session[RETRY_SESSION_KEY] = retries


def team_ids_to_retry(session):
    """The IDs of the teams with failed summaries to retry in this session."""
    return {int(team_id) for team_id in session.get(RETRY_SESSION_KEY, {})}
