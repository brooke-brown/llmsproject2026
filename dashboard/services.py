import math
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.formats import date_format

from contributions.models import Contribution, Corroboration
from core.models import (
    Course,
    Enrollment,
    Project,
    Repo,
    Requirement,
    Sprint,
    Team,
    TeamMembership,
    User,
)
from core import services as setup_services
from ingest import services as ingest_services
from ingest.models import Artifact
from peereval.models import PeerRating
from summaries import services as summaries_services
from summaries.llm import LLMError
from summaries.models import Claim, Summary

from . import flags
from .models import Flag, FlagDispute, GenerationRun

STAFF_ROLES = [Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA]
# How long a run may hold its team: the GitHub pull, plus up to 3 LLM calls of 2 minutes each
# for each student. A run that dies is cleared after this.
RUN_MINUTES_FOR_PULL = 10
RUN_MINUTES_PER_STUDENT = 6
RESULTS_SESSION_KEY = "run_results"
RETRY_SESSION_KEY = "failed_summaries"  # {team ID: {"sprint_id": ..., "student_ids": [...]}}


def _withdrawn():
    """True for a flag an instructor withdrew after a dispute, inside a Flag query. Disputes
    match on what a recomputed flag keeps, so a withdrawn flag stays withdrawn after a rerun."""
    return Exists(
        FlagDispute.objects.filter(
            team=OuterRef("team"),
            student=OuterRef("user"),
            rule=OuterRef("rule"),
            sprint=OuterRef("sprint"),
            status=FlagDispute.Status.FLAG_WITHDRAWN,
        )
    )


def active_flags():
    """Flags that count toward a team being flagged: all but those withdrawn after a dispute."""
    return Flag.objects.filter(~_withdrawn())


ACTIVE_FLAGS_PREFETCH = Prefetch("flags", queryset=active_flags(), to_attr="active_flags")


def worst_severity(severities):
    """"flag" or "warning", the worst of `severities`, or None if there's neither."""
    for severity in [Flag.Severity.FLAG, Flag.Severity.WARNING]:
        if severity in severities:
            return severity
    return None


def sprint_severity(team, sprint):
    """"flag" or "warning", the team's worst active flag in the sprint, or None. Flags only
    exist for sprints that have ended. Prefetch ACTIVE_FLAGS_PREFETCH when labelling many
    teams."""
    if sprint is None:
        return None
    if hasattr(team, "active_flags"):
        return worst_severity({f.severity for f in team.active_flags if f.sprint_id == sprint.id})
    return worst_severity(
        set(active_flags().filter(team=team, sprint=sprint).values_list("severity", flat=True))
    )


def team_severity(team):
    """"flag" or "warning", the worst active flag in the team's latest flaggable sprint, or
    None. Only that sprint labels a team, so an earlier sprint's flags don't."""
    return sprint_severity(team, flags.latest_flaggable_sprint(team))


def flags_in_display_order():
    """Every flag, each with `is_withdrawn`. "flag" sorts before "warning", so flags come first."""
    return Flag.objects.annotate(is_withdrawn=_withdrawn()).order_by("severity", "id")


def dispute_for(flag):
    """The student's dispute of this flag, or None. Flags are replaced on every run, so this
    matches on what a recomputed flag keeps: team, student, rule and sprint, not wording or
    severity. A flag about the whole team has no student, so it has no dispute."""
    if flag.user_id is None:
        return None
    return FlagDispute.objects.filter(
        team_id=flag.team_id, student_id=flag.user_id, rule=flag.rule, sprint_id=flag.sprint_id
    ).first()


def add_disputes(memberships):
    """Give each of the team's flags in `memberships` its `dispute` (or None), and each
    membership `unraised_disputes`: its student's disputes whose flag is no longer raised.

    Uses the `team_flags` and `team_disputes` from team_memberships_with_summaries, so it runs
    no queries.
    """
    for membership in memberships:
        # Each student's flags and disputes are already this team's, so rule and sprint match them.
        disputes = {(d.rule, d.sprint_id): d for d in membership.user.team_disputes}
        for flag in membership.user.team_flags:
            flag.dispute = disputes.pop((flag.rule, flag.sprint_id), None)
        membership.unraised_disputes = list(disputes.values())
    return memberships


DISPUTE_NOTE_LIMIT = 1000
DECISIONS = {
    "keep": FlagDispute.Status.FLAG_KEPT,
    "withdraw": FlagDispute.Status.FLAG_WITHDRAWN,
}


class DisputeError(Exception):
    """A dispute decision was refused. The message is shown to the user as is."""


