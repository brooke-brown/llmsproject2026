from fractions import Fraction

from django.db import transaction
from django.db.models import Count

from core.models import Team
from summaries.models import Claim, Summary

from .models import Flag

SIGNIFICANCE_WEIGHTS = {"minor": 1, "moderate": 2, "major": 3}
# Fractions of an equal share. Below FLAG_BELOW is a flag; from there up to
# WARNING_BELOW is a warning. Exact fractions, so a share on a boundary isn't
# tipped either way by float rounding.
FLAG_BELOW = Fraction(1, 2)
WARNING_BELOW = Fraction(3, 4)


def low_requirement_share(team):
    """Flag students whose weighted share of requirement-tied work is low.

    Only the latest week in which every student on the team has a summary is checked,
    so a missing summary never raises a flag. A student's score is the sum of their
    verified, requirement-tied claims weighted by significance.
    """
    students = [membership.user for membership in team.memberships.select_related("user")]
    if len(students) < 2:
        return []
    week = (
        Summary.objects.filter(team=team, user__in=students)
        .values("week_start")
        .annotate(students=Count("user", distinct=True))
        .filter(students=len(students))
        .order_by("-week_start")
        .values_list("week_start", flat=True)
        .first()
    )
    if week is None:
        return []

    scores = {student.id: 0 for student in students}
    claims = Claim.objects.filter(
        summary__team=team, summary__week_start=week, status=Claim.Status.VERIFIED
    ).values_list("summary__user_id", "significance", "requirement_ids")
    for user_id, significance, requirement_ids in claims:
        if requirement_ids and user_id in scores:
            scores[user_id] += SIGNIFICANCE_WEIGHTS[significance]
    total = sum(scores.values())
    if total == 0:
        return []

    flags = []
    for student in students:
        share = Fraction(scores[student.id], total)
        of_equal_share = share * len(students)
        if of_equal_share < FLAG_BELOW:
            severity = Flag.Severity.FLAG
        elif of_equal_share < WARNING_BELOW:
            severity = Flag.Severity.WARNING
        else:
            continue
        name = student.get_full_name() or student.github_login
        reason = (
            f"{name} did {round(share * 100)}% of the team's requirement-tied work in the "
            f"week of {week:%b} {week.day} (an equal share is {round(100 / len(students))}%)."
        )
        flags.append(
            Flag(
                team=team,
                user=student,
                rule="low-requirement-share",
                reason=reason,
                severity=severity,
                week_start=week,
            )
        )
    return flags


# Each rule takes a team and returns unsaved Flags.
RULES = [low_requirement_share]


def compute_flags(team: Team) -> list[Flag]:
    """Run every rule for the team and replace its earlier flags with the new ones.

    If a rule fails, nothing is saved and the earlier flags are kept.
    """
    with transaction.atomic():
        flags = [flag for rule in RULES for flag in rule(team)]
        team.flags.all().delete()
        return Flag.objects.bulk_create(flags)
