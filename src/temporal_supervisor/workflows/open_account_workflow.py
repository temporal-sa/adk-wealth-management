from dataclasses import dataclass
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from common.investment_manager import InvestmentAccount
    from temporal_supervisor.activities.clients import ClientActivities
    from temporal_supervisor.activities.investments import Investments
    from common.account_context import UpdateAccountOpeningStateInput


@dataclass
class OpenInvestmentAccountInput:
    client_id: str
    account_name: str
    initial_amount: float


@dataclass
class OpenInvestmentAccountOutput:
    account_created: bool = False
    message: str = None


@dataclass
class WealthManagementClient:
    first_name: str = None
    last_name: str = None
    address: str = None
    phone: str = None
    email: str = None
    marital_status: str = None


@workflow.defn
class OpenInvestmentAccountWorkflow:
    def __init__(self):
        self.client = None
        self.inputs: OpenInvestmentAccountInput = None
        self.initialized = False
        self.sched_to_close_timeout = timedelta(seconds=5)
        self.kyc_verified = False
        self.compliance_reviewed = False
        self.current_state = "Initializing"

    @workflow.run
    async def run(self, inputs: OpenInvestmentAccountInput) -> OpenInvestmentAccountOutput:
        workflow.logger.info(f"started workflow {inputs}")
        self.inputs = inputs

        self.client = await workflow.execute_activity(
            ClientActivities.get_client,
            self.inputs.client_id,
            schedule_to_close_timeout=self.sched_to_close_timeout,
            retry_policy=ClientActivities.retry_policy,
        )
        self.initialized = True
        await self._set_state("Waiting KYC")

        await workflow.wait_condition(lambda: self.kyc_verified)
        await self._set_state("Waiting Compliance Review")

        await workflow.wait_condition(lambda: self.compliance_reviewed)
        await self._set_state(
            "Compliance review has been approved. Creating Investment Account"
        )

        new_account = InvestmentAccount(self.inputs.client_id, inputs.account_name, inputs.initial_amount)
        investment_account = await workflow.execute_activity(
            Investments.open_investment,
            args=[new_account],
            schedule_to_close_timeout=self.sched_to_close_timeout,
            retry_policy=ClientActivities.retry_policy,
        )
        await self._set_state("Complete")

        return_value = OpenInvestmentAccountOutput()
        return_value.account_created = investment_account is not None
        return_value.message = (
            "investment account created"
            if investment_account is not None
            else "An unexpected error occurred creating investment account"
        )
        return return_value

    @workflow.query
    async def get_current_state(self) -> str:
        return self.current_state

    @workflow.update
    async def get_client_details(self) -> WealthManagementClient:
        await workflow.wait_condition(lambda: self.initialized)
        return self.client

    @workflow.update
    async def update_client_details(self, client: dict) -> str:
        result = await workflow.execute_activity(
            ClientActivities.update_client,
            args=[self.inputs.client_id, client],
            schedule_to_close_timeout=self.sched_to_close_timeout,
            retry_policy=ClientActivities.retry_policy,
        )
        self.kyc_verified = True
        return result

    @workflow.signal
    async def verify_kyc(self) -> None:
        await workflow.wait_condition(lambda: self.initialized)
        self.kyc_verified = True

    @workflow.signal
    async def compliance_approved(self) -> None:
        await workflow.wait_condition(lambda: self.initialized and self.kyc_verified)
        self.compliance_reviewed = True

    async def _set_state(self, state: str) -> None:
        self.current_state = state
        await self._update_parent_state(state)

    async def _update_parent_state(self, state: str) -> None:
        parent_handle = workflow.get_external_workflow_handle(
            workflow.info().parent.workflow_id
        )
        await parent_handle.signal(
            "update_account_opening_state",
            UpdateAccountOpeningStateInput(
                account_name=self.inputs.account_name,
                state=state,
            ),
        )