def get_dispute_for_instructor(user, dispute_id):
    dispute = get_object_or_404(
        FlagDispute.objects.select_related("team__project__course", "student"), pk=dispute_id
    )
    check_course_instructor(user, dispute.team.project.course)
    return dispute


def decide_dispute(dispute, decision, note, decided_by):
    """Keep or withdraw a disputed flag, or raise DisputeError saying why not. A decision can
    be changed later, and the latest one replaces the earlier one."""
    status = DECISIONS.get(decision)
    if status is None:
        raise DisputeError("Choose Keep flag or Withdraw flag.")
    note = note.strip()
    if len(note) > DISPUTE_NOTE_LIMIT:
        raise DisputeError(
            f"The note can be up to {DISPUTE_NOTE_LIMIT:,} characters, and this one has "
            f"{len(note):,}. Nothing was saved."
        )
    dispute.status = status
    dispute.resolution_note = note
    dispute.resolved_by = decided_by
    dispute.resolved_at = timezone.now()
    # Leaves out updated_at, which records the student's edits, not the instructor's decision.
    dispute.save(update_fields=["status", "resolution_note", "resolved_by", "resolved_at"])
    return dispute


DISPUTE_EXPLANATION_LIMIT = 2000
DISPUTE_DECIDED = "Your instructor has decided on this dispute, so it can't be changed."


@dataclass
class DisputePage:
    """What a student's dispute page is about. `flag` is the live flag about them, or None
    once it's no longer raised; `dispute` is their dispute of it, or None."""

    team: Team
    rule: str
    sprint: Sprint
    flag: Flag | None
    dispute: FlagDispute | None

    @property
    def severity(self):
        """The flag as the student disputed it, or as it's raised now if not disputed yet."""
        return self.dispute.severity if self.dispute else self.flag.severity

    @property
    def severity_label(self):
        return Flag.Severity(self.severity).label

    @property
    def reason(self):
        return self.dispute.flag_reason if self.dispute else self.flag.reason

    @property
    def is_withdrawn(self):
        return bool(self.dispute) and self.dispute.status == FlagDispute.Status.FLAG_WITHDRAWN

    @property
    def can_edit(self):
        return self.flag is not None and (
            self.dispute is None or self.dispute.status == FlagDispute.Status.OPEN
        )


def get_dispute_page(user, team_id, rule, sprint_id):
    """The user's flag and dispute for this team, rule and sprint. These survive compute_flags,
    unlike Flag.pk, so the page keeps working while flags are regenerated.

    403 unless the user is on the team, in a course they're a student in, and the flag is
    about them. 404 if there's no such flag, or it's no longer raised and its dispute is still
    open (#108 hides those). A decided dispute stays readable after its flag is gone.
    """
    team = get_team_for_student(user, team_id)
    if not is_course_student(user, team.project.course):
        raise PermissionDenied
    sprint = get_object_or_404(Sprint, pk=sprint_id, project=team.project)
    flag = flags_in_display_order().filter(team=team, user=user, rule=rule, sprint=sprint).first()
    dispute = FlagDispute.objects.filter(
        team=team, student=user, rule=rule, sprint=sprint
    ).select_related("resolved_by").first()
    if flag is None and (dispute is None or dispute.status == FlagDispute.Status.OPEN):
        if Flag.objects.filter(team=team, rule=rule, sprint=sprint).exists():
            raise PermissionDenied  # A teammate's flag, or one about the whole team.
        raise Http404
    return DisputePage(team, rule, sprint, flag, dispute)


def submit_dispute(flag, explanation):
    """File the student's dispute of this flag, or edit it while it's open, and return
    (dispute, created). Raise DisputeError, saying why, for a blank or too-long explanation,
    a decided dispute, or a flag that's no longer raised (None).

    The explanation is stored exactly as typed. A new dispute keeps the flag's reason and
    severity as they are now; an edit leaves them as they were when it was filed. Sending the
    same text again changes nothing, so it doesn't count as an edit.
    """
    if flag is None:
        raise DisputeError("This flag is no longer raised, so its dispute can't be changed.")
    if not explanation.strip():
        raise DisputeError("Write your explanation before sending it.")
    # Browsers send each line break as two characters but count it as one.
    length = len(explanation.replace("\r\n", "\n"))
    if length > DISPUTE_EXPLANATION_LIMIT:
        raise DisputeError(
            f"Your explanation can be up to {DISPUTE_EXPLANATION_LIMIT:,} characters, and this "
            f"one has {length:,}. Nothing was sent."
        )
    dispute = dispute_for(flag)
    if dispute is None:
        try:
            with transaction.atomic():
                dispute = FlagDispute.objects.create(
                    team_id=flag.team_id,
                    student_id=flag.user_id,
                    rule=flag.rule,
                    sprint_id=flag.sprint_id,
                    flag_reason=flag.reason,
                    severity=flag.severity,
                    explanation=explanation,
                )
                return dispute, True
        except IntegrityError:
            # Usually sent twice at once: the other request filed it, so this one edits it.
            dispute = dispute_for(flag)
            if dispute is None:
                raise DisputeError("Your dispute couldn't be saved. Please try again.")
    if dispute.status != FlagDispute.Status.OPEN:
        raise DisputeError(DISPUTE_DECIDED)
    if dispute.explanation == explanation:
        return dispute, False
    # Saved only while still open, in one statement, so an edit can't land after a decision
    # made between the lookup above and this save.
    now = timezone.now()
    edited = FlagDispute.objects.filter(pk=dispute.pk, status=FlagDispute.Status.OPEN).update(
        explanation=explanation, updated_at=now
    )
    if not edited:
        raise DisputeError(DISPUTE_DECIDED)
    dispute.explanation, dispute.updated_at = explanation, now
    return dispute, False


