"""The LLM client: sends a prompt to Claude and returns the claims it writes.

The reply is requested as structured output and checked against the claim schema, so
a reply that doesn't fit raises LLMError instead of returning partial claims.

The prompt and the reply contain student work, so nothing here logs them and no error
message repeats them. Errors are raised with `from None` so a chained validation error
can't print the reply in a traceback.
"""

import os

import anthropic
from pydantic import BaseModel, ValidationError

from summaries.schemas import Claim

DEFAULT_MODEL = "claude-opus-5-5"
# A summary's claims are short, so the reply is capped well below the model's maximum.
# Together with the timeout and no automatic retries, that bounds how long one call can
# take: at most TIMEOUT_SECONDS. generate_summary may make up to 3 calls.
MAX_TOKENS = 8000
TIMEOUT_SECONDS = 120


class LLMError(Exception):
    """The LLM couldn't give us claims. The message says why, and holds no student work."""


class ClaimList(BaseModel):
    # Structured output needs an object at the top level, so the claims go inside one.
    claims: list[Claim]


def generate_claims(prompt: str, *, system: str = "", client=None) -> list[Claim]:
    """Send `prompt` to the LLM and return the claims it wrote.

    `client` is for tests. Without it a client is built from LLM_API_KEY, and the model
    comes from LLM_MODEL.
    """
    if client is None:
        client = _build_client()

    request = {
        "model": _model(),
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": ClaimList,
    }
    if system:
        request["system"] = system

    try:
        response = client.messages.parse(**request)
    except anthropic.APITimeoutError:  # Before APIConnectionError, which it extends.
        raise LLMError("The LLM request timed out.") from None
    except anthropic.APIConnectionError:
        raise LLMError("Couldn't reach the LLM.") from None
    except anthropic.RateLimitError:
        raise LLMError("The LLM rate limit was hit.") from None
    except anthropic.AuthenticationError:
        raise LLMError("The LLM rejected the API key. Check LLM_API_KEY.") from None
    except anthropic.APIStatusError as error:
        raise LLMError(f"The LLM returned an error (HTTP {error.status_code}).") from None
    except ValidationError:
        raise LLMError(_SCHEMA_MESSAGE) from None

    if response.stop_reason == "refusal":
        raise LLMError("The LLM declined to answer.")
    if response.stop_reason == "max_tokens":
        raise LLMError("The LLM's reply was cut off at the token limit.")
    if response.parsed_output is None:
        raise LLMError(_SCHEMA_MESSAGE)
    return list(response.parsed_output.claims)


_SCHEMA_MESSAGE = "The LLM's reply didn't match the claim schema."


def _build_client():
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise LLMError("LLM_API_KEY isn't set. Add it to the environment (see .env.example).")
    # No automatic retries: the SDK's would triple the wait. A failure raises LLMError.
    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)


def _model():
    return os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL
