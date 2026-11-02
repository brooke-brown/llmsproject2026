from datetime import date

from django.db import transaction

from core.models import Team, TeamMembership, User
from ingest.models import Artifact
from ingest.services import student_week_artifacts
from summaries.llm import generate_claims
from summaries.models import Claim, Summary
from summaries.prompts import SYSTEM_PROMPT, build_prompt
from summaries.validator import validate_claims

MONDAY = 0  # date.weekday() counts from Monday.


def generate_summary(user: User, team: Team, week_start: date) -> Summary:
    """Write and save one student's summary for the week starting on `week_start`.

    Every claim the LLM writes is checked by the validator. A claim that passes is saved
    as verified. One that fails is saved as couldn't verify, with the validator's errors,
    and is never shown. Regenerating a week reuses its Summary and replaces its claims.

    Raises ValueError for a week_start that isn't a Monday or a student who isn't on the
    team, and LLMError if the LLM can't give us claims. In each case nothing is saved, and
    an earlier summary for the week is left as it was.
    """
    if week_start.weekday() != MONDAY:
        raise ValueError(f"The reporting week must start on a Monday, not {week_start}.")
    if not TeamMembership.objects.filter(user=user, team=team).exists():
        raise ValueError(f"{user.github_login} isn't on this team.")

    artifacts = student_week_artifacts(user, team, week_start)
    # With nothing to describe there's nothing to ask the LLM, so no call is made.
    claims = generate_claims(build_prompt(artifacts), system=SYSTEM_PROMPT) if artifacts else []
    results = validate_claims(claims, user=user, team=team, week_start=week_start)

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
                # The validator can't check requirement numbers until US-14, and the flag
                # rules weight requirement-tied claims, so none are saved until it can.
                requirement_ids=[],
                significance=claim.significance,
                status=Claim.Status.VERIFIED if result.ok else Claim.Status.COULDNT_VERIFY,
                validator_errors=[error.to_dict() for error in result.errors],
            )
            if result.ok:
                # Only a verified claim is shown, so only it needs links to GitHub.
                row.artifacts.set(
                    Artifact.objects.filter(repo__team=team, artifact_id__in=claim.artifact_ids)
                )
    return summary