def staff_courses(user):
    """Courses where the user is an instructor or TA."""
    return Course.objects.filter(
        enrollments__user=user, enrollments__role__in=STAFF_ROLES
    ).order_by("name")


def course_cards(user, now=None):
    """The courses the user staffs, each with `role_label` and counts of its teams, so the
    course picker can show where to look first. A team counts as flagged or warned in the
    sprint the course overview opens on for its project (see overview_sprint), so the two
    pages agree."""
    enrollments = (
        Enrollment.objects.filter(user=user, role__in=STAFF_ROLES)
        .select_related("course")
        .order_by("course__name")
    )
    courses = []
    for enrollment in enrollments:
        course = enrollment.course
        course.role_label = enrollment.get_role_display()
        projects = course.projects.prefetch_related(
            "sprints", Prefetch("teams", queryset=Team.objects.prefetch_related(ACTIVE_FLAGS_PREFETCH))
        )
        severities = []
        for project in projects:
            sprint = overview_sprint(started_sprints(project, now)[::-1], (), now)
            severities += [sprint_severity(team, sprint) for team in project.teams.all()]
        course.team_count = len(severities)
        course.flagged_count = severities.count(Flag.Severity.FLAG)
        course.warned_count = severities.count(Flag.Severity.WARNING)
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


def check_any_course_student(user):
    """Raise PermissionDenied unless the user is a student in at least one course."""
    if not Enrollment.objects.filter(user=user, role=Enrollment.Role.STUDENT).exists():
        raise PermissionDenied


def student_home_courses(user):
    """What the student home shows: the courses the user is a student in that have a team
    they're on, by name. Each course has `memberships`, the user's own, by project name.

    Each membership has `teammates`, the rest of its team by name, and `my_flags`: the flags
    and warnings about this user on that team, newest first, each with `is_withdrawn` and its
    `dispute` (or None). Flags about teammates or the whole team are never included. It also
    has `unraised_disputes`: the user's disputes on that team whose flag is no longer raised,
    newest first, so a student can still read a dispute and its decision.
    """
    student_course_ids = Enrollment.objects.filter(
        user=user, role=Enrollment.Role.STUDENT
    ).values("course_id")
    memberships = (
        TeamMembership.objects.filter(user=user, team__project__course__in=student_course_ids)
        .select_related("team__project__course")
        .prefetch_related(
            Prefetch(
                "team__memberships",
                queryset=TeamMembership.objects.select_related("user").order_by(
                    "user__first_name", "user__last_name", "user__github_login"
                ),
                to_attr="all_memberships",
            ),
        )
        .order_by("team__project__course__name", "team__project__name", "team__name")
    )
    my_flags = (
        flags_in_display_order()
        .filter(user=user)
        .select_related("sprint")
        .order_by("-sprint__start_date", "severity", "id")
    )
    flags_by_team = {}
    for flag in my_flags:
        flags_by_team.setdefault(flag.team_id, []).append(flag)
    disputes = {
        (d.team_id, d.rule, d.sprint_id): d
        for d in FlagDispute.objects.filter(student=user)
        .select_related("sprint")
        .order_by("-sprint__start_date", "id")
    }

    courses = {}
    for membership in memberships:
        membership.teammates = [
            other.user for other in membership.team.all_memberships if other.user_id != user.id
        ]
        membership.my_flags = flags_by_team.get(membership.team_id, [])
        for flag in membership.my_flags:
            flag.dispute = disputes.pop((flag.team_id, flag.rule, flag.sprint_id), None)
        # Whatever is left for this team matched no current flag. Dicts keep insertion order.
        membership.unraised_disputes = [
            dispute for (team_id, _, _), dispute in disputes.items()
            if team_id == membership.team_id
        ]
        course = membership.team.project.course
        if course.id not in courses:
            course.memberships = []
            courses[course.id] = course
        courses[course.id].memberships.append(membership)
    return list(courses.values())


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


