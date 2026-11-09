import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Case, Count, Exists, Max, OuterRef, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.formats import date_format

from core.models import (
    Course,
    Enrollment,
    Project,
    Repo,
    Requirement,
    Team,
    TeamMembership,
    User,
)
from core import services as setup_services
from ingest import services as ingest_services
from ingest.models import Artifact
from summaries import services as summaries_services
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from . import flags
from .models import Flag, FlagDispute, GenerationRun

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]
MONDAY = 0  # date.weekday() counts from Monday.
# How long a run may hold its team: the GitHub pull, plus up to 3 LLM calls of 2 minutes each
# for each student. A run that dies is cleared after this.
RUN_MINUTES_FOR_PULL = 10
RUN_MINUTES_PER_STUDENT = 6
RESULTS_SESSION_KEY = "run_results"
RETRY_SESSION_KEY = "failed_summaries"  # {team ID: {"week_start": ..., "student_ids": [...]}}


def flags_in_display_order():
    # "flag" sorts before "warning", so flags come first.
    return Flag.objects.order_by("severity", "id")


def dispute_for(flag):
    """The student's dispute of this flag, or None. Flags are replaced on every run, so this
    matches on what a recomputed flag keeps: team, student, rule and week, not wording or
    severity. A flag about the whole team has no student, so it has no dispute."""
    if flag.user_id is None:
        return None
    return FlagDispute.objects.filter(
        team_id=flag.team_id, student_id=flag.user_id, rule=flag.rule, week_start=flag.week_start
    ).first()


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


def student_courses(user):
    """Courses where the user is a student."""
    return Course.objects.filter(
        enrollments__user=user, enrollments__role=Enrollment.Role.STUDENT
    ).order_by("name")


def is_course_student(user, course):
    return Enrollment.objects.filter(
        user=user, course=course, role=Enrollment.Role.STUDENT
    ).exists()


def get_team_for_student(user, team_id):
    """Fetch a team the user is on. 404 if it doesn't exist, 403 if they aren't on it."""
    team = get_object_or_404(Team.objects.select_related("project__course"), pk=team_id)
    if not team.memberships.filter(user=user).exists():
        raise PermissionDenied
    return team


def check_course_staff(user, course):
    """Raise PermissionDenied unless the user is an instructor or TA in this course."""
    is_staff = Enrollment.objects.filter(
        user=user, course=course, role__in=STAFF_ROLES
    ).exists()
    if not is_staff:
        raise PermissionDenied


def is_course_instructor(user, course):
    return Enrollment.objects.filter(
        user=user, course=course, role=Enrollment.Role.INSTRUCTOR
    ).exists()


def check_course_instructor(user, course):
    """Raise PermissionDenied unless the user is an instructor in this course. Setting up a
    course is theirs alone, so this is stricter than check_course_staff."""
    if not is_course_instructor(user, course):
        raise PermissionDenied


def get_course_for_instructor(user, course_id):
    """The course, or 404 if it doesn't exist and 403 if the user isn't its instructor."""
    course = get_object_or_404(Course, pk=course_id)
    check_course_instructor(user, course)
    return course


def get_project_for_instructor(user, project_id):
    project = get_object_or_404(Project.objects.select_related("course"), pk=project_id)
    check_course_instructor(user, project.course)
    return project


def get_requirement_for_instructor(user, requirement_id):
    requirement = get_object_or_404(
        Requirement.objects.select_related("project__course"), pk=requirement_id
    )
    check_course_instructor(user, requirement.project.course)
    return requirement


def get_team_for_instructor(user, team_id):
    team = get_object_or_404(Team.objects.select_related("project__course"), pk=team_id)
    check_course_instructor(user, team.project.course)
    return team


def get_membership_for_instructor(user, membership_id):
    membership = get_object_or_404(
        TeamMembership.objects.select_related("user", "team__project__course"), pk=membership_id
    )
    check_course_instructor(user, membership.team.project.course)
    return membership


def get_repo_for_instructor(user, repo_id):
    repo = get_object_or_404(Repo.objects.select_related("team__project__course"), pk=repo_id)
    check_course_instructor(user, repo.team.project.course)
    return repo


def parse_id(value):
    """A database ID from a query string, or None if `value` can't be one."""
    return int(value) if value and value.isdecimal() else None


