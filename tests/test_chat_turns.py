"""Runs the real WealthManagementWorkflow with a scripted model (see scripted_model.py)
and stub activities registered under the real activity names.

- The client ID is asked for once and then given to every agent, however many
  handoffs follow.
- An empty model reply is nudged once, and replaced with a fallback message if the
  nudge gets nothing either, so the customer never sees an empty chat bubble.

Requires the Temporal dev server (downloaded automatically on first run).
No GEMINI_API_KEY or Redis needed.
"""

import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import tests.scripted_model as model
from common.user_message import ChatInteraction, ProcessUserMessageInput
from common.status_update import StatusUpdate
from temporal_supervisor.workflows.supervisor_workflow import (
    EMPTY_RESPONSE_FALLBACK,
    WealthManagementWorkflow,
)
from tests.scripted_model import empty, text, tool, transfer

TASK_QUEUE = "test-chat-turns"
CLIENT_ID = "123"


@activity.defn(name="list_beneficiaries")
async def list_beneficiaries(client_id: str) -> list:
    return [{"beneficiary_id": "b-1", "first_name": "John", "last_name": "Doe", "relationship": "son"}]


@activity.defn(name="append_chat_interaction")
async def append_chat_interaction(workflow_id: str, chat_interaction: ChatInteraction) -> int:
    return 1


@activity.defn(name="append_status_update")
async def append_status_update(workflow_id: str, status_update: StatusUpdate) -> int:
    return 1


@activity.defn(name="delete_conversation")
async def delete_conversation(workflow_id: str) -> bool:
    return True


@pytest.fixture(scope="module")
async def client():
    async with await WorkflowEnvironment.start_local() as env:
        config = env.client.config()
        config["plugins"] = [model.scripted_plugin()]
        client = Client(**config)
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow],
            activities=[list_beneficiaries, append_chat_interaction, append_status_update, delete_conversation],
        ):
            yield client


@pytest.fixture(autouse=True)
def reset_model():
    model.reset()
    yield
    assert not model.unused(), f"scripted responses were not used: {model.unused()}"


async def start_chat(client: Client) -> WorkflowHandle:
    return await client.start_workflow(
        WealthManagementWorkflow.run, id=f"test-chat-{uuid.uuid4()}", task_queue=TASK_QUEUE
    )


async def say(handle: WorkflowHandle, prompt: str) -> str:
    """Send one chat message and return the reply shown to the customer."""
    turns = len(await handle.query(WealthManagementWorkflow.get_chat_history))
    await handle.signal(WealthManagementWorkflow.process_user_message, ProcessUserMessageInput(user_input=prompt))
    for _ in range(200):
        history = await handle.query(WealthManagementWorkflow.get_chat_history)
        if len(history) > turns:
            return history[-1].text_response
        await asyncio.sleep(0.05)
    raise TimeoutError(f"no reply to {prompt!r}")


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

    await handle.signal(WealthManagementWorkflow.end_workflow)
    await handle.result()

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

    await handle.signal(WealthManagementWorkflow.end_workflow)
    await handle.result()


async def test_repeated_empty_reply_falls_back_to_a_message(client):
    handle = await start_chat(client)

    model.expect("Supervisor_Agent", empty())
    model.expect("Supervisor_Agent", empty())
    assert await say(handle, "Hi") == EMPTY_RESPONSE_FALLBACK

    # The chat carries on normally afterwards
    model.expect("Supervisor_Agent", text("How can I help with your accounts?"))
    assert await say(handle, "Hello?") == "How can I help with your accounts?"

    await handle.signal(WealthManagementWorkflow.end_workflow)
    await handle.result()