def get_sprint_for_instructor(user, sprint_id):
    sprint = get_object_or_404(Sprint.objects.select_related("project__course"), pk=sprint_id)
    check_course_instructor(user, sprint.project.course)
    return sprint


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


def parse_ids(values):
    """The database IDs among query string values, skipping any that can't be one."""
    return {parsed for parsed in map(parse_id, values) if parsed is not None}


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


def summary_period(summary):
    """The first and last day a summary's period spans: its sprint's, or its reporting week."""
    if summary.sprint_id is not None:
        return summary.sprint.start_date, summary.sprint.end_date
    # A weekly summary from the alpha: its Monday-Sunday week.
    return summary.week_start, summary.week_start + timedelta(days=6)


# Sprint summaries first, newest sprint first, then any weekly ones from before sprints.
SUMMARY_ORDER = (F("sprint__start_date").desc(nulls_last=True), F("week_start").desc(nulls_last=True))


def short_date(day):
    """"Oct 5", the way a sprint's dates are written."""
    return f"{day:%b} {day.day}"


def sprint_label(sprint):
    """"Sprint 2 (Oct 30 – Nov 5)"."""
    return f"{sprint.name} ({short_date(sprint.start_date)} – {short_date(sprint.end_date)})"


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
    # A requirement number means something only within its sprint.
    if summary.sprint_id is not None:
        requirements = dict(summary.sprint.requirements.values_list("number", "text"))
    else:
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
        .select_related("sprint")
        .order_by(*SUMMARY_ORDER)
        .prefetch_related("validation_runs")
    )


def team_status(team):
    """'flag' or 'warning' for a flagged team, 'ok' for one that has a complete summary and no
    flags, 'in_progress' for one with only partial summaries, and 'none' for one nothing has
    been generated for yet."""
    severity = team_severity(team)
    if severity is not None:
        return severity
    summaries = list(team.summaries.prefetch_related("validation_runs"))
    if not summaries:
        return "none"
    return "in_progress" if all(summary.is_partial for summary in summaries) else "ok"


def team_sprint_status(team, sprint, today=None):
    """The team's tag for one sprint, on both the team page and the course overview: 'flag' or
    'warning' for its worst active flag, 'ok' for a sprint that can raise flags and raised none,
    'in_progress' when a current student's summary for it is partial, otherwise 'none'. Unlike
    team_status, it's about this sprint only. Uses prefetched flags, memberships and summaries
    when there are some."""
    if sprint is None:
        return "none"
    severity = sprint_severity(team, sprint)
    if severity is not None:
        return severity
    if sprint in flags.flaggable_sprints(team, today):
        return "ok"
    students = {membership.user_id for membership in team.memberships.all()}
    partial = any(
        summary.sprint_id == sprint.id and summary.user_id in students and summary.is_partial
        for summary in flags.sprint_summaries(team)
    )
    return "in_progress" if partial else "none"


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


def team_wide_flags(team, sprint):
    """The team's flags in this sprint that aren't about one student."""
    return flags_in_display_order().filter(team=team, sprint=sprint, user=None)


def team_page_sprints(project, now=None):
    """The sprints in the team page's menu: those that have started, newest first."""
    return started_sprints(project, now)[::-1]


def team_page_sprint(team, sprints, value, now=None):
    """The sprint the team page shows, from `sprints` (team_page_sprints): the one whose ID is
    `value`, or else the latest sprint the team has a summary for, or else the project's
    default_sprint. None if no sprint has started."""
    chosen = parse_id(value)
    for sprint in sprints:
        if sprint.id == chosen:
            return sprint
    latest = (
        team.summaries.filter(sprint__isnull=False)
        .select_related("sprint")
        .order_by("-sprint__start_date", "-sprint__end_date")
        .first()
    )
    if latest is not None:
        return latest.sprint
    return default_sprint(team.project, now)


def summary_covered_through(summary):
    """The last day a sprint summary covers: the day it was generated, or its sprint's last day
    if it was generated after the sprint ended."""
    generated_on = summary.generated_at.astimezone(ingest_services.REPORTING_TIME_ZONE).date()
    return summary.sprint.covered_through(generated_on)


