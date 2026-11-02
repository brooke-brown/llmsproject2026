"""Tests for T-8 (#35): the LLM client with structured output.

The client is imported inside each test rather than at the top of the file, so each
criterion passes or fails on its own instead of the whole module failing to import.

Nothing here makes a network call. Tests hand the function a fake client, and every
test also replaces anthropic.Anthropic with something that fails the test if the code
under test tries to build a real client.
"""

import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from django.test import SimpleTestCase
from pydantic import BaseModel

from summaries.schemas import Claim

ROOT = Path(__file__).resolve().parents[2]

PROMPT = "Summarize this week's activity: pr:12 added the login form."
SYSTEM = "You write evidence-linked summaries. Describe observable activity only."
# Unique strings, so a test can tell whether student work leaked into an error message.
PRIVATE_PROMPT = "PRIVATE-PROMPT-TEXT-91ac"
PRIVATE_REPLY = "PRIVATE-REPLY-TEXT-77de"


def generate_claims(*args, **kwargs):
    from summaries.llm import generate_claims

    return generate_claims(*args, **kwargs)


def llm_error():
    from summaries.llm import LLMError

    return LLMError


def make_claim(text="Added the login form.", artifact_id="sample-org/app:pr:12"):
    return Claim(
        text=text,
        category="feature",
        artifact_ids=[artifact_id],
        requirement_ids=[],
        significance="minor",
    )


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    """Stands in for anthropic.Anthropic: records the call and returns what it's given."""

    def __init__(self, claims=None, stop_reason="end_turn", parsed=True, error=None):
        parsed_output = SimpleNamespace(claims=claims or []) if parsed else None
        response = SimpleNamespace(parsed_output=parsed_output, stop_reason=stop_reason)
        self.messages = FakeMessages(response=response, error=error)

    @property
    def call(self):
        self_calls = self.messages.calls
        assert len(self_calls) == 1, f"expected one API call, got {len(self_calls)}"
        return self_calls[0]


def api_request():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def api_status_error(error_class, status):
    response = httpx2.Response(status, request=api_request())
    return error_class("failed", response=response, body=None)


def schema_error():
    """A real pydantic ValidationError whose text includes the model's reply."""
    try:
        Claim.model_validate({"text": PRIVATE_REPLY, "category": "not-a-category"})
    except Exception as error:  # noqa: BLE001 - pydantic.ValidationError
        return error
    raise AssertionError("the invalid claim was accepted")


class LLMClientTestCase(SimpleTestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"LLM_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LLM_MODEL", None)

        # Any test that builds a real client has a bug: it would call the network.
        patcher = mock.patch(
            "anthropic.Anthropic", side_effect=AssertionError("built a real Anthropic client")
        )
        self.anthropic_class = patcher.start()
        self.addCleanup(patcher.stop)


class ReplyTests(LLMClientTestCase):
    def test_returns_the_claims_from_the_reply(self):
        claims = [make_claim("Added the login form."), make_claim("Fixed the logout bug.")]

        result = generate_claims(PROMPT, client=FakeClient(claims=claims))

        self.assertEqual(result, claims)
        self.assertTrue(all(isinstance(claim, Claim) for claim in result))

    def test_returns_an_empty_list_when_the_reply_has_no_claims(self):
        self.assertEqual(generate_claims(PROMPT, client=FakeClient(claims=[])), [])


