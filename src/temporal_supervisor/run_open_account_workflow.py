"""Exercise OpenInvestmentAccountWorkflow on its own, without the chat or the LLM.

Starts the open-account workflow directly for three scenarios:

1. KYC approved (``verify_kyc`` signal), then compliance approved.
2. Client details read (``get_client_details`` update) and changed
   (``update_client_details`` update, which also completes KYC), then compliance
   approved.
3. KYC approved, then waits until you send the compliance approval yourself
   (``localsendcomplianceapproval.sh <workflow ID>``).

The workflow is started on TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT (default: the main
task queue), so it runs on whichever worker polls that queue: the Python worker,
or the Java worker in java/ when the queues are split. The activities it calls
run on the Python worker's main task queue, so a Python worker must be running
(SKIP_ADK_PLUGIN=True runs it without the Google ADK plugin, so no Gemini key).

The scenarios change data/*.json like the demo does (client 123's last name, a
new account, the linked bank account balance); reset with ``git checkout data/``.

Usage (from the project root; see startlocalopenaccount.sh):
    uv run python src/temporal_supervisor/run_open_account_workflow.py [--scenario N ...] [--amount X]
"""

import argparse
import asyncio
import sys
import uuid
from typing import Optional

from temporalio.client import Client, WorkflowHandle

from common.client_helper import ClientHelper
from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin
from temporal_supervisor.task_queues import OPEN_ACCOUNT_TASK_QUEUE
from temporal_supervisor.workflows.open_account_workflow import (
    OpenInvestmentAccountInput,
    OpenInvestmentAccountWorkflow,
)

CLIENT_ID = "123"
WAITING_KYC = "Waiting KYC"
WAITING_COMPLIANCE_REVIEW = "Waiting Compliance Review"
# Account name and initial deposit of each scenario.
SCENARIOS = {1: ("Vacation", 150.78), 2: ("Vacation2", 350.72), 3: ("Vacation3", 176.73)}


async def _connect() -> Client:
    client_helper = ClientHelper()
    print(f"address is {client_helper.address}")
    # The claim check plugin uses the same data converter as the worker
    # (the Redis claim check codec when USE_CLAIM_CHECK=true).
    return await Client.connect(**client_helper.client_config, plugins=[ClaimCheckPlugin()])


async def _start(client: Client, run_id: str, scenario: int, amount: Optional[float]) -> WorkflowHandle:
    account_name, default_amount = SCENARIOS[scenario]
    workflow_id = f"testing-open-account-{run_id}-{scenario}"
    handle = await client.start_workflow(
        OpenInvestmentAccountWorkflow.run,
        OpenInvestmentAccountInput(
            client_id=CLIENT_ID,
            account_name=account_name,
            initial_amount=amount if amount is not None else default_amount,
        ),
        id=workflow_id,
        task_queue=OPEN_ACCOUNT_TASK_QUEUE,
    )
    print(f"Started {workflow_id} on task queue '{OPEN_ACCOUNT_TASK_QUEUE}'")
    return handle


async def _state(handle: WorkflowHandle) -> str:
    return await handle.query(OpenInvestmentAccountWorkflow.get_current_state)


async def _wait_for_state(handle: WorkflowHandle, state: str, timeout: float = 30.0) -> None:
    """Wait until the workflow reports ``state`` (the signals and updates are asynchronous)."""
    for _ in range(int(timeout * 4)):
        if await _state(handle) == state:
            return
        await asyncio.sleep(0.25)
    raise TimeoutError(f"{handle.id} did not reach '{state}' (current state: '{await _state(handle)}')")


async def _finish(handle: WorkflowHandle) -> None:
    """Print each state the saga goes through, then the result and the final state."""
    result_task = asyncio.create_task(handle.result())
    last = None
    while not result_task.done():
        state = await _state(handle)
        if state != last:
            print(f"  state: '{state}'")
            last = state
        await asyncio.wait({result_task}, timeout=0.25)
    print(f"The result is {result_task.result()}")
    print(f"The final state is '{await _state(handle)}'")


async def scenario1(client: Client, run_id: str, amount: Optional[float]) -> None:
    handle = await _start(client, run_id, 1, amount)
    await _wait_for_state(handle, WAITING_KYC)
    print(f"The current state is '{await _state(handle)}'")

    await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)
    await _wait_for_state(handle, WAITING_COMPLIANCE_REVIEW)
    print(f"After verifying KYC, the current state is '{await _state(handle)}'")

    await handle.signal(OpenInvestmentAccountWorkflow.compliance_approved)
    print("Sent the compliance approval")
    await _finish(handle)


async def scenario2(client: Client, run_id: str, amount: Optional[float]) -> None:
    handle = await _start(client, run_id, 2, amount)

    # The update waits until the workflow has loaded the client.
    details = await handle.execute_update(OpenInvestmentAccountWorkflow.get_client_details)
    print(f"The client details are {details}")

    result = await handle.execute_update(
        OpenInvestmentAccountWorkflow.update_client_details, {"last_name": "Doenut"}
    )
    print(f"The result of updating the client details is '{result}'")

    # Updating the details also completes KYC.
    await _wait_for_state(handle, WAITING_COMPLIANCE_REVIEW)
    print(f"After updating the details, the current state is '{await _state(handle)}'")

    await handle.signal(OpenInvestmentAccountWorkflow.compliance_approved)
    print("Sent the compliance approval")
    await _finish(handle)


async def scenario3(client: Client, run_id: str, amount: Optional[float]) -> None:
    handle = await _start(client, run_id, 3, amount)
    await _wait_for_state(handle, WAITING_KYC)
    await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)
    await _wait_for_state(handle, WAITING_COMPLIANCE_REVIEW)
    print(f"After verifying KYC, the current state is '{await _state(handle)}'")

    print("*****>>> Send the compliance approval for this workflow, in another terminal:")
    print(f"*****>>>   cd src/temporal_supervisor && ./localsendcomplianceapproval.sh {handle.id}")
    state = await _state(handle)
    while state == WAITING_COMPLIANCE_REVIEW:
        await asyncio.sleep(1)
        state = await _state(handle)
    print("Compliance approved")
    await _finish(handle)


async def main(scenarios: list, amount: Optional[float]) -> None:
    client = await _connect()
    run_id = uuid.uuid4().hex[:8]
    runners = {1: scenario1, 2: scenario2, 3: scenario3}
    for number in scenarios:
        print(f"\n=== Scenario {number} ===")
        await runners[number](client, run_id, amount)


if __name__ == "__main__":
    # Print each line as it happens, also when the output is piped or redirected.
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Run the open-account workflow's three scenarios without the chat.")
    parser.add_argument(
        "--scenario", type=int, choices=sorted(SCENARIOS), action="append",
        help="Run only this scenario (repeatable). Default: all three.",
    )
    parser.add_argument(
        "--amount", type=float,
        help="Initial deposit for every scenario run, e.g. 300000 for client 123 to see the saga roll back.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.scenario or sorted(SCENARIOS), args.amount))
