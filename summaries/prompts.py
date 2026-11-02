"""The prompt sent to the LLM to write one student's weekly summary."""

from zoneinfo import ZoneInfo

REPORTING_TIME_ZONE = ZoneInfo("America/New_York")

# Caps keep the prompt a sensible size. A commit's title is its whole message and a body
# can be pasted output, so both are cut. Diffs are what let the LLM judge what the work
# accomplished, so they're included, but only the start of a long one.
TITLE_CAP = 1000
BODY_CAP = 2000
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
- Always set requirement_ids to an empty list.
- Write each claim as one plain sentence. Group related artifacts into one claim and cite \
all of them.
"""


def build_prompt(artifacts) -> str:
    """List the student's artifacts for the week, oldest first, one block each."""
    artifacts = list(artifacts)
    intro = "This student's artifacts for the week:\n\n"
    heads = [_head(artifact) for artifact in artifacts]
    diff_cap = _diff_cap(
        room=PROMPT_BUDGET - len(intro) - sum(len(head) + 2 for head in heads),
        diffs=sum(1 for artifact in artifacts if artifact.diff),
    )
    blocks = [_artifact_block(artifact, head, diff_cap) for artifact, head in zip(artifacts, heads)]
    return intro + "\n\n".join(blocks)


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
    if artifact.title:
        lines.extend(_capped("title", artifact.title, TITLE_CAP))
    if artifact.body:
        lines.extend(_capped("body", artifact.body, BODY_CAP))
    return "\n".join(lines)


def _capped(label: str, text: str, cap: int) -> list[str]:
    if len(text) <= cap:
        return [f"{label}: {text}"]
    return [f"{label}: {text[:cap]}", f"[{label} truncated]"]


def _artifact_block(artifact, head: str, diff_cap: int) -> str:
    if not artifact.diff:
        return head
    lines = [head, "diff:", artifact.diff[:diff_cap]]
    if len(artifact.diff) > diff_cap or artifact.diff_truncated:
        lines.append("[diff truncated]")
    return "\n".join(lines)