class RequestTests(LLMClientTestCase):
    def test_sends_the_prompt_as_the_user_message(self):
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertEqual(client.call["messages"], [{"role": "user", "content": PROMPT}])

    def test_sends_the_system_prompt_when_one_is_given(self):
        client = FakeClient()

        generate_claims(PROMPT, system=SYSTEM, client=client)

        self.assertEqual(client.call["system"], SYSTEM)

    def test_sends_no_system_prompt_when_none_is_given(self):
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertNotIn("system", client.call)

    def test_asks_for_the_claim_schema_as_structured_output(self):
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        output_format = client.call["output_format"]
        self.assertTrue(issubclass(output_format, BaseModel))
        self.assertIn("claims", output_format.model_fields)

    def test_the_structured_output_is_built_from_the_claim_schema(self):
        # The reply is checked against the T-6 Claim, so it can't drift from the contract.
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        schema = client.call["output_format"].model_json_schema()
        claim_properties = schema["$defs"]["Claim"]["properties"]
        self.assertEqual(
            set(claim_properties),
            {"text", "category", "artifact_ids", "requirement_ids", "significance"},
        )

    def test_sets_a_max_tokens_limit(self):
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertIsInstance(client.call["max_tokens"], int)
        self.assertGreater(client.call["max_tokens"], 0)

    def test_sends_no_sampling_parameters(self):
        # temperature, top_p and top_k are rejected by the current models.
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        for name in ["temperature", "top_p", "top_k"]:
            with self.subTest(parameter=name):
                self.assertNotIn(name, client.call)


class ModelTests(LLMClientTestCase):
    def test_uses_the_model_named_in_llm_model(self):
        os.environ["LLM_MODEL"] = "claude-sonnet-5-5"
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertEqual(client.call["model"], "claude-sonnet-5-5")

    def test_uses_a_default_model_when_llm_model_is_not_set(self):
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertEqual(client.call["model"], "claude-opus-5-5")

    def test_a_blank_llm_model_falls_back_to_the_default(self):
        os.environ["LLM_MODEL"] = "  "
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertEqual(client.call["model"], "claude-opus-5-5")


class ClientCreationTests(LLMClientTestCase):
    def test_builds_a_client_from_the_api_key_in_the_environment(self):
        fake = FakeClient(claims=[make_claim()])
        self.anthropic_class.side_effect = None
        self.anthropic_class.return_value = fake

        result = generate_claims(PROMPT)

        self.assertEqual(len(result), 1)
        self.assertEqual(self.anthropic_class.call_args.kwargs["api_key"], "test-key")

    def test_the_client_has_a_request_timeout(self):
        self.anthropic_class.side_effect = None
        self.anthropic_class.return_value = FakeClient()

        generate_claims(PROMPT)

        self.assertEqual(self.anthropic_class.call_args.kwargs["timeout"], 120)

    def test_the_client_does_not_retry_on_its_own(self):
        # The SDK's own retries would multiply the wait: 3 tries at 120s is 6 minutes for
        # one call. A failure raises LLMError straight away instead.
        self.anthropic_class.side_effect = None
        self.anthropic_class.return_value = FakeClient()

        generate_claims(PROMPT)

        self.assertEqual(self.anthropic_class.call_args.kwargs["max_retries"], 0)

    def test_the_reply_is_capped_at_8000_tokens(self):
        # A summary's claims are short. A smaller cap keeps the worst-case wait known.
        client = FakeClient()

        generate_claims(PROMPT, client=client)

        self.assertEqual(client.call["max_tokens"], 8000)

    def test_a_missing_api_key_raises_a_clear_error(self):
        del os.environ["LLM_API_KEY"]

        with self.assertRaisesMessage(llm_error(), "LLM_API_KEY"):
            generate_claims(PROMPT)

        self.anthropic_class.assert_not_called()

    def test_a_blank_api_key_raises_a_clear_error(self):
        for value in ["", "   "]:
            with self.subTest(value=value):
                os.environ["LLM_API_KEY"] = value

                with self.assertRaisesMessage(llm_error(), "LLM_API_KEY"):
                    generate_claims(PROMPT)

    def test_a_client_passed_in_is_used_without_needing_an_api_key(self):
        del os.environ["LLM_API_KEY"]

        result = generate_claims(PROMPT, client=FakeClient(claims=[make_claim()]))

        self.assertEqual(len(result), 1)
        self.anthropic_class.assert_not_called()


