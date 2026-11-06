"""A tool that raises gives its error to the model instead of failing the chat workflow,
and the agents can only transfer along the OpenAI version's handoffs.

Runs the real WealthManagementWorkflow with a scripted model (see chat_harness.py).

Requires the Temporal dev server (downloaded automatically on first run).
No GEMINI_API_KEY or Redis needed.
"""

import asyncio

import pytest
from google.adk.flows.llm_flows.agent_transfer import _get_transfer_targets
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ActivityError, CancelledError, RetryState

import tests.scripted_model as model
from temporal_supervisor.workflows.supervisor_workflow import init_agents, tool_error_to_model
from tests.chat_harness import FAILING_OPENING, end_chat, say, start_chat
from tests.scripted_model import text, tool, transfer


@pytest.mark.usefixtures("scripted")
async def test_a_tool_error_goes_to_the_model_and_the_chat_carries_on(client):
    handle = await start_chat(client)
    model.expect("Supervisor_Agent", transfer("Investment_Agent"))
    model.expect("Investment_Agent", transfer("Open_Account_Agent"))
    # The stub get_current_client_info activity fails (non-retryable) for this opening
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=FAILING_OPENING))
    model.expect("Open_Account_Agent", text("Sorry, I couldn't retrieve your details."))
    turn = await say(handle, "Show my details for my Vacation account opening")

    assert "'error': 'Tool get_current_client_info failed" in turn.json_response
    assert turn.text_response == "Sorry, I couldn't retrieve your details."
    assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING

    model.expect("Open_Account_Agent", text("Anything else?"))
    assert (await say(handle, "Thanks")).text_response == "Anything else?"
    await end_chat(handle)


@pytest.mark.parametrize("error", [
    asyncio.CancelledError(),
    CancelledError("cancelled"),
    ActivityError("failed", scheduled_event_id=1, started_event_id=2, identity="", activity_type="a",
                  activity_id="1", retry_state=RetryState.CANCEL_REQUESTED),
    workflow.NondeterminismError("nondeterminism"),
    workflow._NotInWorkflowEventLoopError(),
])
def test_cancellation_and_temporal_internal_errors_are_not_given_to_the_model(error):
    if isinstance(error, ActivityError):
        error.__cause__ = CancelledError("cancelled")
    assert tool_error_to_model(None, {}, None, error) is None


def test_transfers_match_the_openai_handoffs():
    supervisor = init_agents()
    agents = {supervisor.name: supervisor}
    for agent in supervisor.sub_agents:
        agents[agent.name] = agent
        for sub in agent.sub_agents:
            agents[sub.name] = sub

    def targets(name):
        return sorted(a.name for a in _get_transfer_targets(agents[name]))

    assert targets("Supervisor_Agent") == ["Beneficiary_Agent", "Investment_Agent"]
    assert targets("Beneficiary_Agent") == ["Supervisor_Agent"]
    assert targets("Investment_Agent") == ["Open_Account_Agent", "Supervisor_Agent"]
    assert targets("Open_Account_Agent") == ["Investment_Agent"]
