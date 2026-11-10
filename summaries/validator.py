"""The validator: checks every claim against the GitHub data stored for a team.

Plain Python with no LLM calls. For each artifact a claim cites it checks that the ID is
well formed, that the artifact is in one of the team's repos, that it falls inside the
reporting week, and that the student is its author or co-author (for a review, the
author is the reviewer). A claim fails if any citation fails, and every problem is
reported so the LLM can fix them all in one retry.

It also checks that every requirement number a claim cites exists in the team's project
(Requirement.number, not the database ID).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import Requirement
from ingest.models import ARTIFACT_ID_PATTERN, Artifact

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")
MONDAY = 0  # date.weekday() counts from Monday.

MALFORMED_ID = "malformed_id"
ARTIFACT_NOT_FOUND = "artifact_not_found"
OUTSIDE_WEEK = "outside_week"
NOT_CONTRIBUTOR = "not_contributor"
UNKNOWN_REQUIREMENT = "unknown_requirement"
NOT_IN_SOURCE = "not_in_source"

_ARTIFACT_ID_RE = re.compile(ARTIFACT_ID_PATTERN)


@dataclass(frozen=True)
class ClaimError:
    """One problem with one citation, in a form that can be stored and sent to the LLM."""

    claim_index: int
    artifact_id: str  # Empty for an error about a requirement, which has no artifact.
    check: str
    message: str
    requirement_id: int | None = None  # Set only for an unknown_requirement error.

    def to_dict(self):
        data = {
            "claim_index": self.claim_index,
            "artifact_id": self.artifact_id,
            "check": self.check,
            "message": self.message,
        }
        # Left out when unset, so an artifact error keeps exactly its old shape.
        if self.requirement_id is not None:
            data["requirement_id"] = self.requirement_id
        return data


@dataclass(frozen=True)
class ClaimResult:
    claim_index: int
    errors: list[ClaimError] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def validate_claims(claims, user, team, week_start: date) -> list[ClaimResult]:
    """Check each claim for `user` and `team` in the week starting on `week_start`.

    Returns one result per claim, in order. Raises ValueError if `week_start` isn't a
    Monday, because the week would otherwise quietly cover the wrong seven days.
    """
    if week_start.weekday() != MONDAY:
        raise ValueError(f"The reporting week must start on a Monday, not {week_start.isoformat()}.")
    week = _Week(week_start)
    requirements = Requirement.objects.filter(project=team.project_id)
    return _validate_all(claims, user, team, week, requirements)


def validate_sprint_claims(
    claims, user, team, sprint, last_day: date, *, first_day: date | None = None,
    allowed_artifact_ids=None,
) -> list[ClaimResult]:
    """Check each claim against a sprint: `first_day` (default `sprint.start_date`) through
    `last_day`, inclusive.

    `last_day` is the sprint's covered_through, so a running sprint stops at today. A chunk
    passes its own first and last day. It needn't start on a Monday, and requirement numbers
    are looked up in the sprint alone. If `allowed_artifact_ids` is given, a claim may cite
    only those artifacts, as a merged claim may cite only the ones it was merged from.
    """
    window = _Week(first_day or sprint.start_date, last_day)
    return _validate_all(
        claims, user, team, window, Requirement.objects.filter(sprint=sprint),
        allowed_artifact_ids,
    )


def _validate_all(claims, user, team, week, requirements, allowed_artifact_ids=None):
    login = user.github_login.lower()
    # Looked up once for the whole list.
    requirement_numbers = set(requirements.values_list("number", flat=True))
    return [
        ClaimResult(
            index,
            _validate_claim(
                index, claim, team, login, week, requirement_numbers, allowed_artifact_ids
            ),
        )
        for index, claim in enumerate(claims)
    ]


class _Week:
    """The first day at 00:00 to the day after the last at 00:00 in New York, so the last
    night is included. A reporting week is Monday-Sunday; a sprint's range is any days."""

    def __init__(self, first_day: date, last_day: date | None = None):
        self.first_day = first_day
        self.last_day = last_day or first_day + timedelta(days=6)
        self.start = datetime.combine(first_day, time.min, tzinfo=REPORTING_TIME_ZONE)
        self.end = datetime.combine(
            self.last_day + timedelta(days=1), time.min, tzinfo=REPORTING_TIME_ZONE
        )

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def _validate_claim(index, claim, team, login, week, requirement_numbers, allowed=None):
    errors = []
    for artifact_id in claim.artifact_ids:
        errors.extend(_validate_citation(index, artifact_id, team, login, week, allowed))
    # Each unknown number once, even if the claim repeats it.
    for number in dict.fromkeys(claim.requirement_ids):
        if number not in requirement_numbers:
            errors.append(
                ClaimError(
                    index,
                    "",
                    UNKNOWN_REQUIREMENT,
                    f"Requirement {number} doesn't exist in this project.",
                    requirement_id=number,
                )
            )
    return errors


def _validate_citation(index, artifact_id, team, login, week, allowed=None):
    def error(check, message):
        return ClaimError(index, artifact_id, check, message)

    if not _ARTIFACT_ID_RE.match(artifact_id):
        return [error(MALFORMED_ID, f"{artifact_id!r} isn't in the format owner/repo:type:id.")]

    # Looked up within the team: an artifact in another team's repo doesn't count.
    artifact = Artifact.objects.filter(repo__team=team, artifact_id=artifact_id).first()
    if artifact is None:
        return [error(ARTIFACT_NOT_FOUND, f"{artifact_id} isn't in any of this team's repos.")]

    errors = []
    if allowed is not None and artifact_id not in allowed:
        errors.append(
            error(
                NOT_IN_SOURCE,
                f"{artifact_id} isn't cited by any of the claims being merged, so it can't be cited here.",
            )
        )
    when = artifact.timestamp.astimezone(REPORTING_TIME_ZONE)
    if not week.contains(when):
        errors.append(
            error(
                OUTSIDE_WEEK,
                f"{artifact_id} is dated {when.date().isoformat()}, outside the reporting "
                f"week {week.first_day.isoformat()} to {week.last_day.isoformat()}.",
            )
        )
    credited = {artifact.author_login.lower(), *(c.lower() for c in artifact.co_author_logins)}
    if login not in credited:
        errors.append(
            error(NOT_CONTRIBUTOR, f"The student isn't the author or a co-author of {artifact_id}.")
        )
    return errors
