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


def _all_agents(root):
    yield root
    for sub in root.sub_agents:
        yield from _all_agents(sub)


def test_every_agent_starts_with_handoff_prefix():
    """Every agent can transfer (to a sub-agent or back to its parent), so every instruction starts
    with HANDOFF_PROMPT_PREFIX, and the supervisor's decline block follows it unchanged."""
    from adk_supervisor import main as standalone
    from common.agent_constants import HANDOFF_PROMPT_PREFIX
    from temporal_supervisor.workflows.supervisor_workflow import init_agents

    assert "{" not in HANDOFF_PROMPT_PREFIX  # would be read as an ADK session-state placeholder
    assert "transfer_to_agent" in HANDOFF_PROMPT_PREFIX
    for root, count in ((init_agents(), 4), (standalone.supervisor_agent, 3)):
        agents = list(_all_agents(root))
        assert len(agents) == count
        for agent in agents:
            assert agent.instruction.startswith(HANDOFF_PROMPT_PREFIX), agent.name
        assert root.instruction == HANDOFF_PROMPT_PREFIX + SUPERVISOR_INSTRUCTIONS
