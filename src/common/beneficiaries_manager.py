import json
import os
import uuid
import logging
from typing import List, Dict, Any

script_dir = os.path.dirname(__file__)
relative_path = '../../data/beneficiaries.json'
BENEFICIARIES_FILE = os.path.join(script_dir, relative_path)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class BeneficiariesManager:
    def __init__(self, file_path: str = BENEFICIARIES_FILE):
        self.file_path = file_path

    def _load_data(self) -> Dict[str, List[Dict[str, Any]]]:
        if not os.path.exists(self.file_path) or os.stat(self.file_path).st_size == 0:
            return {}
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
        except Exception as e:
            logger.error(f"Error loading data from '{self.file_path}': {e}")
            return {}

    def _save_data(self, data: Dict[str, List[Dict[str, Any]]]):
        try:
            with open(self.file_path, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving data to '{self.file_path}': {e}")

    def list_beneficiaries(self, client_id: str):
        data = self._load_data()
        return data.get(client_id, [])

    def add_beneficiary(self, client_id: str, first_name: str, last_name: str, relationship: str) -> None:
        data = self._load_data()
        if client_id not in data:
            data[client_id] = []
        existing_ids = {b['beneficiary_id'] for b in data[client_id]}
        new_id = f"b-{str(uuid.uuid4())[:8]}"
        while new_id in existing_ids:
            new_id = f"b-{str(uuid.uuid4())[:8]}"
        new_beneficiary = {
            "beneficiary_id": new_id,
            "first_name": first_name,
            "last_name": last_name,
            "relationship": relationship
        }
        data[client_id].append(new_beneficiary)
        self._save_data(data)

    def delete_beneficiary(self, client_id: str, beneficiary_id: str) -> None:
        data = self._load_data()
        if client_id not in data or not data[client_id]:
            return
        original_count = len(data[client_id])
        data[client_id] = [b for b in data[client_id] if b['beneficiary_id'] != beneficiary_id]
        if len(data[client_id]) < original_count:
            self._save_data(data)
