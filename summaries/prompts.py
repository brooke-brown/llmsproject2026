"""The prompt sent to the LLM to write one student's weekly summary."""

from zoneinfo import ZoneInfo

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")

# A cap per artifact keeps the prompt small. Diffs are what let the LLM judge what the
# work accomplished, so they're included, but only the start of a long one.
DIFF_CAP = 4000

SYSTEM_PROMPT = """\
You write one student's weekly contribution summary for an instructor, as a list of claims.

Rules:
- Describe observable activity only: what the student did, as shown in the artifacts. \
Make no judgments of character, effort or ability.
- Every claim cites at least one artifact. Cite artifacts by their artifact ID, copied \
exactly as listed. Never cite an ID that isn't in the list.
- Give each claim a category: feature, bug fix, tests, refactor or review.
- Set each claim's significance (minor, moderate or major) by what the work accomplishes, \
not by how many lines changed. A small fix that unblocks the team can be major, and a \
large reformatting is minor.
- Always set requirement_ids to an empty list.
- Write each claim as one plain sentence. Group related artifacts into one claim and cite \
all of them.
"""


def build_prompt(artifacts) -> str:
    """List the student's artifacts for the week, oldest first, one block each."""
    blocks = [_artifact_block(artifact) for artifact in artifacts]
    return "This student's artifacts for the week:\n\n" + "\n\n".join(blocks)


def build_retry_prompt(artifacts, failing) -> str:
    """Ask for corrected versions of the claims that failed validation.

    `failing` is a list of (claim, errors) pairs. The artifact list goes back too, so the
    LLM still has the evidence in front of it.
    """
    blocks = [_failing_claim_block(number, claim, errors) for number, (claim, errors) in enumerate(failing, start=1)]
    return (
        build_prompt(artifacts)
        + "\n\nThese claims from your earlier answer failed validation:\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn exactly one corrected claim for each claim above, in the same order. "
        "Fix the citations or the wording. If no listed artifact supports a claim, replace it "
        "with a claim that is supported."
    )


def _failing_claim_block(number, claim, errors) -> str:
    lines = [
        f"Claim {number}:",
        f"text: {claim.text}",
        f"category: {claim.category}",
        f"significance: {claim.significance}",
        f"artifact IDs: {', '.join(claim.artifact_ids)}",
        "errors:",
    ]
    lines.extend(f"- {error.check}: {error.message}" for error in errors)
    return "\n".join(lines)


def _artifact_block(artifact) -> str:
    when = artifact.timestamp.astimezone(REPORTING_TIME_ZONE).date().isoformat()
    lines = [
        f"artifact ID: {artifact.artifact_id}",
        f"type: {artifact.type}",
        f"date: {when}",
    ]
    if artifact.title:
        lines.append(f"title: {artifact.title}")
    if artifact.body:
        lines.append(f"body: {artifact.body}")
    if artifact.diff:
        lines.append("diff:")
        lines.append(artifact.diff[:DIFF_CAP])
        if len(artifact.diff) > DIFF_CAP or artifact.diff_truncated:
            lines.append("[diff truncated]")
    return "\n".join(lines)