def add_sprint_summaries(memberships, sprint):
    """Give each membership `sprint_summary`, its student's summary for the sprint or None,
    with `covered_through` set when it's partial. Return the earliest day any partial one covers
    through, for labelling the sprint in progress, or None if none is partial.

    Uses the `team_summaries` from team_memberships_with_summaries, so it runs no queries.
    """
    earliest = None
    for membership in memberships:
        membership.sprint_summary = None
        if sprint is None:
            continue
        for summary in membership.user.team_summaries:
            if summary.sprint_id == sprint.id:
                membership.sprint_summary = summary
                summary.covered_through = None
                if summary.is_partial:
                    summary.covered_through = summary_covered_through(summary)
                    if earliest is None or summary.covered_through < earliest:
                        earliest = summary.covered_through
    return earliest


def team_memberships_with_summaries(team, sprint=None):
    """The team's memberships, each user with this team's `team_summaries`, newest first,
    `team_flags` about them, their `team_disputes`, `team_contributions` and the
    `team_corroborations` they gave. Each membership has `confirmations_of_their_work`, how
    many times teammates confirmed the student's contributions on this team.

    With a `sprint`, `team_flags` and `team_disputes` are only that sprint's."""
    team_flags = flags_in_display_order().filter(team=team)
    team_disputes = FlagDispute.objects.filter(team=team)
    if sprint is not None:
        team_flags = team_flags.filter(sprint=sprint)
        team_disputes = team_disputes.filter(sprint=sprint)
    summaries = (
        Summary.objects.filter(team=team)
        .annotate(claim_count=Count("claims", filter=Q(claims__status=Claim.Status.VERIFIED)))
        .select_related("sprint")
        .order_by(*SUMMARY_ORDER)
        .prefetch_related("validation_runs")
    )
    return (
        team.memberships.select_related("user")
        .annotate(
            confirmations_of_their_work=Count(
                "user__contributions__corroborations",
                filter=Q(user__contributions__team=team),
            )
        )
        .order_by("user__last_name", "user__first_name", "user__github_login")
        .prefetch_related(
            Prefetch("user__summaries", queryset=summaries, to_attr="team_summaries"),
            Prefetch(
                "user__flags",
                queryset=team_flags.select_related("sprint"),
                to_attr="team_flags",
            ),
            Prefetch(
                "user__flag_disputes",
                queryset=team_disputes.select_related("resolved_by", "sprint")
                .order_by("sprint__start_date", "id"),
                to_attr="team_disputes",
            ),
            Prefetch(
                "user__contributions",
                queryset=Contribution.objects.filter(team=team),
                to_attr="team_contributions",
            ),
            Prefetch(
                "user__corroborations",
                queryset=Corroboration.objects.filter(contribution__team=team),
                to_attr="team_corroborations",
            ),
        )
    )


def add_peer_rating_counts(team, memberships):
    """Give each membership `peer_rating_count`, the ratings its student gave or got from
    someone else on this team, and return how many ratings are between the team's students.
    These are what removing the student, or deleting the team, deletes."""
    student_ids = [membership.user_id for membership in memberships]
    ratings = PeerRating.objects.filter(
        evaluation__project=team.project_id, rater__in=student_ids, ratee__in=student_ids
    ).values_list("rater_id", "ratee_id")
    counts = Counter()
    for rater_id, ratee_id in ratings:
        counts[rater_id] += 1
        counts[ratee_id] += 1
    for membership in memberships:
        membership.peer_rating_count = counts[membership.user_id]
    return len(ratings)


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


def add_contribution_shares(team, memberships, sprint):
    """Give each of the team's memberships `contribution_share`, the student's whole-percent
    share of the team's requirement-tied work in the sprint, and return the memberships.

    It's the share the low-share flag rule uses, rounded the same way as flag reasons, but it
    includes a partial sprint, which the rule skips. So each membership also gets `chart_flag`,
    its worst flag if it's about this sprint, otherwise None. Every share is None until every
    student has a summary for the sprint, partial or not, and there's requirement-tied work to
    compare. Uses the `team_summaries` from team_memberships_with_summaries.

    Each membership also gets `pie_order`, its place in the chart and legend (largest share
    first), and `pie`, the slice to draw for a student with a share above 0%, otherwise None.
    A slice has `color`, `full` (True if it is the whole circle) and `path`. Slices are
    sized from the exact shares, so they always fill the circle.
    """
    memberships = list(memberships)
    shares = None
    if sprint is not None and every_student_has_summary(memberships, sprint):
        shares = flags.sprint_shares(team, sprint)
    shares = shares or {}
    for membership in memberships:
        share = shares.get(membership.user_id)
        about_this_sprint = [
            f
            for f in membership.user.team_flags
            if sprint is not None and f.sprint_id == sprint.id and not f.is_withdrawn
        ]
        membership.chart_flag = about_this_sprint[0] if about_this_sprint else None
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
    return memberships


