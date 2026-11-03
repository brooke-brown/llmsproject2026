"""The prompt sent to the LLM to write one student's weekly summary."""

from zoneinfo import ZoneInfo

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")

# Caps keep the prompt a sensible size. A commit's title is its whole message and a body
# can be pasted output, so both are cut. Diffs are what let the LLM judge what the work
# accomplished, so they're included, but only the start of a long one.
TITLE_CAP = 1000
BODY_CAP = 2000
REQUIREMENT_CAP = 1000
DIFF_CAP = 4000

# A week with many artifacts shrinks each diff's cap so the prompt stays near this many
# characters (about 50,000 tokens), but never below MIN_DIFF_CAP, so every diff keeps its
# start. Titles and bodies are capped but never shrunk, so an extreme week can go a little
# over: the budget bounds the diffs, which are the bulk of a prompt, not every artifact.
PROMPT_BUDGET = 200_000
MIN_DIFF_CAP = 200

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
- Set requirement_ids to the numbers of the requirements this work advances, using only the \
numbers in the project's requirement list. Use an empty list if it advances none.
- Write each claim as one plain sentence. Group related artifacts into one claim and cite \
all of them.
"""


def build_prompt(artifacts, requirements=()) -> str:
    """The project's requirements, then the student's artifacts for the week, oldest first.

    `requirements` are Requirement rows in number order. They come first so the LLM knows
    what it can tie claims to.
    """
    artifacts = list(artifacts)
    intro = _requirements_section(requirements) + "This student's artifacts for the week:\n\n"
    heads = [_head(artifact) for artifact in artifacts]
    diff_cap = _diff_cap(
        room=PROMPT_BUDGET - len(intro) - sum(len(head) + 2 for head in heads),
        diffs=sum(1 for artifact in artifacts if artifact.diff),
    )
    blocks = [_artifact_block(artifact, head, diff_cap) for artifact, head in zip(artifacts, heads)]
    return intro + "\n\n".join(blocks)


def build_retry_prompt(artifacts, failing, requirements=()) -> str:
    """Ask for corrected versions of the claims that failed validation.

    `failing` is a list of (claim, errors) pairs. The artifact list goes back too, so the
    LLM still has the evidence in front of it.
    """
    blocks = [_failing_claim_block(number, claim, errors) for number, (claim, errors) in enumerate(failing, start=1)]
    return (
        build_prompt(artifacts, requirements)
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


def _requirements_section(requirements) -> str:
    requirements = list(requirements)
    if not requirements:
        return "This project has no requirements, so leave requirement_ids empty.\n\n"
    lines = ["The project's requirements:"]
    for requirement in requirements:
        capped = _capped(f"{requirement.number}.", requirement.text, REQUIREMENT_CAP, label="requirement")
        lines.extend(capped)
    return "\n".join(lines) + "\n\n"


def _diff_cap(room: int, diffs: int) -> int:
    """How many characters of each diff fit in the room left once heads are written."""
    if not diffs:
        return DIFF_CAP
    per_diff = room // diffs - _DIFF_OVERHEAD
    return max(MIN_DIFF_CAP, min(DIFF_CAP, per_diff))


# The "diff:" line and the truncation marker around each diff, and a little slack.
_DIFF_OVERHEAD = 40


def _head(artifact) -> str:
    """Everything about an artifact except its diff, with a long title or body cut."""
    when = artifact.timestamp.astimezone(REPORTING_TIME_ZONE).date().isoformat()
    lines = [
        f"artifact ID: {artifact.artifact_id}",
        f"type: {artifact.type}",
        f"date: {when}",
    ]
    if artifact.review_state:
        lines.append(f"review state: {artifact.review_state}")
    if artifact.title:
        lines.extend(_capped("title", artifact.title, TITLE_CAP))
    if artifact.body:
        lines.extend(_capped("body", artifact.body, BODY_CAP))
    return "\n".join(lines)


def _capped(prefix: str, text: str, cap: int, label: str | None = None) -> list[str]:
    """`prefix` and the text, cut to `cap` characters and marked if it was too long.

    A prefix like "title" is written "title: ..." and names its own marker. One that ends in
    a period, like "3.", is written "3. ..." and needs `label` for its marker.
    """
    separator = " " if prefix.endswith(".") else ": "
    marker = f"[{label or prefix} truncated]"
    if len(text) <= cap:
        return [f"{prefix}{separator}{text}"]
    return [f"{prefix}{separator}{text[:cap]}", marker]


def _artifact_block(artifact, head: str, diff_cap: int) -> str:
    if not artifact.diff:
        return head
    lines = [head, "diff:", artifact.diff[:diff_cap]]
    if len(artifact.diff) > diff_cap or artifact.diff_truncated:
        lines.append("[diff truncated]")
    return "\n".join(lines)
