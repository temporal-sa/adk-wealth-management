"""Live test of the agent handoffs, against the real Gemini model.

A customer starts with the Beneficiary Agent, then asks about their investments. The
Beneficiary Agent should hand the conversation back to the supervisor (ADK
transfer_to_agent), which hands it to the Investment Agent, all in the same turn, so the
reply lists the investment accounts. No agent may ask the customer whether they want to be
transferred, or announce the transfer.

The model decides whether to hand off, so this can't be checked with the scripted model. It
runs the real WealthManagementWorkflow with real Gemini calls (through the GoogleAdkPlugin's
invoke_model activity on the LLM task queue, wrapped only to record what each agent said)
and the real beneficiary and investment activities, which only read data/*.json. The Redis
event stream is replaced by in-memory stand-ins, and the chaos flags read as off.

Not part of the default `uv run pytest` run (pytest only collects test_*.py), because it
calls the paid Gemini API. Run it with:
    source ./setgeminikey.sh && LIVE_TEST_RUNS=10 uv run pytest tests/live_agent_handoffs.py -s
LIVE_TEST_RUNS=<n> repeats the conversation n times (default 1), since model output varies.
"""

import asyncio
import os
import re
import uuid
from dataclasses import dataclass, field

import pytest
import temporalio.google_adk._plugin as adk_plugin_module
from google.adk.models import LlmRequest, LlmResponse
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.google_adk import GoogleAdkPlugin
from temporalio.google_adk._model import invoke_model as real_invoke_model
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import temporal_supervisor.chaos as chaos
from common.status_update import StatusUpdate
from common.user_message import ChatInteraction, ProcessUserMessageInput
from temporal_supervisor.activities.beneficiaries import Beneficiaries
from temporal_supervisor.activities.investments import Investments
from temporal_supervisor.model_config import register_chaos_aware_gemini
from temporal_supervisor.task_queues import LLM_TASK_QUEUE
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

RUNS = max(1, int(os.getenv("LIVE_TEST_RUNS", "1")))
TASK_QUEUE = "live-agent-handoffs"
# Client 123's beneficiaries and accounts in data/beneficiaries.json and data/investments.json
CLIENT_123_BENEFICIARIES = ["John", "Jane", "Joan"]
CLIENT_123_ACCOUNTS = ["Checking", "Savings", "401K"]
TRANSFER = re.compile(r"transfer", re.IGNORECASE)
CLIENT_ID_REQUEST = re.compile(r"client id", re.IGNORECASE)

pytestmark = pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set (source ./setgeminikey.sh)")


# --- What each agent said, recorded from the model activity --------------------------------

@dataclass
class AgentOutput:
    agent: str
    text: str = ""
    calls: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        said = f" said {self.text.strip()!r}" if self.text.strip() else ""
        called = f" called {', '.join(self.calls)}" if self.calls else ""
        return f"[{self.agent}]{said}{called}" if said or called else f"[{self.agent}] (empty response)"


model_outputs: list[AgentOutput] = []


@activity.defn(name="invoke_model")
async def recording_invoke_model(llm_request: LlmRequest) -> list[LlmResponse]:
    """The real invoke_model activity, recording the text and tool calls of each agent."""
    # Read the agent name first: the Gemini API client drops the labels from the request
    output = AgentOutput(agent=(llm_request.config.labels or {}).get("adk_agent_name", "?"))
    responses = await real_invoke_model(llm_request)
    for response in responses:
        for part in (response.content.parts or []) if response.content else []:
            if part.text and not part.thought:
                output.text += part.text
            if part.function_call:
                output.calls.append(f"{part.function_call.name}({dict(part.function_call.args or {})})")
    model_outputs.append(output)
    return responses


def recording_plugin() -> GoogleAdkPlugin:
    adk_plugin_module.invoke_model = recording_invoke_model
    try:
        return GoogleAdkPlugin()
    finally:
        adk_plugin_module.invoke_model = real_invoke_model


# --- In-memory stand-ins for the Redis event stream ---------------------------------------

@activity.defn(name="append_chat_interaction")
async def append_chat_interaction(workflow_id: str, chat_interaction: ChatInteraction) -> int:
    return 1


@activity.defn(name="append_status_update")
async def append_status_update(workflow_id: str, status_update: StatusUpdate) -> int:
    return 1


@activity.defn(name="delete_conversation")
async def delete_conversation(workflow_id: str) -> bool:
    return True


async def chaos_off(flag) -> bool:
    return False


# --- The conversation ----------------------------------------------------------------------