def project_setup_teams(project):
    """The project's teams by name, each with its `student_count` and `repo_count`."""
    return project.teams.annotate(
        student_count=Count("memberships", distinct=True),
        repo_count=Count("repos", distinct=True),
    ).order_by("name")


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
    return (
        Summary.objects.filter(team=summary.team, user=summary.user)
        .order_by("-week_start")
        .prefetch_related("validation_runs")
    )


def team_status(team):
    """'flag' or 'warning' for a flagged team, 'ok' for one that has a complete summary and no
    flags, 'in_progress' for one with only partial summaries, and 'none' for one nothing has
    been generated for yet."""
    severities = set(Flag.objects.filter(team=team).values_list("severity", flat=True))
    if Flag.Severity.FLAG in severities:
        return "flag"
    if Flag.Severity.WARNING in severities:
        return "warning"
    summaries = list(team.summaries.prefetch_related("validation_runs"))
    if not summaries:
        return "none"
    return "in_progress" if all(summary.is_partial for summary in summaries) else "ok"


def team_repos(team):
    """The team's repos, each with `has_artifacts`, since those can't be changed, and
    `has_cited_artifacts`, since those can't be removed either."""
    stored = Artifact.objects.filter(repo=OuterRef("pk"))
    cited = Claim.objects.filter(artifacts__repo=OuterRef("pk"))
    return team.repos.annotate(
        has_artifacts=Exists(stored), has_cited_artifacts=Exists(cited)
    ).order_by("full_name")


def remove_student(membership):
    """Take a student off their team, then recompute its flags, since the shares change.
    Their disputes on the team go too, since no page could reach them once they're gone."""
    with transaction.atomic():
        FlagDispute.objects.filter(team=membership.team_id, student=membership.user_id).delete()
        setup_services.remove_student(membership)
        flags.compute_flags(membership.team)


def team_wide_flags(team):
    """The team's flags that aren't about one student."""
    return flags_in_display_order().filter(team=team, user=None)


def team_memberships_with_summaries(team):
    """The team's memberships, each user with this team's `team_summaries`, newest first,
    `team_flags` about them and their `team_disputes`."""
    summaries = (
        Summary.objects.filter(team=team)
        .annotate(claim_count=Count("claims", filter=Q(claims__status=Claim.Status.VERIFIED)))
        .order_by("-week_start")
        .prefetch_related("validation_runs")
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
            Prefetch(
                "user__flag_disputes",
                queryset=FlagDispute.objects.filter(team=team),
                to_attr="team_disputes",
            ),
        )
    )


# The pie's colours for students with no flag or warning. They are theme variables in
# base.html, none of them red or amber.
PIE_PALETTE = [f"var(--pie-{number})" for number in range(1, 7)]
FLAG_COLOUR = "var(--flag)"
WARNING_COLOUR = "var(--warn)"
PIE_CENTRE = 50  # The pie is drawn in a 100 x 100 box, with a radius of 50.


def _point(fraction):
    """Where `fraction` of the way round the pie, clockwise from the top, is on its edge."""
    angle = 2 * math.pi * float(fraction)
    x = PIE_CENTRE + PIE_CENTRE * math.sin(angle)
    y = PIE_CENTRE - PIE_CENTRE * math.cos(angle)
    # Adding 0.0 turns a rounded -0.0 into 0.0.
    return f"{round(x, 3) + 0.0:g}", f"{round(y, 3) + 0.0:g}"


def _slice_path(start, end):
    """The SVG path for the wedge from `start` to `end`, both exact fractions of the circle."""
    (x1, y1), (x2, y2) = _point(start), _point(end)
    large_arc = 1 if end - start > Fraction(1, 2) else 0
    return f"M{PIE_CENTRE} {PIE_CENTRE} L{x1} {y1} A{PIE_CENTRE} {PIE_CENTRE} 0 {large_arc} 1 {x2} {y2} Z"


def _pie_colours(severities):
    """A colour per slice: red for a flag, amber for a warning, and otherwise the next palette
    colour that differs from the slices either side, including round the circle."""
    colours = [None] * len(severities)
    for index, severity in enumerate(severities):
        if severity == Flag.Severity.FLAG:
            colours[index] = FLAG_COLOUR
        elif severity == Flag.Severity.WARNING:
            colours[index] = WARNING_COLOUR
    pick = 0
    for index, colour in enumerate(colours):
        if colour is not None:
            continue
        avoid = set()
        if index > 0:
            avoid.add(colours[index - 1])
        if index == len(colours) - 1:
            avoid.add(colours[0])
        while PIE_PALETTE[pick % len(PIE_PALETTE)] in avoid:
            pick += 1
        colours[index] = PIE_PALETTE[pick % len(PIE_PALETTE)]
        pick += 1
    return colours


