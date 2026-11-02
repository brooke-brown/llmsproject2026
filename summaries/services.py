from dataclasses import dataclass
from datetime import date

from django.db import transaction

from core.models import Team, TeamMembership, User
from ingest.models import Artifact
from ingest.services import student_week_artifacts
from summaries import schemas
from summaries.llm import LLMError, generate_claims
from summaries.models import Claim, Summary, ValidationRun
from summaries.prompts import SYSTEM_PROMPT, build_prompt, build_retry_prompt
from summaries.validator import ClaimResult, validate_claims

MONDAY = 0  # date.weekday() counts from Monday.
MAX_RETRIES = 2  # So at most 3 attempts in all.


@dataclass
class ValidatedClaims:
    """What the retry loop produced, and what T-9 will log about it."""

    claims: list[schemas.Claim]
    results: list[ClaimResult]  # One per claim, in the same order.
    retries: int  # How many retries were used, including ones that failed.
    first_pass: int  # How many claims were valid on the first attempt.


def generate_validated_claims(user: User, team: Team, week_start: date, artifacts) -> ValidatedClaims:
    """Ask the LLM for claims, then send only the failing ones back, up to MAX_RETRIES times.

    Claims that pass are never changed. A retry that raises LLMError, or that comes back
    with a different number of claims than were sent, counts as a failed attempt and the
    claims are left as they were. Only the first attempt can raise.
    """
    claims = list(generate_claims(build_prompt(artifacts), system=SYSTEM_PROMPT))
    results = validate_claims(claims, user=user, team=team, week_start=week_start)
    first_pass = sum(result.ok for result in results)

    retries = 0
    while retries < MAX_RETRIES:
        failing = [index for index, result in enumerate(results) if not result.ok]
        if not failing:
            break
        retries += 1
        fixes = _retry(artifacts, [(claims[index], results[index].errors) for index in failing])
        if fixes is None:
            continue
        for index, fixed in zip(failing, fixes):
            claims[index] = fixed
        results = validate_claims(claims, user=user, team=team, week_start=week_start)

    return ValidatedClaims(claims, results, retries, first_pass)


def _retry(artifacts, failing):
    """One corrected claim per failing claim, or None if this attempt didn't work."""
    try:
        fixes = generate_claims(build_retry_prompt(artifacts, failing), system=SYSTEM_PROMPT)
    except LLMError:
        return None
    # Replies are matched to failures by position, so a wrong count can't be trusted.
    return fixes if len(fixes) == len(failing) else None


def generate_summary(user: User, team: Team, week_start: date) -> Summary:
    """Write and save one student's summary for the week starting on `week_start`.

    Every claim the LLM writes is checked by the validator. A claim that fails is sent back
    to the LLM, with its errors, for up to MAX_RETRIES retries. A claim that passes is
    saved as verified. One that still fails is saved as couldn't verify, with the
    validator's last errors, and is never shown. Regenerating a week reuses its Summary and
    replaces its claims. Each call also saves one ValidationRun for the metrics.

    Raises ValueError for a week_start that isn't a Monday or a student who isn't on the
    team, and LLMError if the LLM can't give us claims. In each case nothing is saved, and
    an earlier summary for the week is left as it was.
    """
    if week_start.weekday() != MONDAY:
        raise ValueError(f"The reporting week must start on a Monday, not {week_start}.")
    if not TeamMembership.objects.filter(user=user, team=team).exists():
        raise ValueError(f"{user.github_login} isn't on this team.")

    artifacts = student_week_artifacts(user, team, week_start)
    claims, results, retries, first_pass = [], [], 0, 0
    # With nothing to describe there's nothing to ask the LLM, so no call is made.
    if artifacts:
        outcome = generate_validated_claims(user, team, week_start, artifacts)
        claims, results = outcome.claims, outcome.results
        retries, first_pass = outcome.retries, outcome.first_pass

    with transaction.atomic():
        summary, _ = Summary.objects.update_or_create(
            team=team,
            user=user,
            week_start=week_start,
            defaults={"artifact_count": len(artifacts)},
        )
        summary.claims.all().delete()
        for claim, result in zip(claims, results):
            row = Claim.objects.create(
                summary=summary,
                text=claim.text,
                category=claim.category,
                artifact_ids=list(claim.artifact_ids),
                requirement_ids=list(claim.requirement_ids),
                significance=claim.significance,
                status=Claim.Status.VERIFIED if result.ok else Claim.Status.COULDNT_VERIFY,
                validator_errors=[error.to_dict() for error in result.errors],
            )
            if result.ok:
                # Only a verified claim is shown, so only it needs links to GitHub.
                row.artifacts.set(
                    Artifact.objects.filter(repo__team=team, artifact_id__in=claim.artifact_ids)
                )
        # Saved with the summary, so the log never says something the database doesn't.
        # Counts only: it never holds student text or code.
        ValidationRun.objects.create(
            summary=summary,
            claims_total=len(claims),
            claims_first_pass=first_pass,
            claims_dropped=sum(not result.ok for result in results),
            retries=retries,
        )
    return summary
