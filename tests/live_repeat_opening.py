"""Live test: asking to open the same account again starts a new account opening, with its own KYC.

The customer opens a Vacation account, confirms their details (KYC), and while that opening waits
for compliance review asks to open Vacation again. The second request must start a second account
opening (child workflow ...-Vacation-2) and show the client details for KYC again, not answer from
the conversation history.

The model decides whether to call the tool, so this can't be checked with the scripted model. It
runs the real WealthManagementWorkflow and OpenInvestmentAccountWorkflow with real Gemini calls (see
live_agent_handoffs.py) and the real activities that only read data/*.json. The activities that
would connect to a configured Temporal server (get_current_client_info, approve_kyc,
update_client_details) are replaced by ones that do the same against the test server, and the one
that would write data/clients.json (update_client) by a stub. Compliance is never approved, so no
account is created. The Redis event stream is replaced by in-memory stand-ins, and the chaos flags
read as off.

Not part of the default `uv run pytest` run (pytest only collects test_*.py), because it
calls the paid Gemini API. Run it with:
    source ./setgeminikey.sh && LIVE_TEST_RUNS=3 uv run pytest tests/live_repeat_opening.py -s
LIVE_TEST_RUNS=<n> repeats the conversation n times (default 1), since model output varies.
"""

import os
import uuid

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import temporal_supervisor.chaos as chaos
from temporal_supervisor.activities.clients import ClientActivities
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.activities.beneficiaries import Beneficiaries
from temporal_supervisor.model_config import register_chaos_aware_gemini
from temporal_supervisor.task_queues import LLM_TASK_QUEUE, OPEN_ACCOUNT_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow
from tests.live_agent_handoffs import (
    Turn, append_chat_interaction, append_status_update, chaos_off, delete_conversation, model_outputs,
    recording_plugin, say, transcript,
)

RUNS = max(1, int(os.getenv("LIVE_TEST_RUNS", "1")))
TASK_QUEUE = "live-repeat-opening"
CONVERSATION = (
    "Who are my beneficiaries?", "123", "what investments do I have?",
    "Open Vacation with $13,000", "Yes", "Open Vacation with $13,000",
)
# Client 123's details in data/clients.json, shown for KYC
KYC_DETAILS = ("Don", "123 Main Street")

pytestmark = pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set (source ./setgeminikey.sh)")


# --- The open-account activities, against the test server ---------------------------------

test_client: Client | None = None


@activity.defn(name="get_current_client_info")
async def get_current_client_info(workflow_id: str) -> dict:
    handle = test_client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    return await handle.execute_update(OpenInvestmentAccountWorkflow.get_client_details)


@activity.defn(name="approve_kyc")
async def approve_kyc(workflow_id: str) -> None:
    handle = test_client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)


@activity.defn(name="update_client_details")
async def update_client_details(workflow_id: str, client_fields: dict) -> str:
    handle = test_client.get_workflow_handle_for(OpenInvestmentAccountWorkflow.run, workflow_id)
    return await handle.execute_update(OpenInvestmentAccountWorkflow.update_client_details, args=[client_fields])


@activity.defn(name="update_client")
async def update_client(client_id: str, field_dict: dict) -> str:
    return "Client details updated."  # never writes data/clients.json


async def child_ids(client: Client, chat_id: str) -> list[str]:
    return sorted([w.id async for w in client.list_workflows(
        f"WorkflowType='OpenInvestmentAccountWorkflow' AND ParentWorkflowId='{chat_id}'")])


def problems(turns: list[Turn], children: list[str]) -> list[str]:
    found = []
    for index in (3, 5):
        reply = turns[index].reply.text_response
        for value in KYC_DETAILS:
            if value not in reply:
                found.append(f"reply to message {index + 1} doesn't show {value!r}")
    for suffix in ("-Vacation-1", "-Vacation-2"):
        if not any(child.endswith(suffix) for child in children):
            found.append(f"no child workflow ending {suffix!r} (started: {children})")
    return found


async def test_opening_the_same_account_again_starts_a_new_opening_with_kyc(monkeypatch):
    global test_client
    monkeypatch.setattr(chaos, "is_enabled", chaos_off)
    register_chaos_aware_gemini()
    model_outputs.clear()

    async with await WorkflowEnvironment.start_local() as env:
        config = env.client.config()
        config["plugins"] = [recording_plugin()]
        client = Client(**config)
        test_client = client
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow],
            activities=[Beneficiaries.list_beneficiaries, Investments.list_investments,
                        Investments.validate_new_account, Investments.find_promotion,
                        get_current_client_info, approve_kyc, update_client_details,
                        append_chat_interaction, append_status_update, delete_conversation],
        ), Worker(
            client,
            task_queue=OPEN_ACCOUNT_TASK_QUEUE,
            workflows=[OpenInvestmentAccountWorkflow],
            activities=[ClientActivities.get_client, update_client],
        ), Worker(client, task_queue=LLM_TASK_QUEUE):  # model calls; the plugin adds invoke_model
            failures = []
            for run in range(1, RUNS + 1):
                handle = await client.start_workflow(
                    WealthManagementWorkflow.run, id=f"live-repeat-opening-{run}-{uuid.uuid4()}", task_queue=TASK_QUEUE)
                turns = [await say(handle, prompt) for prompt in CONVERSATION]
                children = await child_ids(client, handle.id)
                await handle.signal(WealthManagementWorkflow.end_workflow)
                await handle.result()

                text = transcript(run, turns) + f"\n  child workflows: {children}"
                found = problems(turns, children)
                print(f"\n{text}\n  result: {'FAIL: ' + '; '.join(found) if found else 'pass'}")
                if found:
                    failures.append(f"{'; '.join(found)}\n{text}")

            print(f"\n{RUNS - len(failures)}/{RUNS} runs passed")
            assert not failures, f"{len(failures)}/{RUNS} runs failed:\n\n" + "\n\n".join(failures)
