"""Runs the real WealthManagementWorkflow with the scripted model (see scripted_model.py)
and stub activities registered under the real activity names, so chat tests need no
Gemini key, Redis or data files. The fixtures that use it are in conftest.py.

Account openings started by a chat are not run: no worker polls their task queue.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker

from common.status_update import StatusUpdate
from common.user_message import ChatInteraction, ProcessUserMessageInput
from temporal_supervisor.task_queues import LLM_TASK_QUEUE
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow
import tests.scripted_model as model

TASK_QUEUE = "test-chat"
CLIENT_ID = "123"
CLIENT_DETAILS = {
    "client_id": CLIENT_ID, "first_name": "Sam", "last_name": "Doe", "address": "1 Main St",
    "phone": "555-0100", "email": "sam@example.com", "marital_status": "single",
}

# Account openings that KYC was approved for, by workflow ID
kyc_approvals: list[str] = []


@activity.defn(name="list_beneficiaries")
async def list_beneficiaries(client_id: str) -> list:
    return [{"beneficiary_id": "b-1", "first_name": "John", "last_name": "Doe", "relationship": "son"}]


@activity.defn(name="validate_new_account")
async def validate_new_account(client_id: str, account_name: str, initial_amount: float) -> None:
    pass


@activity.defn(name="find_promotion")
async def find_promotion(client_id: str) -> None:
    return None


@activity.defn(name="get_current_client_info")
async def get_current_client_info(workflow_id: str) -> dict:
    return CLIENT_DETAILS


@activity.defn(name="approve_kyc")
async def approve_kyc(workflow_id: str) -> None:
    kyc_approvals.append(workflow_id)


# Chat turns the workflows published (what the UI would show), by workflow ID
published_turns: dict[str, list[ChatInteraction]] = {}


@activity.defn(name="append_chat_interaction")
async def append_chat_interaction(workflow_id: str, chat_interaction: ChatInteraction) -> int:
    published_turns.setdefault(workflow_id, []).append(chat_interaction)
    return 1


@activity.defn(name="append_status_update")
async def append_status_update(workflow_id: str, status_update: StatusUpdate) -> int:
    return 1


@activity.defn(name="delete_conversation")
async def delete_conversation(workflow_id: str) -> bool:
    return True


@asynccontextmanager
async def chat_workers(env_client: Client):
    """A client using the scripted model, with workers for the chat workflow and model calls."""
    config = env_client.config()
    config["plugins"] = [model.scripted_plugin()]
    client = Client(**config)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[WealthManagementWorkflow],
        activities=[list_beneficiaries, validate_new_account, find_promotion, get_current_client_info, approve_kyc,
                    append_chat_interaction, append_status_update, delete_conversation],
    ), Worker(client, task_queue=LLM_TASK_QUEUE):  # model calls; the plugin adds invoke_model
        yield client


async def start_chat(client: Client) -> WorkflowHandle:
    return await client.start_workflow(
        WealthManagementWorkflow.run, id=f"test-chat-{uuid.uuid4()}", task_queue=TASK_QUEUE
    )


async def say(handle: WorkflowHandle, prompt: str) -> ChatInteraction:
    """Send one chat message and return the turn: the reply shown to the customer, and the tool results."""
    turns = len(await handle.query(WealthManagementWorkflow.get_chat_history))
    await handle.signal(WealthManagementWorkflow.process_user_message, ProcessUserMessageInput(user_input=prompt))
    for _ in range(200):
        history = await handle.query(WealthManagementWorkflow.get_chat_history)
        if len(history) > turns:
            return history[-1]
        await asyncio.sleep(0.05)
    raise TimeoutError(f"no reply to {prompt!r}")


async def end_chat(handle: WorkflowHandle) -> None:
    await handle.signal(WealthManagementWorkflow.end_workflow)
    await handle.result()
