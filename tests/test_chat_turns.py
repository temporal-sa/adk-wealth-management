"""Runs the real WealthManagementWorkflow with a scripted model (see scripted_model.py)
and stub activities registered under the real activity names.

- The client ID is asked for once and then given to every agent, however many
  handoffs follow.
- An empty model reply is nudged once, and replaced with a fallback message if the
  nudge gets nothing either, so the customer never sees an empty chat bubble.
- The account-opening status tool reports what the open-account workflow last
  signalled, not what the agent remembers.
- Every request to open an account starts a new account opening (child workflow),
  with its own KYC, even for an account name opened earlier in the chat.

Requires the Temporal dev server (downloaded automatically on first run).
No GEMINI_API_KEY or Redis needed.
"""

import asyncio

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Worker

import tests.scripted_model as model
from common.account_context import UpdateAccountOpeningStateInput
from temporal_supervisor.workflows.supervisor_workflow import (
    EMPTY_RESPONSE_FALLBACK,
    WealthManagementWorkflow,
)
from temporal_supervisor.task_queues import OPEN_ACCOUNT_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from tests.chat_harness import CLIENT_DETAILS, CLIENT_ID, end_chat, kyc_approvals, say as say_turn, start_chat
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

    # Beneficiaries -> investments -> open account (via the supervisor, as in the OpenAI version)
    model.expect("Beneficiary_Agent", transfer("Supervisor_Agent"))
    model.expect("Supervisor_Agent", transfer("Investment_Agent"))
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
    opening_id = "OpenAccount-test-123-Bike-1"
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
    # A finished opening's status tells the agent how to open the account again
    assert "call open_new_investment_account" in history[-1].json_response

    await end_chat(handle)


@activity.defn(name="get_client")
async def stub_get_client(client_id: str) -> dict:
    return CLIENT_DETAILS


async def child_state(client, workflow_id: str, expected: str) -> None:
    child = client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    for _ in range(200):
        if await child.query(OpenInvestmentAccountWorkflow.get_current_state) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{workflow_id} never reached {expected!r}")


async def test_every_request_to_open_an_account_starts_a_new_opening_with_its_own_kyc(client):
    # Opening "Vacation" twice in one chat starts two child workflows, numbered by the
    # openings started in the chat, even while the first is still waiting. The second needs
    # its own KYC: the confirmation for the first opening doesn't approve it.
    handle = await start_chat(client)
    first = f"OpenAccount-{handle.id}-{CLIENT_ID}-Vacation-1"
    second = f"OpenAccount-{handle.id}-{CLIENT_ID}-Vacation-2"
    details = (f"First Name: {CLIENT_DETAILS['first_name']}\nLast Name: {CLIENT_DETAILS['last_name']}\n"
               f"Email: {CLIENT_DETAILS['email']}\nIs this information correct?")
    open_vacation = tool("open_new_investment_account", client_id=CLIENT_ID, account_name="Vacation",
                         initial_amount=13000)

    # The open-account workflows run, with the client record from a stub activity
    async with Worker(client, task_queue=OPEN_ACCOUNT_TASK_QUEUE,
                      workflows=[OpenInvestmentAccountWorkflow], activities=[stub_get_client]):
        model.expect("Supervisor_Agent", transfer("Investment_Agent"))
        model.expect("Investment_Agent", transfer("Open_Account_Agent"))
        model.expect("Open_Account_Agent", open_vacation)
        model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=first))
        model.expect("Open_Account_Agent", text(details))
        turn = await say_turn(handle, "Open Vacation with $13,000")
        assert first in turn.json_response
        await child_state(client, first, "Waiting KYC")

        model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=first))
        model.expect("Open_Account_Agent", text("Thanks. Your account is waiting for compliance review."))
        await say_turn(handle, "Yes")
        assert kyc_approvals == [first]
        # What the real approve_kyc activity does
        await client.get_workflow_handle(first).signal(OpenInvestmentAccountWorkflow.verify_kyc)
        await child_state(client, first, "Waiting Compliance Review")

        # The same request again starts a second opening. Approving its KYC straight away,
        # on the strength of the first opening's confirmation, is refused.
        model.expect("Open_Account_Agent", open_vacation)
        model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=second))
        model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=second))
        model.expect("Open_Account_Agent", text(details))
        turn = await say_turn(handle, "Open Vacation with $13,000")
        assert second in turn.json_response
        assert "KYC was not approved" in turn.json_response
        assert kyc_approvals == [first]
        await child_state(client, second, "Waiting KYC")

        model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=second))
        model.expect("Open_Account_Agent", text("Thanks. Your account is waiting for compliance review."))
        await say_turn(handle, "Yes")
        assert kyc_approvals == [first, second]
        await client.get_workflow_handle(second).signal(OpenInvestmentAccountWorkflow.verify_kyc)
        await child_state(client, second, "Waiting Compliance Review")

        # Both openings are children of the chat, still running
        for opening in (first, second):
            description = await client.get_workflow_handle(opening).describe()
            assert description.status == WorkflowExecutionStatus.RUNNING
            assert description.parent_id == handle.id
        await end_chat(handle)