def add_contribution_shares(team, memberships):
    """Give each of the team's memberships `contribution_share`, the student's whole-percent
    share of the team's requirement-tied work, and return the memberships and the week the
    shares are for.

    It's the share the low-share flag rule uses, rounded the same way as flag reasons, but it
    includes a partial week, which the rule skips. So each membership also gets `chart_flag`,
    its worst flag if it's about the week being shown, otherwise None. Before there's anything
    to compare, the week and every share are None.

    Each membership also gets `pie_order`, its place in the chart and legend (largest share
    first), and `pie`, the slice to draw for a student with a share above 0%, otherwise None.
    A slice has `color`, `full` (True if it is the whole circle) and `path`. Slices are
    sized from the exact shares, so they always fill the circle.
    """
    memberships = list(memberships)
    shares = flags.requirement_shares(team, include_partial=True)
    week, shares = shares if shares is not None else (None, {})
    for membership in memberships:
        share = shares.get(membership.user_id)
        about_this_week = [f for f in membership.user.team_flags if f.week_start == week]
        membership.chart_flag = about_this_week[0] if about_this_week else None
        membership.contribution_share = None if share is None else round(share * 100)
        membership.pie_order = None
        membership.pie = None

    ordered = sorted(
        (m for m in memberships if m.contribution_share is not None),
        key=lambda m: -shares[m.user_id],
    )
    for position, membership in enumerate(ordered):
        membership.pie_order = position

    drawn = [m for m in ordered if shares[m.user_id] > 0]
    worst = [m.chart_flag.severity if m.chart_flag else None for m in drawn]
    start = Fraction(0)
    for membership, colour in zip(drawn, _pie_colours(worst)):
        end = start + shares[membership.user_id]
        membership.pie = {
            "color": colour,
            "full": end - start == 1,
            "path": _slice_path(start, end),
        }
        start = end
    return memberships, week


def share_week_note(team, week):
    """Why the shares for this week may change: "in progress", "partial", or "" if complete."""
    summaries = team.summaries.filter(week_start=week).prefetch_related("validation_runs")
    partial = [summary for summary in summaries if summary.is_partial]
    if not partial:
        return ""
    return "in progress" if partial[0].week_is_running else "partial"


def pie_legend(memberships):
    """The memberships that have a share, in chart order: largest share first."""
    return sorted(
        (m for m in memberships if m.pie_order is not None), key=lambda m: m.pie_order
    )


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
        .prefetch_related(
            Prefetch("memberships", queryset=memberships),
            "repos",
            "summaries__validation_runs",
        )
    )
    projects = list(
        course.projects.order_by("id").prefetch_related(
            Prefetch("teams", queryset=teams),
            Prefetch("requirements", queryset=Requirement.objects.order_by("number")),
        )
    )
    for project in projects:
        for team in project.teams.all():
            _add_summary_status(team)
    return projects


def _add_summary_status(team):
    """Give the team `has_complete_summary`, and `latest_week_note`, which says if its latest
    week is partial."""
    summaries = list(team.summaries.all())
    team.has_complete_summary = any(not summary.is_partial for summary in summaries)
    latest = [summary for summary in summaries if summary.week_start == team.latest_week]
    team.latest_week_note = ""
    if any(summary.is_partial for summary in latest):
        team.latest_week_note = "in progress" if latest[0].week_is_running else "partial"


class GenerationError(Exception):
    """A summary couldn't be generated. The message is shown to the user as is."""


def reporting_today(now=None):
    """Today's date in the reporting time zone."""
    return (now or timezone.now()).astimezone(ingest_services.REPORTING_TIME_ZONE).date()


def current_week_start(now=None):
    """The Monday of the Monday-Sunday week happening now."""
    today = reporting_today(now)
    return today - timedelta(days=today.weekday())


def last_completed_week_start(now=None):
    """The Monday of the most recent Monday-Sunday week that has fully ended."""
    return current_week_start(now) - timedelta(days=7)