class FailedCallTests(LLMClientTestCase):
    def test_a_failed_api_call_raises_a_clear_error(self):
        cases = [
            (anthropic.APITimeoutError(request=api_request()), "timed out"),
            (anthropic.APIConnectionError(request=api_request()), "reach"),
            (api_status_error(anthropic.RateLimitError, 429), "rate limit"),
            (api_status_error(anthropic.AuthenticationError, 401), "LLM_API_KEY"),
            (api_status_error(anthropic.InternalServerError, 500), "HTTP 500"),
            (api_status_error(anthropic.BadRequestError, 400), "HTTP 400"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                client = FakeClient(error=error)

                with self.assertRaisesMessage(llm_error(), expected):
                    generate_claims(PROMPT, client=client)

    def test_a_refusal_raises_a_clear_error(self):
        client = FakeClient(stop_reason="refusal", parsed=False)

        with self.assertRaisesMessage(llm_error(), "declined"):
            generate_claims(PROMPT, client=client)

    def test_a_reply_cut_off_at_the_token_limit_raises_a_clear_error(self):
        client = FakeClient(stop_reason="max_tokens", parsed=False)

        with self.assertRaisesMessage(llm_error(), "cut off"):
            generate_claims(PROMPT, client=client)

    def test_a_reply_with_nothing_usable_raises_a_clear_error(self):
        client = FakeClient(stop_reason="end_turn", parsed=False)

        with self.assertRaisesMessage(llm_error(), "schema"):
            generate_claims(PROMPT, client=client)

    def test_a_reply_that_does_not_match_the_schema_raises_instead_of_returning_claims(self):
        client = FakeClient(error=schema_error())

        with self.assertRaisesMessage(llm_error(), "schema"):
            generate_claims(PROMPT, client=client)

    def test_an_error_from_a_failed_call_is_an_exception(self):
        self.assertTrue(issubclass(llm_error(), Exception))


class StudentWorkStaysPrivateTests(LLMClientTestCase):
    """The prompt and reply contain student work, so errors must never repeat them."""

    def failures(self):
        return {
            "schema mismatch": FakeClient(error=schema_error()),
            "refusal": FakeClient(stop_reason="refusal", parsed=False),
            "truncated": FakeClient(stop_reason="max_tokens", parsed=False),
            "api error": FakeClient(error=api_status_error(anthropic.BadRequestError, 400)),
        }

    def test_error_messages_do_not_include_the_prompt_or_the_reply(self):
        for name, client in self.failures().items():
            with self.subTest(failure=name):
                with self.assertRaises(llm_error()) as caught:
                    generate_claims(PRIVATE_PROMPT, client=client)

                message = str(caught.exception)
                self.assertNotIn(PRIVATE_PROMPT, message)
                self.assertNotIn(PRIVATE_REPLY, message)

    def test_the_original_error_is_not_chained_onto_ours(self):
        # A chained validation error would print the reply in any traceback.
        with self.assertRaises(llm_error()) as caught:
            generate_claims(PROMPT, client=FakeClient(error=schema_error()))

        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)


class ConfigurationTests(SimpleTestCase):
    def test_anthropic_is_pinned_in_requirements(self):
        lines = (ROOT / "requirements.txt").read_text().splitlines()

        pinned = [line for line in lines if re.match(r"^anthropic\s*(==|>=|~=|<)", line, re.I)]

        self.assertTrue(pinned, "requirements.txt has no pinned anthropic line")

    def test_env_example_documents_the_api_key_and_model_with_placeholders(self):
        text = (ROOT / ".env.example").read_text()

        self.assertRegex(text, r"(?m)^LLM_API_KEY=")
        self.assertRegex(text, r"(?m)^LLM_MODEL=")

    def test_env_example_contains_no_real_key(self):
        text = (ROOT / ".env.example").read_text()

        self.assertNotRegex(text, r"sk-ant-[A-Za-z0-9_-]{10,}")
