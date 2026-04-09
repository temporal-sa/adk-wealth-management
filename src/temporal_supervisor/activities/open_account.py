from datetime import timedelta

from temporalio import workflow, activity
from temporalio.client import WorkflowHandle, Client
from temporalio.workflow import ParentClosePolicy
from temporalio.common import RetryPolicy

from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin

with workflow.unsafe.imports_passed_through():
    from common.client_helper import ClientHelper
    from temporal_supervisor.workflows.open_account_workflow import (
        OpenInvestmentAccountWorkflow,
        OpenInvestmentAccountInput,
    )


async def open_new_investment_account(
    client_id: str,
    account_name: str,
    initial_amount: float,
) -> str:
    """Open a new investment account. Returns the child workflow ID.

    Args:
        client_id: The client's unique identifier.
        account_name: The name for the new investment account.
        initial_amount: The initial deposit amount.

    Returns:
        The child workflow ID string.
    """
    account_input = OpenInvestmentAccountInput(
        client_id=client_id,
        account_name=account_name,
        initial_amount=initial_amount,
    )
    current_workflow_id = workflow.info().workflow_id
    child_workflow_id = (
        f"OpenAccount-{current_workflow_id}-{client_id}-{account_name}"
    )
    await workflow.start_child_workflow(
        OpenInvestmentAccountWorkflow.run,
        args=[account_input],
        id=child_workflow_id,
        parent_close_policy=ParentClosePolicy.TERMINATE,
    )
    return child_workflow_id


class OpenAccount:
    retry_policy = RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2,
        maximum_interval=timedelta(seconds=30),
    )

    @staticmethod
    async def _get_workflow_handle(workflow_id: str) -> WorkflowHandle:
        client_helper = ClientHelper()
        the_client = await Client.connect(**client_helper.client_config,
                                          plugins=[ClaimCheckPlugin()])
        return the_client.get_workflow_handle_for(
            OpenInvestmentAccountWorkflow.run, workflow_id
        )

    @staticmethod
    @activity.defn
    async def get_current_client_info(workflow_id: str) -> dict:
        handle = await OpenAccount._get_workflow_handle(workflow_id)
        return await handle.execute_update(
            OpenInvestmentAccountWorkflow.get_client_details
        )

    @staticmethod
    @activity.defn
    async def approve_kyc(workflow_id: str):
        handle = await OpenAccount._get_workflow_handle(workflow_id)
        await handle.signal(OpenInvestmentAccountWorkflow.verify_kyc)

    @staticmethod
    @activity.defn
    async def update_client_details(workflow_id: str, client_fields: dict) -> str:
        """Update client fields. client_fields is a dict of only the fields to change."""
        handle = await OpenAccount._get_workflow_handle(workflow_id)
        return await handle.execute_update(
            OpenInvestmentAccountWorkflow.update_client_details,
            args=[client_fields],
        )
