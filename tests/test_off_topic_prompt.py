"""Off-topic questions are declined by the supervisor's instructions; there is no guardrail code."""

from pathlib import Path

from common.agent_constants import SUPERVISOR_INSTRUCTIONS

DECLINE_BLOCK = """You only help with wealth management: the customer's beneficiaries and investment accounts.
If the customer asks about anything else (for example general knowledge, weather, news, or
other financial products), politely decline, say that you can only help with beneficiaries
and investment accounts, and do not answer the question. Then ask how you can help with
their beneficiaries or investment accounts."""

SRC = Path(__file__).resolve().parent.parent / "src"


def test_supervisor_instructions_decline_off_topic_questions():
    assert DECLINE_BLOCK in SUPERVISOR_INSTRUCTIONS
    # Placed right after the first line, before the routine
    first_line, rest = SUPERVISOR_INSTRUCTIONS.split("\n", 1)
    assert rest.startswith(DECLINE_BLOCK)
    assert rest.index(DECLINE_BLOCK) < rest.index("# Routine")


def test_no_guardrail_code_remains():
    for path in SRC.rglob("*.py"):
        text = path.read_text().lower()
        for symbol in ("guardrail", "tripwire", "routing_instructions"):
            assert symbol not in text, f"{symbol} found in {path}"
