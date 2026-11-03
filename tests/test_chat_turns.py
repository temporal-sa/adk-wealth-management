"""Runs the real WealthManagementWorkflow with a scripted model (see scripted_model.py)
and stub activities registered under the real activity names.

- The client ID is asked for once and then given to every agent, however many
  handoffs follow.
- An empty model reply is nudged once, and replaced with a fallback message if the
  nudge gets nothing either, so the customer never sees an empty chat bubble.
- The account-opening status tool reports what the open-account workflow last
  signalled, not what the agent remembers.

Requires the Temporal dev server (downloaded automatically on first run).
No GEMINI_API_KEY or Redis needed.
"""

import pytest

import tests.scripted_model as model
from common.account_context import UpdateAccountOpeningStateInput
from temporal_supervisor.workflows.supervisor_workflow import (
    EMPTY_RESPONSE_FALLBACK,
    WealthManagementWorkflow,
)
from tests.chat_harness import CLIENT_ID, end_chat, say as say_turn, start_chat
from tests.scripted_model import empty, text, tool, transfer

pytestmark = pytest.mark.usefixtures("scripted")


async def say(handle, prompt: str) -> str:
    return (await say_turn(handle, prompt)).text_response


def client_id_line(client_id: str) -> str:
    return f'client ID is: "{client_id}".'


async def test_client_id_is_asked_for_once_and_shared_with_every_agent(client):
    handle = await start_chat(client)

    model.expect("Supervisor_Agent", transfer("Beneficiary_Agent"))
    model.expect("Beneficiary_Agent", text("Please provide your client ID."))
    assert await say(handle, "Who are my beneficiaries?") == "Please provide your client ID."

    model.expect("Beneficiary_Agent", tool("list_beneficiaries", client_id=CLIENT_ID))
    model.expect("Beneficiary_Agent", text("Your beneficiary is John Doe (son)."))
    await say(handle, CLIENT_ID)

    # The same handoffs as the reported chat: beneficiaries -> investments -> open account
    model.expect("Beneficiary_Agent", transfer("Investment_Agent"))
    model.expect("Investment_Agent", transfer("Open_Account_Agent"))
    model.expect("Open_Account_Agent", text("Opening the Bike account."))
    await say(handle, "Open Bike with $15,000")

    await end_chat(handle)

    # Before the customer gave the ID, the agents were told it was empty, so they ask for it
    assert client_id_line("") in model.instructions_for("Supervisor_Agent")[0]
    assert client_id_line("") in model.instructions_for("Beneficiary_Agent")[0]
    # After the first tool call with it, every agent is given the ID
    assert client_id_line(CLIENT_ID) in model.instructions_for("Beneficiary_Agent")[-1]
    assert client_id_line(CLIENT_ID) in model.instructions_for("Investment_Agent")[0]
    assert client_id_line(CLIENT_ID) in model.instructions_for("Open_Account_Agent")[0]


async def test_empty_reply_is_nudged_once(client):
    handle = await start_chat(client)

    model.expect("Supervisor_Agent", empty())
    model.expect("Supervisor_Agent", text("How can I help with your accounts?"))
    assert await say(handle, "Hi") == "How can I help with your accounts?"

    await end_chat(handle)


async def test_repeated_empty_reply_falls_back_to_a_message(client):
    handle = await start_chat(client)

    model.expect("Supervisor_Agent", empty())
    model.expect("Supervisor_Agent", empty())
    assert await say(handle, "Hi") == EMPTY_RESPONSE_FALLBACK

    # The chat carries on normally afterwards
    model.expect("Supervisor_Agent", text("How can I help with your accounts?"))
    assert await say(handle, "Hello?") == "How can I help with your accounts?"

    await end_chat(handle)


async def test_status_tool_reports_the_state_the_open_account_workflow_signalled(client):
    handle = await start_chat(client)
    opening_id = "OpenAccount-test-123-Bike"
    rolled_back = "Rolled back. The account was not opened: The custodian rejected the account registration."
    await handle.signal(
        WealthManagementWorkflow.update_account_opening_state,
        UpdateAccountOpeningStateInput(account_name="Bike", state=rolled_back, workflow_id=opening_id),
    )

    model.expect("Supervisor_Agent", transfer("Investment_Agent"))
    model.expect("Investment_Agent", transfer("Open_Account_Agent"))
    model.expect("Open_Account_Agent", tool("get_account_opening_status", workflow_id=opening_id))
    model.expect("Open_Account_Agent", text("The Bike account was not opened."))
    await say(handle, "Is my Bike account open?")

    history = await handle.query(WealthManagementWorkflow.get_chat_history)
    assert rolled_back in history[-1].json_response

    await end_chat(handle)
