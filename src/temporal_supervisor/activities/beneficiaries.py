from dataclasses import dataclass
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from common.beneficiaries_manager import BeneficiariesManager


@dataclass
class Beneficiary:
    client_id: str
    first_name: str
    last_name: str
    relationship: str


class Beneficiaries:
    @staticmethod
    @activity.defn
    async def list_beneficiaries(client_id: str) -> list:
        activity.logger.info(f"list_beneficiaries: Listing beneficiaries for {client_id}")
        beneficiaries_mgr = BeneficiariesManager()
        return beneficiaries_mgr.list_beneficiaries(client_id)

    @staticmethod
    @activity.defn
    async def add_beneficiary(new_beneficiary: Beneficiary):
        activity.logger.info(
            f"add_beneficiary: {new_beneficiary.client_id}, "
            f"{new_beneficiary.first_name}, {new_beneficiary.last_name}"
        )
        beneficiaries_mgr = BeneficiariesManager()
        beneficiaries_mgr.add_beneficiary(
            new_beneficiary.client_id,
            new_beneficiary.first_name,
            new_beneficiary.last_name,
            new_beneficiary.relationship,
        )

    @staticmethod
    @activity.defn
    async def delete_beneficiary(client_id: str, beneficiary_id: str):
        activity.logger.info(f"delete_beneficiary: {client_id}, {beneficiary_id}")
        beneficiaries_mgr = BeneficiariesManager()
        beneficiaries_mgr.delete_beneficiary(client_id, beneficiary_id)
