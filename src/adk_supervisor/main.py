import asyncio
import logging
import os
import uuid

# google.genai.types emits a logger.warning() when content.text is accessed on a response
# that contains function_call parts. ADK triggers this internally during event routing.
# It is harmless for our usage — suppress it by raising the log level on that logger.
logging.getLogger("google_genai.types").setLevel(logging.ERROR)

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# Add src/ to the path so common imports work
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.agent_constants import (
    BENE_AGENT_NAME, BENE_HANDOFF, BENE_INSTRUCTIONS,
    INVEST_AGENT_NAME, INVEST_HANDOFF, STANDALONE_INVEST_INSTRUCTIONS,
    SUPERVISOR_AGENT_NAME, SUPERVISOR_HANDOFF, SUPERVISOR_INSTRUCTIONS,
)
from common.client_id_memory import remember_client_id
from common.beneficiaries_manager import BeneficiariesManager
from common.investment_manager import InvestmentManager, InvestmentAccount

load_dotenv()

MODEL = "gemini-2.5-flash"
APP_NAME = "wealth-management"
USER_ID = "default-user"

# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def list_beneficiaries(client_id: str) -> list:
    """List all beneficiaries for a given client.

    Args:
        client_id: The unique identifier of the client.

    Returns:
        A list of beneficiary records for the client.
    """
    manager = BeneficiariesManager()
    return manager.list_beneficiaries(client_id)


def add_beneficiary(client_id: str, first_name: str, last_name: str, relationship: str) -> str:
    """Add a new beneficiary for a client.

    Args:
        client_id: The unique identifier of the client.
        first_name: The beneficiary's first name.
        last_name: The beneficiary's last name.
        relationship: The beneficiary's relationship to the client (e.g. spouse, son, daughter).

    Returns:
        A confirmation message.
    """
    manager = BeneficiariesManager()
    manager.add_beneficiary(client_id, first_name, last_name, relationship)
    return f"Beneficiary {first_name} {last_name} added successfully."


def delete_beneficiary(client_id: str, beneficiary_id: str) -> str:
    """Delete a beneficiary for a client.

    Args:
        client_id: The unique identifier of the client.
        beneficiary_id: The unique identifier of the beneficiary to delete.

    Returns:
        A confirmation message.
    """
    manager = BeneficiariesManager()
    manager.delete_beneficiary(client_id, beneficiary_id)
    return f"Beneficiary {beneficiary_id} deleted successfully."


def list_investments(client_id: str) -> list:
    """List all investment accounts for a given client.

    Args:
        client_id: The unique identifier of the client.

    Returns:
        A list of investment account records for the client.
    """
    manager = InvestmentManager()
    return manager.list_investment_accounts(client_id)


def open_investment(client_id: str, account_name: str, initial_balance: float) -> dict:
    """Open a new investment account for a client.

    Args:
        client_id: The unique identifier of the client.
        account_name: The name for the new investment account.
        initial_balance: The opening balance for the account.

    Returns:
        The newly created investment account record, or an error dict.
    """
    manager = InvestmentManager()
    account = InvestmentAccount(
        client_id=client_id,
        name=account_name,
        balance=initial_balance,
    )
    result = manager.add_investment_account(account)
    if result is None:
        return {"error": "Failed to create investment account. Check that the balance is non-negative."}
    return result


def close_investment(client_id: str, investment_id: str) -> str:
    """Close an investment account for a client.

    Args:
        client_id: The unique identifier of the client.
        investment_id: The unique identifier of the investment account to close.

    Returns:
        A confirmation message.
    """
    manager = InvestmentManager()
    success = manager.delete_investment_account(client_id, investment_id)
    if success:
        return f"Investment account {investment_id} closed successfully."
    return f"Investment account {investment_id} not found or could not be closed."


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------
# Same handoffs as the OpenAI version: Supervisor -> Beneficiary or Investment, and each
# back to the Supervisor. The Investment Agent opens accounts itself with open_investment
# (the Temporal version has an Open Account Agent for that). Peer transfers
# (Beneficiary <-> Investment) are disallowed so they go through the Supervisor.
# remember_client_id saves the client ID in session state, which each agent's
# instructions read through {client_id?}, so the customer is asked for it once.

investment_agent = LlmAgent(
    name=INVEST_AGENT_NAME.replace(" ", "_"),
    model=MODEL,
    description=INVEST_HANDOFF,
    instruction=STANDALONE_INVEST_INSTRUCTIONS,
    before_tool_callback=remember_client_id,
    disallow_transfer_to_peers=True,
    tools=[list_investments, open_investment, close_investment],
)

beneficiary_agent = LlmAgent(
    name=BENE_AGENT_NAME.replace(" ", "_"),
    model=MODEL,
    description=BENE_HANDOFF,
    instruction=BENE_INSTRUCTIONS,
    before_tool_callback=remember_client_id,
    disallow_transfer_to_peers=True,
    tools=[list_beneficiaries, add_beneficiary, delete_beneficiary],
)

supervisor_agent = LlmAgent(
    name=SUPERVISOR_AGENT_NAME.replace(" ", "_"),
    model=MODEL,
    description=SUPERVISOR_HANDOFF,
    instruction=SUPERVISOR_INSTRUCTIONS,
    before_tool_callback=remember_client_id,
    sub_agents=[beneficiary_agent, investment_agent],
)


# ---------------------------------------------------------------------------
# CLI main loop
# ---------------------------------------------------------------------------

async def run_conversation():
    """Run an interactive CLI conversation with the wealth management agents."""
    session_service = InMemorySessionService()
    session_id = str(uuid.uuid4())

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    runner = Runner(
        agent=supervisor_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    print("=" * 60)
    print("  Wealth Management Assistant")
    print("=" * 60)
    print("Type your message and press Enter. Type 'exit', 'quit', or 'end' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "end"):
            print("Goodbye!")
            break

        new_message = types.Content(
            role="user",
            parts=[types.Part(text=user_input)],
        )

        response_text = ""
        try:
            async for event in runner.run_async(
                user_id=USER_ID,
                session_id=session.id,
                new_message=new_message,
            ):
                if event.is_final_response():
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                response_text += part.text
        except Exception as e:
            response_text = f"[Error: {e}]"

        print(f"\nAssistant: {response_text}\n")


def main():
    asyncio.run(run_conversation())


if __name__ == "__main__":
    main()
