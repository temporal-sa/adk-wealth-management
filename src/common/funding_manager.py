import os

from common.data_store import JsonDocument

script_dir = os.path.dirname(__file__)
relative_path = '../../data/funding_accounts.json'
FUNDING_FILE = os.path.join(script_dir, relative_path)


class InsufficientFundsError(Exception):
    pass


class FundingManager:
    """The client's linked external bank account, used to fund new investment accounts.

    Every movement of money carries a caller-supplied transfer ID and is recorded
    in a ledger, so applying the same transfer twice (e.g. when an activity is
    retried after it already succeeded) only moves the money once.
    """

    def __init__(self, json_file=FUNDING_FILE):
        self.json_file = json_file
        self.document = JsonDocument("funding_accounts", json_file)
        self._load_data()

    def _load_data(self):
        self.data = self.document.load()
        if self.data is None:
            raise FileNotFoundError(self.json_file)
        self.data.setdefault("accounts", {})
        self.data.setdefault("transfers", {})

    def _save_data(self):
        self.document.save(self.data, indent=2)

    def get_account(self, client_id: str) -> dict:
        account = self.data["accounts"].get(client_id)
        if account is None:
            raise KeyError(f"No linked funding account for client {client_id}")
        return account

    def debit(self, client_id: str, amount: float, transfer_id: str) -> dict:
        if transfer_id in self.data["transfers"]:
            return self.data["transfers"][transfer_id]
        account = self.get_account(client_id)
        if account["balance"] < amount:
            raise InsufficientFundsError(
                f"{account['name']} has ${account['balance']:,.2f}, "
                f"which is not enough to transfer ${amount:,.2f}"
            )
        return self._apply(client_id, -amount, transfer_id)

    def credit(self, client_id: str, amount: float, transfer_id: str) -> dict:
        if transfer_id in self.data["transfers"]:
            return self.data["transfers"][transfer_id]
        return self._apply(client_id, amount, transfer_id)

    def _apply(self, client_id: str, delta: float, transfer_id: str) -> dict:
        account = self.get_account(client_id)
        account["balance"] = round(account["balance"] + delta, 2)
        transfer = {"client_id": client_id, "amount": delta, "balance_after": account["balance"]}
        self.data["transfers"][transfer_id] = transfer
        self._save_data()
        return transfer
