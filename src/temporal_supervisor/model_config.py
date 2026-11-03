"""The model the agents call, wrapped for the chaos panel's LLM failures.

The Google ADK plugin runs every model call in its invoke_model activity, which
creates the model through ADK's LLMRegistry. register_chaos_aware_gemini() makes
the registry return ChaosAwareGemini for Gemini model names, so anything raised
here is an activity failure and is retried by LLM_RETRY_POLICY.

Call register_chaos_aware_gemini() in the worker process only: it is never used
from workflow code.
"""

from datetime import timedelta

from google.adk.models import Gemini, LLMRegistry
from google.genai.errors import ClientError
from temporalio.exceptions import ApplicationError

from common.chaos_manager import ChaosFlag
from temporal_supervisor.chaos import inject_failure

# Stands in for the Retry-After delay of a real 429.
RATE_LIMIT_RETRY_AFTER = timedelta(seconds=15)


def rate_limited(message: str) -> ApplicationError:
    return ApplicationError(message, type="ModelRateLimited", next_retry_delay=RATE_LIMIT_RETRY_AFTER)


class ChaosAwareGemini(Gemini):
    async def generate_content_async(self, llm_request, stream: bool = False):
        await inject_failure(ChaosFlag.LLM_DOWN, ConnectionError("Gemini API unavailable"))
        await inject_failure(ChaosFlag.LLM_RATE_LIMITED, rate_limited("Gemini API rate limit exceeded (HTTP 429)"))
        try:
            async for response in super().generate_content_async(llm_request, stream):
                yield response
        except ClientError as e:
            # A real 429 (RESOURCE_EXHAUSTED): wait before retrying, like a Retry-After header
            if e.code == 429:
                raise rate_limited(f"Gemini API throttled the request: {e}") from e
            raise


def register_chaos_aware_gemini() -> None:
    LLMRegistry.register(ChaosAwareGemini)
    # resolve() caches model name lookups, so drop any made before registering
    LLMRegistry.resolve.cache_clear()
