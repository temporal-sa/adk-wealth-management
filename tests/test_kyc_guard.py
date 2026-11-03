"""The approve_kyc tool only approves KYC once the customer has seen their details for
that account opening in a reply, and has answered since.

Runs the real WealthManagementWorkflow with a scripted model (see chat_harness.py);
the stub approve_kyc activity records which account openings KYC was approved for.

Requires the Temporal dev server (downloaded automatically on first run).
No GEMINI_API_KEY or Redis needed.
"""

import pytest

import tests.scripted_model as model
from tests.chat_harness import CLIENT_DETAILS, end_chat, kyc_approvals, say, start_chat
from tests.scripted_model import text, tool, transfer

pytestmark = pytest.mark.usefixtures("scripted")

OPENING = "OpenAccount-test-123-Boat"
CAR = "OpenAccount-test-123-Car"
DETAILS_REPLY = (
    f"First Name: {CLIENT_DETAILS['first_name']}\nLast Name: {CLIENT_DETAILS['last_name']}\n"
    f"Email: {CLIENT_DETAILS['email']}\nIs this information correct?"
)
NOT_APPROVED = "KYC was not approved"


def to_open_account_agent():
    model.expect("Supervisor_Agent", transfer("Investment_Agent"))
    model.expect("Investment_Agent", transfer("Open_Account_Agent"))


async def test_kyc_is_approved_after_the_customer_confirms_the_details_they_were_shown(client):
    handle = await start_chat(client)
    to_open_account_agent()
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text(DETAILS_REPLY))
    await say(handle, "Open a Boat account with $5,000")

    model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text("Thanks, KYC is approved."))
    turn = await say(handle, "Yes, that's correct")

    assert kyc_approvals == [OPENING]
    assert "KYC approved" in turn.json_response
    await end_chat(handle)


async def test_kyc_is_not_approved_when_the_details_were_fetched_but_not_shown(client):
    # The failure seen with Gemini: the agent fetched the details, didn't show them,
    # and approved KYC on the customer's next "yes".
    handle = await start_chat(client)
    to_open_account_agent()
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text("I have started opening your Boat account."))
    await say(handle, "Open a Boat account with $5,000")

    model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text(DETAILS_REPLY))
    turn = await say(handle, "Yes, correct")

    assert kyc_approvals == []
    assert NOT_APPROVED in turn.json_response

    # Once the details have been shown, the next confirmation approves KYC
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text(DETAILS_REPLY))
    await say(handle, "Sorry, show me my details")
    model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text("Thanks, KYC is approved."))
    await say(handle, "Yes, those are correct")

    assert kyc_approvals == [OPENING]
    await end_chat(handle)


async def test_kyc_is_not_approved_in_the_same_turn_the_details_are_shown(client):
    handle = await start_chat(client)
    to_open_account_agent()
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=OPENING))
    model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text(DETAILS_REPLY))
    turn = await say(handle, "Open a Boat account with $5,000")

    assert kyc_approvals == []
    assert NOT_APPROVED in turn.json_response
    await end_chat(handle)


async def test_a_confirmation_for_one_opening_does_not_approve_another(client):
    handle = await start_chat(client)
    to_open_account_agent()
    model.expect("Open_Account_Agent", tool("get_current_client_info", workflow_id=CAR))
    model.expect("Open_Account_Agent", text(DETAILS_REPLY))
    await say(handle, "Open a Car account with $5,000")

    # The customer confirms, but the agent approves a different opening it never showed
    model.expect("Open_Account_Agent", tool("approve_kyc", workflow_id=OPENING))
    model.expect("Open_Account_Agent", text("Let me show you your details first."))
    turn = await say(handle, "Yes")

    assert kyc_approvals == []
    assert NOT_APPROVED in turn.json_response
    await end_chat(handle)
