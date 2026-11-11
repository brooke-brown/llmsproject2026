"""Lets the tests that were written around a Monday-Sunday week run through the sprint code.

A week is just a sprint of seven days that starts on a Monday. The sample fixture has one for
Oct 26 to Nov 1, and any other week a test names gets one made on demand.
"""

from datetime import timedelta

from core.models import Sprint


def sprint_for_week(project, week_start):
    """The project's sprint that starts on `week_start`, made with a week's length if needed."""
    sprint, _ = Sprint.objects.get_or_create(
        project=project,
        start_date=week_start,
        defaults={
            "name": f"Week of {week_start:%b} {week_start.day}",
            "end_date": week_start + timedelta(days=6),
        },
    )
    return sprint