def every_student_has_summary(memberships, sprint):
    """True if every membership's student has a summary for the sprint, partial or not, so
    their shares can be compared. Uses the `team_summaries` from team_memberships_with_summaries."""
    return bool(memberships) and all(
        any(summary.sprint_id == sprint.id for summary in membership.user.team_summaries)
        for membership in memberships
    )


def pie_legend(memberships):
    """The memberships that have a share, in chart order: largest share first."""
    return sorted(
        (m for m in memberships if m.pie_order is not None), key=lambda m: m.pie_order
    )


FLAG_RANKS = {Flag.Severity.FLAG: 0, Flag.Severity.WARNING: 1}
OPEN_DISPUTE_RANK, UNFLAGGED_RANK = 2, 3


def overview_sprint(sprints, chosen_ids, now=None):
    """The sprint a project shows on the overview, from its started `sprints`, newest first:
    the one whose ID is in `chosen_ids`, or else the latest that has ended, or else the
    running one. None if no sprint has started."""
    for sprint in sprints:
        if sprint.id in chosen_ids:
            return sprint
    today = reporting_today(now)
    ended = [sprint for sprint in sprints if sprint.end_date < today]
    if ended:
        return max(ended, key=lambda sprint: (sprint.end_date, sprint.start_date))
    return sprints[0] if sprints else None


def _add_sprint_status(team, sprint, today):
    """Give the team `sprint_status` (see team_sprint_status), `has_open_dispute`,
    `dispute_sprints` and `flag_rank` for the overview's sprint.

    An open dispute counts in any sprint, so a decision still waiting in an earlier sprint
    isn't hidden. `dispute_sprints` names their sprints, newest first, when any isn't the one
    shown; it's empty otherwise. Only current members' disputes count, since the team page
    shows only theirs."""
    members = {membership.user_id for membership in team.memberships.all()}
    disputed = {d.sprint for d in team.open_disputes if d.student_id in members}
    team.has_open_dispute = bool(disputed)
    team.dispute_sprints = []
    if disputed - {sprint}:
        team.dispute_sprints = sorted(
            disputed, key=lambda s: (s.start_date, s.end_date), reverse=True
        )
    team.sprint_status = team_sprint_status(team, sprint, today)
    team.flag_rank = FLAG_RANKS.get(
        team.sprint_status, OPEN_DISPUTE_RANK if team.has_open_dispute else UNFLAGGED_RANK
    )


def course_projects_with_teams(course, chosen_sprint_ids=(), now=None):
    """The course's projects, each with its requirements by number, its teams and their
    students, and the sprint it shows.

    Each project has `started_sprints` (for generating, oldest first), `menu_sprints` (newest
    first), `overview_sprint` (see overview_sprint; IDs in `chosen_sprint_ids` pick it),
    `other_sprint_ids` (the sprints other projects were explicitly given in `chosen_sprint_ids`,
    so its menu keeps those choices without pinning the others' defaults) and `has_sprints`.

    Each project's `ranked_teams` has teams flagged in its overview sprint first, then warned
    teams, then teams with an open dispute in any sprint, then the rest, each group sorted by
    name. Each team's `flag_rank` is 0, 1, 2 or 3 for those groups. Withdrawn flags don't count.
    Each team also gets `sprint_status`, `has_open_dispute` and `dispute_sprints` (see
    _add_sprint_status).
    """
    chosen_sprint_ids = set(chosen_sprint_ids)
    today = reporting_today(now)
    memberships = TeamMembership.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__github_login"
    )
    open_disputes = FlagDispute.objects.filter(status=FlagDispute.Status.OPEN).select_related(
        "sprint"
    )
    teams = (
        Team.objects.annotate(latest_week=Max("summaries__week_start"))
        .order_by("name")
        .prefetch_related(
            Prefetch("memberships", queryset=memberships),
            "repos",
            "summaries__validation_runs",
            "summaries__sprint",
            ACTIVE_FLAGS_PREFETCH,
            Prefetch("flag_disputes", queryset=open_disputes, to_attr="open_disputes"),
        )
    )
    projects = list(
        course.projects.order_by("id").prefetch_related(
            "sprints",
            Prefetch("teams", queryset=teams),
            Prefetch("requirements", queryset=Requirement.objects.order_by("number")),
        )
    )
    runs = active_generation_runs(course=course)
    for project in projects:
        project.started_sprints = started_sprints(project, now)
        project.menu_sprints = project.started_sprints[::-1]
        for sprint in project.started_sprints:
            sprint.label = sprint_label(sprint)
            sprint.is_running = sprint.end_date >= today
        project.has_sprints = bool(project.sprints.all())
        project.default_sprint = default_sprint(project, now)
        project.overview_sprint = overview_sprint(project.menu_sprints, chosen_sprint_ids, now)
        for team in project.teams.all():
            _add_summary_status(team)
            team.active_run = runs.get(team.id)
            _add_sprint_status(team, project.overview_sprint, today)
        # Sorting is stable, so each rank stays in name order.
        project.ranked_teams = sorted(project.teams.all(), key=lambda team: team.flag_rank)
    chosen = [
        project.overview_sprint.id
        for project in projects
        if project.overview_sprint is not None and project.overview_sprint.id in chosen_sprint_ids
    ]
    for project in projects:
        own = project.overview_sprint.id if project.overview_sprint else None
        project.other_sprint_ids = [sprint_id for sprint_id in chosen if sprint_id != own]
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
    team.latest_sprint, team.latest_sprint_note = None, ""
    sprint_summaries = [summary for summary in summaries if summary.sprint_id is not None]
    if sprint_summaries:
        newest = max(sprint_summaries, key=lambda summary: summary.sprint.start_date)
        team.latest_sprint = newest.sprint
        partial = [s for s in sprint_summaries if s.sprint_id == newest.sprint_id and s.is_partial]
        if partial:
            team.latest_sprint_note = "in progress" if partial[0].week_is_running else "partial"


