"""A scripted stand-in for Gemini, so the real WealthManagementWorkflow can be tested
without an API key.

TemporalModel sends every model call through the ``invoke_model`` activity, which
GoogleAdkPlugin registers on the worker. ``scripted_plugin()`` builds the plugin with a
fake ``invoke_model`` instead: it plays back the responses queued with ``expect()``, in
order, and records each request so tests can inspect what the agents were told.
"""

from dataclasses import dataclass

import temporalio.google_adk._plugin as adk_plugin_module
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types
from temporalio import activity
from temporalio.google_adk import GoogleAdkPlugin


@dataclass
class ModelCall:
    agent: str
    system_instruction: str
    # The conversation the agent was given, one line per part
    contents: str = ""


calls: list[ModelCall] = []
_script: list[tuple[str, list[LlmResponse]]] = []


def reset() -> None:
    calls.clear()
    _script.clear()


def expect(agent: str, response: list[LlmResponse]) -> None:
    """Queue the response for the next model call, which must come from ``agent``."""
    _script.append((agent, response))


def unused() -> list[tuple[str, list[LlmResponse]]]:
    return list(_script)


def _contents_text(llm_request: LlmRequest) -> str:
    lines = []
    for content in llm_request.contents or []:
        for part in content.parts or []:
            if part.text:
                lines.append(f"{content.role}: {part.text}")
            elif part.function_call:
                lines.append(f"{content.role}: call {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                lines.append(f"{content.role}: {part.function_response.name} -> {part.function_response.response}")
    return "\n".join(lines)


def instructions_for(agent: str) -> list[str]:
    return [c.system_instruction for c in calls if c.agent == agent]


def text(t: str) -> list[LlmResponse]:
    return [LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))]


def tool(name: str, **args) -> list[LlmResponse]:
    return [LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=name, args=args))]))]


def transfer(agent: str) -> list[LlmResponse]:
    return tool("transfer_to_agent", agent_name=agent)


def empty() -> list[LlmResponse]:
    """What Gemini occasionally returns: finish reason STOP, but no parts at all."""
    return [LlmResponse(content=types.Content(role="model", parts=None))]


@activity.defn(name="invoke_model")
async def fake_invoke_model(llm_request: LlmRequest) -> list[LlmResponse]:
    agent = (llm_request.config.labels or {}).get("adk_agent_name")
    calls.append(ModelCall(agent=agent, system_instruction=str(llm_request.config.system_instruction),
                           contents=_contents_text(llm_request)))
    if not _script:
        raise AssertionError(f"unexpected model call from {agent}: the script is empty")
    expected_agent, response = _script.pop(0)
    if agent != expected_agent:
        raise AssertionError(f"expected a model call from {expected_agent}, got {agent}")
    return response


def scripted_plugin() -> GoogleAdkPlugin:
    """GoogleAdkPlugin with the fake invoke_model in place of the real one."""
    real_invoke_model = adk_plugin_module.invoke_model
    adk_plugin_module.invoke_model = fake_invoke_model
    try:
        return GoogleAdkPlugin()
    finally:
        adk_plugin_module.invoke_model = real_invoke_model
