"""`python manage.py demo_complete_week --team NAME`: shows a flag for an unfinished week.

Demo data only. Flags ignore a summary generated before its week ended, so this marks a
team's generated week as generated when the week ends, then recomputes the team's flags.
That makes its generation log false, so never run it on real data. The week keeps counting
if it's generated again, because a summary's latest logged run decides whether it's partial.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Team
from dashboard.flags import compute_flags
from dashboard.services import current_week_start
from summaries.models import Summary, ValidationRun


class Command(BaseCommand):
    help = "Demo data only: counts a team's unfinished week toward flags."

    def add_arguments(self, parser):
        parser.add_argument("--team", required=True, help="The team's name.")
        parser.add_argument(
            "--week",
            type=date.fromisoformat,
            help="The week's Monday as YYYY-MM-DD. Defaults to the current week.",
        )

    def handle(self, *args, team, week, **options):
        team = self._find_team(team)
        week = week or current_week_start()
        summaries = Summary.objects.filter(team=team, week_start=week)
        first_summary = summaries.first()
        if first_summary is None:
            raise CommandError(f"{team} has no summaries for the week of {week}. Generate first.")

        with transaction.atomic():
            # update() because created_at is auto_now_add, which save() would ignore.
            ValidationRun.objects.filter(summary__in=summaries).update(
                created_at=first_summary.week_ended_at
            )
            flags = compute_flags(team)

        self.stdout.write(f"Counted {summaries.count()} summaries for the week of {week}.")
        for flag in flags:
            self.stdout.write(f"{flag.get_severity_display()}: {flag.reason}")
        if not flags:
            self.stdout.write("No flags came out of this week's claims.")

    def _find_team(self, name):
        teams = list(Team.objects.filter(name=name)[:2])
        if not teams:
            raise CommandError(f"No team is named {name!r}.")
        if len(teams) > 1:
            raise CommandError(f"More than one team is named {name!r}.")
        return teams[0]
