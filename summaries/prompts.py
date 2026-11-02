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