def parse_week_start(value, now=None):
    """The Monday that starts a reporting week that has started, from a YYYY-MM-DD string.

    Raises GenerationError, with a message for the user, for anything else.
    """
    try:
        week_start = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise GenerationError("Pick a week to generate.") from None
    if week_start.weekday() != MONDAY:
        raise GenerationError("The week must start on a Monday.")
    if week_start > current_week_start(now):
        raise GenerationError("That week hasn't started yet. Pick this week or an earlier one.")
    return week_start


def student_name(student):
    return student.display_name


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
            message = f"Generated summaries for {students} on {self.team.name} for {week}."
            if self.week_start > last_completed_week_start():
                message += (
                    " This week is still in progress, so these summaries are partial and don't "
                    "count toward flags until you generate the week again after it ends."
                )
            return message
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


def generating_team_ids(course):
    """The IDs of the course's teams generating summaries right now."""
    return list(
        GenerationRun.objects.filter(
            team__project__course=course, expires_at__gt=timezone.now()
        )
        .order_by("team_id")
        .values_list("team_id", flat=True)
    )


@contextmanager
def _team_run(team, student_count):
    """Hold the team's one run for the length of the block, however the block ends."""
    run = _start_run(team, student_count)
    try:
        yield
    finally:
        _finish_run(run)


def _start_run(team, student_count):
    """Record that the team is generating, or raise GenerationError if it already is."""
    now = timezone.now()
    # Checked before the transaction, which waits for any other run's lock. A second run that
    # waited could find the first one finished by then, and start all over again.
    if GenerationRun.objects.filter(team=team, expires_at__gt=now).exists():
        raise GenerationError(_already_running_message(team))
    limit = RUN_MINUTES_FOR_PULL + RUN_MINUTES_PER_STUDENT * student_count
    try:
        with transaction.atomic():
            GenerationRun.objects.filter(team=team, expires_at__lte=now).delete()
            return GenerationRun.objects.create(
                team=team, expires_at=now + timedelta(minutes=limit)
            )
    except IntegrityError:
        raise GenerationError(_already_running_message(team)) from None
    except OperationalError:
        raise GenerationError(
            "The database is busy with another run, so nothing was generated. Please try again."
        ) from None


def _already_running_message(team):
    run = GenerationRun.objects.filter(team=team).first()
    clears = f", or after {_clock_time(run.expires_at)} if this looks stuck" if run else ""
    return f"Summaries are already being generated for this team. Try again when they finish{clears}."


def _clock_time(moment):
    return date_format(timezone.localtime(moment), "g:i A")


def _finish_run(run):
    # A busy database mustn't turn a finished run into an error. The record expires by itself.
    try:
        run.delete()
    except OperationalError:
        pass


def save_run_result(session, generation):
    """Keep what the overview needs to report on a finished run: its message and who to retry.

    It's in the session, not a message on the response, because the browser may have left by
    now. A run outlasts other requests, so its copy of the session is out of date. Holding the
    database's lock while rereading and saving means nothing saved meanwhile is overwritten.
    """
    with transaction.atomic():
        _reread(session)
        remember_retry(session, generation)
        results = session.setdefault(RESULTS_SESSION_KEY, [])
        results.append({"succeeded": generation.succeeded, "message": generation.message})
        session.save()
    session.modified = False  # Already saved, so the middleware needn't save an older moment.


def take_run_results(session):
    """Remove and return the results saved by save_run_result."""
    if RESULTS_SESSION_KEY not in session:
        return []
    with transaction.atomic():
        _reread(session)
        results = session.pop(RESULTS_SESSION_KEY, [])
        session.save()
    session.modified = False
    return results


def _reread(session):
    session._session_cache = session.load()


def generate_team_summaries(team, week_start):
    """Ingest the team's repos once, then write a summary for each student on the team.

    Raises GenerationError, with nothing run, for a team with no students or no repo, and
    for a failed ingest, with no LLM calls made. Otherwise returns a TeamGeneration, which
    lists any students whose summaries failed. They keep any earlier summary for the week.
    """
    students = _team_students(team)
    if not students:
        raise GenerationError("This team has no students yet, so nothing was generated.")
    # Otherwise every student would get a "No activity" summary, which reads like a finding.
    if not team.repos.exists():
        raise GenerationError("This team has no repo yet, so nothing was generated.")
    with _team_run(team, len(students)):
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
    with _team_run(team, len(students)):
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
