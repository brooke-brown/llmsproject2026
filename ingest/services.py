from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import Team, User
from ingest.models import Artifact

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")


def student_week_artifacts(user: User, team: Team, week_start: date) -> list[Artifact]:
    """The artifacts in a team's repos that credit a student during a reporting week.

    A student is credited as an artifact's author, co-author or reviewer (a review's
    author_login is its reviewer). Logins are compared ignoring case. The week runs for
    seven days from week_start, in the reporting time zone.
    """
    # Built from dates, because a week with a clock change isn't exactly 168 hours long.
    start = datetime.combine(week_start, time.min, tzinfo=REPORTING_TIME_ZONE)
    end = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=REPORTING_TIME_ZONE)
    artifacts = Artifact.objects.filter(
        repo__team=team, timestamp__gte=start, timestamp__lt=end
    ).order_by("timestamp", "pk")

    # Compared in Python, because a database search of the co-author JSON matches
    # substrings: sample-student would also match sample-student-2.
    login = user.github_login.lower()
    return [
        artifact
        for artifact in artifacts
        if artifact.author_login.lower() == login
        or login in (co_author.lower() for co_author in artifact.co_author_logins)
    ]
