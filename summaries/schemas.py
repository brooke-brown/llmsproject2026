"""The claim schema: the shape of one claim the LLM returns.

This is a shared contract with the rest of the team, so changing it needs team
agreement. It checks shape only. Whether a cited artifact or requirement really
exists is the validator's job.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str = Field(min_length=1)
    category: Literal["feature", "bug fix", "tests", "refactor", "review"]
    # Each is "owner/repo:type:id", such as "acme/app:pr:12". The format is checked
    # by the validator, not here.
    artifact_ids: list[str] = Field(min_length=1)
    # Requirement.number, not the database ID. Empty means the claim isn't tied to a
    # requirement.
    requirement_ids: list[int]
    # A judgment of what the work accomplishes, not of how many lines changed.
    significance: Literal["minor", "moderate", "major"]