@dataclass
class Turn:
    prompt: str
    reply: ChatInteraction
    agents: list[AgentOutput]


async def say(handle: WorkflowHandle, prompt: str) -> Turn:
    turns = len(await handle.query(WealthManagementWorkflow.get_chat_history))
    first_output = len(model_outputs)
    await handle.signal(WealthManagementWorkflow.process_user_message, ProcessUserMessageInput(user_input=prompt))
    for _ in range(1800):  # 3 minutes
        history = await handle.query(WealthManagementWorkflow.get_chat_history)
        if len(history) > turns:
            return Turn(prompt, history[-1], model_outputs[first_output:])
        await asyncio.sleep(0.1)
    raise TimeoutError(f"no reply to {prompt!r}")


def transcript(run: int, turns: list[Turn]) -> str:
    lines = [f"Run {run} of {RUNS}:"]
    for turn in turns:
        lines.append(f"Customer: {turn.prompt}")
        lines.append(f"Agent: {turn.reply.text_response}")
        lines.append(f"  trace: {turn.reply.agent_trace.strip()}")
        lines.extend(f"  model: {output}" for output in turn.agents)
    return "\n".join(lines)


def problems(turns: list[Turn]) -> list[str]:
    """Everything wrong with one run of the conversation."""
    found = []
    beneficiaries, investments = turns[1], turns[2]
    for name in CLIENT_123_BENEFICIARIES:
        if name not in beneficiaries.reply.text_response:
            found.append(f"beneficiary {name} not listed")
    reply = investments.reply.text_response
    for account in CLIENT_123_ACCOUNTS:
        if account not in reply:
            found.append(f"account {account} not listed")
    if TRANSFER.search(reply):
        found.append("the reply talks about a transfer")
    if CLIENT_ID_REQUEST.search(reply):
        found.append("the reply asks for the client ID again")
    # The final reply would hide what an earlier agent said, so check each agent's own output
    for output in investments.agents:
        if output.agent == "Beneficiary_Agent" and TRANSFER.search(output.text):
            found.append(f"the Beneficiary Agent talked about the transfer: {output.text.strip()!r}")
    if not any(o.agent == "Investment_Agent" for o in investments.agents):
        found.append("the turn never reached the Investment Agent")
    if not any(o.agent == "Investment_Agent" and any(c.startswith("list_investments(") for c in o.calls)
               for o in investments.agents):
        found.append("the Investment Agent did not call list_investments")
    return found


def chatter(turns: list[Turn]) -> bool:
    """Any agent said anything about a transfer in the investments turn."""
    return any(TRANSFER.search(o.text) for o in turns[2].agents)


async def test_investment_question_asked_while_talking_to_the_beneficiary_agent(monkeypatch):
    # Without Redis the flags already read as off (chaos.is_enabled fails open), but a local
    # Redis with a flag set would change the result, so turn chaos off explicitly.
    monkeypatch.setattr(chaos, "is_enabled", chaos_off)
    register_chaos_aware_gemini()
    model_outputs.clear()

    async with await WorkflowEnvironment.start_local() as env:
        config = env.client.config()
        config["plugins"] = [recording_plugin()]
        client = Client(**config)
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[WealthManagementWorkflow],
            activities=[Beneficiaries.list_beneficiaries, Investments.list_investments,
                        append_chat_interaction, append_status_update, delete_conversation],
        ), Worker(client, task_queue=LLM_TASK_QUEUE):  # model calls; the plugin adds invoke_model
            failures, chattered = [], 0
            for run in range(1, RUNS + 1):
                handle = await client.start_workflow(
                    WealthManagementWorkflow.run, id=f"live-agent-handoffs-{run}-{uuid.uuid4()}", task_queue=TASK_QUEUE)
                turns = []
                for prompt in ("Who are my beneficiaries?", "123", "What investments do I have?"):
                    turns.append(await say(handle, prompt))
                await handle.signal(WealthManagementWorkflow.end_workflow)
                await handle.result()

                text = transcript(run, turns)
                found = problems(turns)
                chattered += chatter(turns)
                print(f"\n{text}\n  result: {'FAIL: ' + '; '.join(found) if found else 'pass'}"
                      f"{' (transfer chatter)' if chatter(turns) else ''}")
                if found:
                    failures.append(f"{'; '.join(found)}\n{text}")

            print(f"\n{RUNS - len(failures)}/{RUNS} runs passed, {chattered} with transfer chatter")
            assert not failures, f"{len(failures)}/{RUNS} runs failed:\n\n" + "\n\n".join(failures)
