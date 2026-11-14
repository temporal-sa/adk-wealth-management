import argparse
import json
import os
from datetime import datetime

from common.data_store import JsonDocument

script_dir = os.path.dirname(os.path.abspath(__file__))
clients_relative_path = '../../data/clients.json'
CLIENTS_FILE = os.path.join(script_dir, clients_relative_path)

class ClientManager:
    def __init__(self, file_path: str = CLIENTS_FILE):
        self.file_path = file_path
        self.document = JsonDocument("clients", file_path)

    def _load_clients(self) -> dict:
        return self.document.load() or {}

    def get_client(self, client_id: str) -> dict:
        try:
            print(f"looking for client {client_id} in {self.file_path}")
            clients = self._load_clients()
            print(f"clients is {clients}")
            return clients.get(client_id, {"error": f"Client {client_id} not found"})
        except Exception as e:
            return {"error": f"Exception occurred while retrieving Client {client_id} error: {e}"}

    def add_client(self, client_id: str, first_name: str,
                   last_name: str, address: str, phone: str,
                   email: str, marital_status: str) -> str:
        try:
            clients = self._load_clients()
            if client_id in clients:
                return "Client already exists"
            new_client = {
                "first_name": first_name,
                "last_name": last_name,
                "address": address,
                "phone": phone,
                "email": email,
                "marital_status": marital_status,
            }
            clients[client_id] = new_client
            self.document.save(clients)
            return f"Client {client_id} added"
        except Exception as e:
            return f"Exception occurred while adding Client {client_id} error: {e}"

    def update_client(self, client_id: str, new_info: dict) -> str:
        try:
            clients = self._load_clients()
            if client_id in clients:
                clients[client_id].update(new_info)
                self.document.save(clients)
                return "Client information successfully updated"
            return f"Client {client_id} not found"
        except Exception as e:
            return f"Exception occurred while updating client {client_id} error: {e}"
