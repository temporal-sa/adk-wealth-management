from datetime import timedelta

from temporalio import workflow, activity
from temporalio.client import WorkflowHandle, Client
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.workflow import ParentClosePolicy

from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin

with workflow.unsafe.imports_passed_through():
    from common.client_helper import ClientHelper
    from temporal_supervisor.activities.investments import Investments
    from temporal_supervisor.retry_policies import TOOL_RETRY_POLICY
    from temporal_supervisor.task_queues import OPEN_ACCOUNT_TASK_QUEUE
    from temporal_supervisor.workflows.open_account_workflow import (
        OpenInvestmentAccountWorkflow,
        OpenInvestmentAccountInput,
    )


async def open_new_investment_account(
    client_id: str,
    account_name: str,
    initial_amount: float,
) -> str:
    """Begin opening a new investment account.

    Args:
        client_id: The client's unique identifier.
        account_name: The name for the new investment account.
        initial_amount: The initial deposit amount.

    Returns:
        The workflow ID of the account opening, which the other open-account tools
        require, or the reason the account cannot be opened.
    """
    # Business rules are checked up front. A violation is a non-retryable error:
    # it fails on the first attempt, and the reason goes back to the agent.
    try:
        await workflow.execute_activity(
            Investments.validate_new_account,
            args=[client_id, account_name, initial_amount],
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=TOOL_RETRY_POLICY,
        )
    except ActivityError as e:
        # The agent would only see "Activity task failed"; pass the business reason through instead.
        if isinstance(e.__cause__, ApplicationError):
            return f"The account cannot be opened: {e.__cause__.message}"
        raise

    # Read in an activity so the result is recorded in history and replays the same.
    promo_code = await workflow.execute_activity(
        Investments.find_promotion,
        client_id,
        start_to_close_timeout=timedelta(seconds=5),
        retry_policy=TOOL_RETRY_POLICY,
    )
    account_input = OpenInvestmentAccountInput(
        client_id=client_id,
        account_name=account_name,
        initial_amount=initial_amount,
        promo_code=promo_code,
    )
    # Every request starts a new account opening, even for an account name opened (or still
    # being opened) earlier in this conversation, so the ID ends with the number of openings
    # started so far, counting this one. The count is workflow state, so it replays the same.
    chat = workflow.instance()
    opening_number = chat.account_openings_started + 1
    current_workflow_id = workflow.info().workflow_id
    child_workflow_id = (
        f"OpenAccount-{current_workflow_id}-{client_id}-{account_name}-{opening_number}"
    )
    await workflow.start_child_workflow(
        OpenInvestmentAccountWorkflow.run,
        args=[account_input],
        id=child_workflow_id,
        parent_close_policy=ParentClosePolicy.TERMINATE,
        task_queue=OPEN_ACCOUNT_TASK_QUEUE,
    )
    # Counted only once the opening has actually started
    chat.account_openings_started = opening_number
    chat.account_opening_states[child_workflow_id] = "Initializing"
    return child_workflow_id


class OpenAccount:
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
