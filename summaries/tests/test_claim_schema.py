"""Tests for T-6 (#33): the summaries app and the claim schema.

The schema is imported inside each test rather than at the top of the file, so each
criterion passes or fails on its own instead of the whole module failing to import.

The schema checks a claim's shape only. Whether a cited artifact or requirement
actually exists is the validator's job (US-11, US-14), so nothing here looks at the
database.
"""

import json
import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "requirements.txt"

CATEGORIES = ["feature", "bug fix", "tests", "refactor", "review"]
SIGNIFICANCES = ["minor", "moderate", "major"]


def claim_class():
    from summaries.schemas import Claim

    return Claim


def validation_error():
    from pydantic import ValidationError

    return ValidationError


def valid_claim(**overrides):
    """A claim that satisfies the schema, with any fields replaced by the caller."""
    claim = {
        "text": "Added the login form and wired it to the session endpoint.",
        "category": "feature",
        "artifact_ids": ["sample-org/app:pr:12"],
        "requirement_ids": [3],
        "significance": "moderate",
    }
    claim.update(overrides)
    return claim


class SummariesAppTests(SimpleTestCase):
    def test_app_is_installed(self):
        self.assertTrue(apps.is_installed("summaries"))


class ValidClaimTests(SimpleTestCase):
    def test_valid_claim_is_accepted_with_every_field_kept(self):
        claim = claim_class()(**valid_claim())

        self.assertEqual(claim.text, "Added the login form and wired it to the session endpoint.")
        self.assertEqual(claim.category, "feature")
        self.assertEqual(claim.artifact_ids, ["sample-org/app:pr:12"])
        self.assertEqual(claim.requirement_ids, [3])
        self.assertEqual(claim.significance, "moderate")

    def test_claim_in_the_shape_from_claude_md_parses_from_json(self):
        # The exact shape the LLM is asked to return, as it arrives: a JSON string.
        reply = json.dumps(valid_claim(requirement_ids=[1, 4], artifact_ids=["a/b:commit:abc123"]))

        claim = claim_class().model_validate_json(reply)

        self.assertEqual(claim.requirement_ids, [1, 4])
        self.assertEqual(claim.artifact_ids, ["a/b:commit:abc123"])

    def test_a_claim_can_cite_several_artifacts(self):
        claim = claim_class()(
            **valid_claim(artifact_ids=["sample-org/app:pr:12", "sample-org/app:commit:abc123"])
        )

        self.assertEqual(len(claim.artifact_ids), 2)


class CategoryTests(SimpleTestCase):
    def test_every_allowed_category_is_accepted(self):
        for category in CATEGORIES:
            with self.subTest(category=category):
                claim = claim_class()(**valid_claim(category=category))
                self.assertEqual(claim.category, category)

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(validation_error()):
            claim_class()(**valid_claim(category="documentation"))

    def test_category_is_required(self):
        data = valid_claim()
        del data["category"]

        with self.assertRaises(validation_error()):
            claim_class()(**data)


class SignificanceTests(SimpleTestCase):
    def test_every_allowed_significance_is_accepted(self):
        for significance in SIGNIFICANCES:
            with self.subTest(significance=significance):
                claim = claim_class()(**valid_claim(significance=significance))
                self.assertEqual(claim.significance, significance)

    def test_unknown_significance_is_rejected(self):
        with self.assertRaises(validation_error()):
            claim_class()(**valid_claim(significance="critical"))

    def test_significance_is_required(self):
        data = valid_claim()
        del data["significance"]

        with self.assertRaises(validation_error()):
            claim_class()(**data)


class TextTests(SimpleTestCase):
    def test_empty_text_is_rejected(self):
        with self.assertRaises(validation_error()):
            claim_class()(**valid_claim(text=""))

    def test_text_is_required(self):
        data = valid_claim()
        del data["text"]

        with self.assertRaises(validation_error()):
            claim_class()(**data)


class ArtifactIdsTests(SimpleTestCase):
    def test_empty_artifact_ids_is_rejected(self):
        # Every claim must cite at least one artifact.
        with self.assertRaises(validation_error()):
            claim_class()(**valid_claim(artifact_ids=[]))

    def test_artifact_ids_is_required(self):
        data = valid_claim()
        del data["artifact_ids"]

        with self.assertRaises(validation_error()):
            claim_class()(**data)


class RequirementIdsTests(SimpleTestCase):
    def test_empty_requirement_ids_is_accepted(self):
        # An empty list means the claim isn't tied to a requirement.
        claim = claim_class()(**valid_claim(requirement_ids=[]))

        self.assertEqual(claim.requirement_ids, [])

    def test_requirement_ids_must_be_integers(self):
        with self.assertRaises(validation_error()):
            claim_class()(**valid_claim(requirement_ids=["not-a-number"]))


class DependencyTests(SimpleTestCase):
    def test_pydantic_is_pinned_in_requirements(self):
        lines = REQUIREMENTS_PATH.read_text().splitlines()

        # "Pinned" in the way the file already pins Django: a version specifier.
        pinned = [line for line in lines if re.match(r"^pydantic\s*(==|>=|~=|<)", line, re.I)]

        self.assertTrue(pinned, "requirements.txt has no pinned pydantic line")
