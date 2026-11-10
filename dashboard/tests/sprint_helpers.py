"""Helpers for tests that need flags, which belong to a finished sprint (US-41).

A flag only labels a team once its sprint has ended and every student on the team has a
complete summary for it, so `flaggable_sprint` sets that up too.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.models import Sprint
from summaries.models import Claim, Summary, ValidationRun

SPRINT_START = date(2026, 10, 26)
SPRINT_END = date(2026, 11, 1)
# After SPRINT_END, so summaries generated then are complete.
GENERATED_AFTER = datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo("America/New_York"))


def sprint_for(project, name="Sprint 1", start=SPRINT_START, end=SPRINT_END):
    """The project's sprint with this name, made on first use."""
    sprint, _ = Sprint.objects.get_or_create(
        project=project, name=name, defaults={"start_date": start, "end_date": end}
    )
    return sprint


def complete_summaries(team, sprint, generated=GENERATED_AFTER):
    """Give every student on the team a summary for the sprint, generated at `generated`."""
    for membership in team.memberships.all():
        summary, _ = Summary.objects.get_or_create(team=team, user=membership.user, sprint=sprint)
        if not summary.validation_runs.exists():
            run = ValidationRun.objects.create(
                summary=summary, claims_total=0, claims_first_pass=0, claims_dropped=0, retries=0
            )
            ValidationRun.objects.filter(pk=run.pk).update(created_at=generated)


def flaggable_sprint(team, **sprint_fields):
    """A finished sprint of the team's project that every student has a complete summary for."""
    sprint = sprint_for(team.project, **sprint_fields)
    complete_summaries(team, sprint)
    return sprint


def give(
    student, team, *significances, sprint=None, tied=True, status=None,
    generated=GENERATED_AFTER,
):
    """Give the student a summary for the sprint (by default the project's Sprint 1), generated
    at `generated`, with one claim per significance. Calling it again adds claims to the same
    summary, as regenerating reuses the row."""
    sprint = sprint or sprint_for(team.project)
    summary, _ = Summary.objects.get_or_create(team=team, user=student, sprint=sprint)
    for significance in significances:
        Claim.objects.create(
            summary=summary,
            text=f"Sample {significance} work",
            category="feature",
            artifact_ids=[],
            requirement_ids=[1] if tied else [],
            significance=significance,
            status=status or Claim.Status.VERIFIED,
        )
    if not summary.validation_runs.exists():
        run = ValidationRun.objects.create(
            summary=summary, claims_total=0, claims_first_pass=0, claims_dropped=0, retries=0
        )
        ValidationRun.objects.filter(pk=run.pk).update(created_at=generated)
    return summary
