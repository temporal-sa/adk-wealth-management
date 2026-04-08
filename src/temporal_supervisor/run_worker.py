import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.contrib.google_adk_agents import GoogleAdkPlugin

from common.client_helper import ClientHelper
from temporal_supervisor.activities.clients import ClientActivities
from temporal_supervisor.activities.event_stream_activities import EventStreamActivities
from temporal_supervisor.activities.open_account import OpenAccount
from temporal_supervisor.activities.beneficiaries import Beneficiaries
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow
from temporal_supervisor.workflows.open_account_workflow import OpenInvestmentAccountWorkflow


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)s | %(message)s",
    )

    client_helper = ClientHelper()

    plugins = [] if client_helper.skipADKPlugin else [GoogleAdkPlugin()]

    client = await Client.connect(**client_helper.client_config,
        plugins=plugins,
    )

    worker = Worker(
        client,
        task_queue=client_helper.taskQueue,
        workflows=[WealthManagementWorkflow, OpenInvestmentAccountWorkflow],
        activities=[
            Beneficiaries.list_beneficiaries,
            Beneficiaries.add_beneficiary,
            Beneficiaries.delete_beneficiary,
            Investments.list_investments,
            Investments.open_investment,
            Investments.close_investment,
            ClientActivities.get_client,
            ClientActivities.add_client,
            ClientActivities.update_client,
            OpenAccount.get_current_client_info,
            OpenAccount.approve_kyc,
            OpenAccount.update_client_details,
            EventStreamActivities.append_chat_interaction,
            EventStreamActivities.append_status_update,
            EventStreamActivities.delete_conversation,
        ],
    )
    print(
        f"Running worker on {client_helper.address}, task queue: {client_helper.taskQueue}"
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
