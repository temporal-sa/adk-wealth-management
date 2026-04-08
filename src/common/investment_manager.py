import json
import os
from dataclasses import dataclass
import uuid
import argparse

script_dir = os.path.dirname(__file__)
relative_path = '../../data/investments.json'
INVESTMENTS_FILE = os.path.join(script_dir, relative_path)

@dataclass
class InvestmentAccount:
    client_id: str
    name: str
    balance: float

class InvestmentManager:
    def __init__(self, json_file=INVESTMENTS_FILE):
        self.json_file = json_file
        self._load_data()

    def _load_data(self):
        if os.path.exists(self.json_file):
            with open(self.json_file, 'r') as f:
                try:
                    self.data = json.load(f)
                    if not isinstance(self.data, dict):
                        self.data = {}
                except json.JSONDecodeError:
                    self.data = {}
        else:
            self.data = {}

    def _save_data(self):
        with open(self.json_file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def list_investment_accounts(self, client_id):
        if client_id not in self.data:
            return []
        return self.data.get(client_id, [])

    def add_investment_account(self, new_account: InvestmentAccount):
        try:
            if new_account.balance < 0:
                return None
        except ValueError:
            return None

        if new_account.client_id not in self.data:
            self.data[new_account.client_id] = []

        existing_ids = {i['investment_id'] for i in self.data[new_account.client_id]}
        new_investment_id = f"i-{str(uuid.uuid4())[:8]}"
        while new_investment_id in existing_ids:
            new_investment_id = f"i-{str(uuid.uuid4())[:8]}"

        new_investment_account = {
            "investment_id": new_investment_id,
            "name": new_account.name,
            "balance": new_account.balance
        }

        self.data[new_account.client_id].append(new_investment_account)
        self._save_data()
        return new_investment_account

    def delete_investment_account(self, client_id, investment_id):
        if client_id not in self.data:
            return False

        initial_count = len(self.data[client_id])
        self.data[client_id] = [
            inv for inv in self.data[client_id]
            if inv["investment_id"] != investment_id
        ]

        if len(self.data[client_id]) < initial_count:
            if not self.data[client_id]:
                del self.data[client_id]
            self._save_data()
            return True
        return False
