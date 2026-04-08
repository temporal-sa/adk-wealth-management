from dataclasses import dataclass
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from common.client_manager import ClientManager


@dataclass
class WealthManagementClient:
    client_id: str
    first_name: str
    last_name: str
    address: str
    phone: str
    email: str
    marital_status: str


class ClientActivities:
    retry_policy = RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2,
        maximum_interval=timedelta(seconds=30),
    )

    @staticmethod
    @activity.defn
    async def add_client(new_client: WealthManagementClient) -> str:
        activity.logger.info(f"add_client: {new_client.first_name} {new_client.last_name}")
        account_manager = ClientManager()
        return account_manager.add_client(
            client_id=new_client.client_id,
            first_name=new_client.first_name,
            last_name=new_client.last_name,
            address=new_client.address,
            phone=new_client.phone,
            email=new_client.email,
            marital_status=new_client.marital_status,
        )

    @staticmethod
    @activity.defn
    async def get_client(client_id: str) -> WealthManagementClient | None:
        activity.logger.info(f"get_client: {client_id}")
        client_manager = ClientManager()
        client_dict = client_manager.get_client(client_id=client_id)
        if "error" in client_dict:
            return None
        client_dict["client_id"] = client_id
        return WealthManagementClient(**client_dict)

    @staticmethod
    @activity.defn
    async def update_client(client_id: str, field_dict: dict) -> str:
        activity.logger.info(f"update_client: {field_dict}")
        client_manager = ClientManager()
        return client_manager.update_client(client_id, field_dict)