class GenerationError(Exception):
    """A summary couldn't be generated. The message is shown to the user as is."""


def reporting_today(now=None):
    """Today's date in the reporting time zone."""
    return (now or timezone.now()).astimezone(ingest_services.REPORTING_TIME_ZONE).date()


def started_sprints(project, now=None):
    """The project's sprints that have started, in date order. A sprint that starts today
    has started. Uses prefetched sprints when there are some."""
    today = reporting_today(now)
    started = [sprint for sprint in project.sprints.all() if sprint.start_date <= today]
    return sorted(started, key=lambda sprint: (sprint.start_date, sprint.end_date))


def default_sprint(project, now=None):
    """The sprint to generate by default: the one covering today, or on a shared day the one
    that just started, or else the most recently ended. None if no sprint has started."""
    today = reporting_today(now)
    started = started_sprints(project, now)
    running = [sprint for sprint in started if sprint.end_date >= today]
    if running:
        return max(running, key=lambda sprint: (sprint.start_date, sprint.end_date))
    return max(started, key=lambda sprint: (sprint.end_date, sprint.start_date), default=None)


def parse_sprint(team, value, now=None):
    """The started sprint of this team's project with this ID.

    Raises GenerationError, with a message for the user, for anything else.
    """
    sprint_id = parse_id(value)
    if sprint_id is None:
        raise GenerationError("Pick a sprint to generate.")
    sprint = Sprint.objects.filter(pk=sprint_id, project_id=team.project_id).first()
    if sprint is None:
        raise GenerationError("That sprint isn't in this team's project. Pick one from the list.")
    if sprint.start_date > reporting_today(now):
        raise GenerationError(
            f"{sprint.name} hasn't started yet. Pick a sprint that has started."
        )
    return sprint


def student_name(student):
    return student.display_name


@dataclass(frozen=True)
class SummaryFailure:
    student: User
    reason: str  # Shown to staff, so it never holds student work.


