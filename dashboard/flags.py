from collections import defaultdict
from fractions import Fraction

from django.db import transaction
from django.utils import timezone

from core.models import Team
from ingest.services import REPORTING_TIME_ZONE
from summaries.models import Claim

from .models import Flag

SIGNIFICANCE_WEIGHTS = {"minor": 1, "moderate": 2, "major": 3}
# Fractions of an equal share. Below FLAG_BELOW is a flag; from there up to
# WARNING_BELOW is a warning. Exact fractions, so a share on a boundary isn't
# tipped either way by float rounding.
FLAG_BELOW = Fraction(1, 2)
WARNING_BELOW = Fraction(3, 4)


def _student_ids(team):
    """The IDs of the team's students. Uses prefetched memberships when there are some."""
    return {membership.user_id for membership in team.memberships.all()}


def _sprint_summaries(team):
    """The team's sprint summaries, each with its sprint and generation log. Uses prefetched
    summaries when there are some, as on the course overview."""
    if "summaries" in getattr(team, "_prefetched_objects_cache", {}):
        return [summary for summary in team.summaries.all() if summary.sprint_id is not None]
    return list(
        team.summaries.filter(sprint__isnull=False)
        .select_related("sprint")
        .prefetch_related("validation_runs")
    )


def _sprints_every_student_has(team, include_partial):
    """The sprints every student on the team has a summary for, oldest first. Unless
    `include_partial`, only complete summaries count, which means the sprint has ended too: a
    summary is partial until it's generated after its sprint's last day."""
    students = _student_ids(team)
    if not students:
        return []
    counted, sprints = defaultdict(set), {}
    # Checked in Python, because whether a summary is partial depends on its generation log.
    for summary in _sprint_summaries(team):
        if summary.user_id in students and (include_partial or not summary.is_partial):
            counted[summary.sprint_id].add(summary.user_id)
            sprints[summary.sprint_id] = summary.sprint
    complete = [sprints[sprint_id] for sprint_id, ids in counted.items() if ids >= students]
    return sorted(complete, key=lambda sprint: sprint.start_date)


def flaggable_sprints(team):
    """The team's sprints that can raise flags, oldest first: each has ended, and every student
    on the team has a complete summary for it."""
    today = timezone.now().astimezone(REPORTING_TIME_ZONE).date()
    return [
        sprint
        for sprint in _sprints_every_student_has(team, include_partial=False)
        # A complete summary implies this, but say it outright: a running sprint never flags.
        if sprint.end_date < today
    ]


def latest_flaggable_sprint(team):
    """The sprint whose flags label the team, or None if no sprint can raise flags yet."""
    sprints = flaggable_sprints(team)
    return sprints[-1] if sprints else None


def sprint_shares(team, sprint):
    """Each student's weighted share of the team's requirement-tied work in the sprint, as
    {user ID: Fraction}, or None if there's no such work to compare.

    A student's score is the sum of their verified, requirement-tied claims in their summary
    for the sprint, weighted by significance.
    """
    scores = {user_id: 0 for user_id in _student_ids(team)}
    claims = Claim.objects.filter(
        summary__team=team, summary__sprint=sprint, status=Claim.Status.VERIFIED
    ).values_list("summary__user_id", "significance", "requirement_ids")
    for user_id, significance, requirement_ids in claims:
        if requirement_ids and user_id in scores:
            scores[user_id] += SIGNIFICANCE_WEIGHTS[significance]
    total = sum(scores.values())
    if total == 0:
        return None
    return {user_id: Fraction(score, total) for user_id, score in scores.items()}


def low_requirement_share(team, sprint):
    """Flag students whose weighted share of the sprint's requirement-tied work is low."""
    students = [membership.user for membership in team.memberships.select_related("user")]
    shares = sprint_shares(team, sprint)
    if len(students) < 2 or shares is None:
        return []

    flags = []
    for student in students:
        share = shares[student.id]
        of_equal_share = share * len(students)
        if of_equal_share < FLAG_BELOW:
            severity = Flag.Severity.FLAG
        elif of_equal_share < WARNING_BELOW:
            severity = Flag.Severity.WARNING
        else:
            continue
        reason = (
            f"{student.display_name} did {round(share * 100)}% of the team's requirement-tied "
            f"work in {sprint.name} (an equal share is {round(100 / len(students))}%)."
        )
        flags.append(
            Flag(
                team=team,
                user=student,
                rule="low-requirement-share",
                reason=reason,
                severity=severity,
                sprint=sprint,
            )
        )
    return flags


# Each rule takes a team and one of its flaggable sprints, and returns unsaved Flags.
RULES = [low_requirement_share]


def compute_flags(team: Team) -> list[Flag]:
    """Run every rule for each of the team's flaggable sprints and replace its earlier flags
    with the new ones. A sprint whose summaries haven't changed gets the same flags back.

    If a rule fails, nothing is saved and the earlier flags are kept.
    """
    with transaction.atomic():
        flags = [
            flag
            for sprint in flaggable_sprints(team)
            for rule in RULES
            for flag in rule(team, sprint)
        ]
        team.flags.all().delete()
        return Flag.objects.bulk_create(flags)
