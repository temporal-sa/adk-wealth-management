"""The chaos panel's "Bug in workflow code" switch heals stuck account openings.

OpenInvestmentAccountWorkflow reads the switch with a local activity just before the buggy
welcome-bonus line. While it is on, the workflow task fails and the workflow waits; once it
is turned off, the next workflow-task retry records "off", opens the account with the
bonus, and the history replays deterministically.

Stub activities, no Redis or Gemini key needed. Requires the Temporal dev server.
"""

import asyncio
import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from temporal_supervisor.workflows.open_account_workflow import (
    OpenInvestmentAccountInput,
    OpenInvestmentAccountWorkflow,
)

TASK_QUEUE = "test-workflow-bug-switch"
SWITCH = {"on": True}
DEPOSITS: list = []


@activity.defn(name="workflow_bug_enabled")
async def stub_workflow_bug_enabled() -> bool:
    return SWITCH["on"]


@activity.defn(name="get_client")
async def stub_get_client(client_id: str) -> dict:
    return {
        "client_id": client_id, "first_name": "Sam", "last_name": "Doe", "address": "1 Main St",
        "phone": "555", "email": "s@example.com", "marital_status": "single",
    }


@activity.defn(name="open_investment")
async def stub_open_investment(new_account: Any) -> dict:
    DEPOSITS.append(new_account["balance"] if isinstance(new_account, dict) else new_account.balance)
    return {"investment_id": "i-test", "name": "Test", "balance": 0.0}


@activity.defn(name="debit_linked_account")
async def stub_debit(client_id: str, amount: float, transfer_id: str) -> dict:
    return {}


@activity.defn(name="register_account")
async def stub_register(client_id: str, investment_id: str, amount: float) -> str:
    return "CUST-1"


@pytest.fixture(scope="module")
async def temporal_env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


async def _workflow_task_failure(handle) -> str:
    for _ in range(80):
        async for event in handle.fetch_history_events():
            if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED:
                return event.workflow_task_failed_event_attributes.failure.message
        await asyncio.sleep(0.25)
    return ""


async def test_turning_the_switch_off_heals_the_stuck_opening(temporal_env):
    SWITCH["on"] = True
    DEPOSITS.clear()
    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[OpenInvestmentAccountWorkflow],
        activities=[stub_workflow_bug_enabled, stub_get_client, stub_open_investment, stub_debit, stub_register],
    ):
        handle = await temporal_env.client.start_workflow(
            OpenInvestmentAccountWorkflow.run,
            OpenInvestmentAccountInput(
                client_id="C-1", account_name="Test", initial_amount=500.0, promo_code="WELCOME-BONUS"
            ),
            id=f"open-account-bug-switch-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)
        await handle.signal(OpenInvestmentAccountWorkflow.compliance_approved)

        # Switch on: the buggy line fails the workflow task; the workflow waits, still running.
        assert "initial_deposit" in await _workflow_task_failure(handle)
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert DEPOSITS == []

        # Switch off: the next workflow-task retry heals it, with the 1% bonus.
        SWITCH["on"] = False
        result = await asyncio.wait_for(handle.result(), timeout=60)
        assert result.account_created
        assert DEPOSITS == [505.0]

    # The switch's answer is recorded, so the history replays even with the switch back on.
    SWITCH["on"] = True
    history = await handle.fetch_history()
    await Replayer(workflows=[OpenInvestmentAccountWorkflow]).replay_workflow(history)