@dataclass(frozen=True)
class TeamGeneration:
    """How one run of writing a team's summaries for a sprint went."""

    team: Team
    sprint: Sprint
    attempted: int
    failures: list[SummaryFailure]
    covered_through: date  # The last day its summaries cover.

    @property
    def succeeded(self):
        return not self.failures

    @property
    def message(self):
        period = sprint_label(self.sprint)
        if not self.attempted:
            return (
                f"Nobody left to retry on {self.team.name} for {period}: the students whose "
                "summaries failed have left the team."
            )
        if self.succeeded:
            students = "1 student" if self.attempted == 1 else f"{self.attempted} students"
            message = f"Generated summaries for {students} on {self.team.name} for {period}."
            if self.covered_through < self.sprint.end_date:
                message += (
                    " This sprint is still in progress, so these summaries are partial: they cover "
                    f"activity up to {short_date(self.covered_through)}. Generate the sprint again "
                    "after it ends to count it toward flags."
                )
            return message
        done = self.attempted - len(self.failures)
        failed = "; ".join(
            f"{student_name(failure.student)} ({failure.reason})" for failure in self.failures
        )
        return (
            f"Generated {done} of {self.attempted} summaries for {self.team.name} for {period}. "
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


def _ingest_team_range(team, first_day, last_day):
    """Ingest each of the team's repos for first_day through last_day, or raise GenerationError
    naming the repo. It's all-or-nothing, so a failure on one repo leaves the others as they were."""
    with transaction.atomic():
        for repo in team_repos(team):
            try:
                ingest_services.ingest(repo, first_day, last_day)
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


def _record_progress(team, student, failure):
    """Count a student as done on the team's run, naming them if their summary failed."""
    try:
        with transaction.atomic():
            run = GenerationRun.objects.select_for_update().filter(team=team).first()
            if run is None:
                return
            run.done_count += 1
            if failure is not None:
                run.failures = [*run.failures, {"name": student_name(student), "reason": failure.reason}]
            run.save(update_fields=["done_count", "failures"])
    except OperationalError:
        # Progress is only for show, so a busy database mustn't fail the run.
        pass


def active_generation_runs(team_ids=None, course=None):
    """The runs going right now, by team ID, for the progress display."""
    runs = GenerationRun.objects.filter(expires_at__gt=timezone.now())
    if course is not None:
        runs = runs.filter(team__project__course=course)
    if team_ids is not None:
        runs = runs.filter(team_id__in=team_ids)
    return {run.team_id: run for run in runs}


def _write_sprint_summaries(team, students, sprint):
    """Write each student's summary of the sprint, carrying on past a failure, then recompute
    flags only if none failed, as for a week. compute_flags only raises flags for a sprint that
    has ended with every student's summary complete, so a running sprint raises none (US-41).
    """
    failures = []
    for student in students:
        try:
            summaries_services.generate_sprint_summary(student, team, sprint)
        except LLMError as error:
            failures.append(SummaryFailure(student, str(error)))
        except Exception:
            failures.append(SummaryFailure(student, "an unexpected error"))
        _record_progress(team, student, failures[-1] if failures and failures[-1].student == student else None)
    if not failures:
        flags.compute_flags(team)
    covered = sprint.covered_through(reporting_today())
    return TeamGeneration(team, sprint, len(students), failures, covered)


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
                team=team, expires_at=now + timedelta(minutes=limit), student_count=student_count
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


def generate_team_sprint_summaries(team, sprint):
    """Ingest the team's repos once for the sprint, from its first day through today (or its
    last day, once it has ended), then write a summary for each student on the team.

    Raises GenerationError, with nothing run, for a team with no students or no repo, and for
    a failed ingest, with no LLM calls made. Otherwise returns a TeamGeneration listing any
    students whose summaries failed. They keep any earlier summary for the sprint.
    """
    students = _team_students(team)
    if not students:
        raise GenerationError("This team has no students yet, so nothing was generated.")
    if not team.repos.exists():
        raise GenerationError("This team has no repo yet, so nothing was generated.")
    with _team_run(team, len(students)):
        _ingest_team_range(team, sprint.start_date, sprint.covered_through(reporting_today()))
        return _write_sprint_summaries(team, students, sprint)


def retry_failed_summaries(team, session):
    """Write summaries again for only the students whose summaries failed, or return None
    if the session has nothing to retry for this team.

    Ingest doesn't run again, so every student's summary for the sprint uses the same GitHub
    data. Anyone who has left the team since is skipped. A retry remembered by an older
    version, with no sprint, has nothing to retry.
    """
    retry = session.get(RETRY_SESSION_KEY, {}).get(str(team.id))
    if retry is None or "sprint_id" not in retry:
        return None
    students = _team_students(team, only_ids=retry["student_ids"])
    sprint = Sprint.objects.filter(pk=retry["sprint_id"], project_id=team.project_id).first()
    if sprint is None:
        raise GenerationError("That sprint no longer exists, so nothing was retried.")
    with _team_run(team, len(students)):
        return _write_sprint_summaries(team, students, sprint)


def remember_retry(session, generation):
    """Keep this team's failed students in the session, replacing any it had, or clear them
    if none failed."""
    retries = session.get(RETRY_SESSION_KEY, {})
    key = str(generation.team.id)
    if generation.failures:
        # Retried for the same sprint.
        retries[key] = {
            "sprint_id": generation.sprint.id,
            "student_ids": [failure.student.id for failure in generation.failures],
        }
    else:
        retries.pop(key, None)
    session[RETRY_SESSION_KEY] = retries


def team_ids_to_retry(session):
    """The IDs of the teams with failed summaries to retry in this session."""
    return {int(team_id) for team_id in session.get(RETRY_SESSION_KEY, {})}
