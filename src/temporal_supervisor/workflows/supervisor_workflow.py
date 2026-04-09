import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from common.user_message import ProcessUserMessageInput, ChatInteraction
from common.status_update import StatusUpdate
from common.agent_constants import (
    BENE_AGENT_NAME, BENE_HANDOFF, BENE_INSTRUCTIONS,
    INVEST_AGENT_NAME, INVEST_HANDOFF, INVEST_INSTRUCTIONS,
    SUPERVISOR_AGENT_NAME, SUPERVISOR_HANDOFF, SUPERVISOR_INSTRUCTIONS,
    OPEN_ACCOUNT_AGENT_NAME, OPEN_ACCOUNT_HANDOFF, OPEN_ACCOUNT_INSTRUCTIONS,
)
from common.account_context import UpdateAccountOpeningStateInput
from temporal_supervisor.activities.event_stream_activities import EventStreamActivities

with workflow.unsafe.imports_passed_through():
    from temporal_supervisor.activities.beneficiaries import Beneficiaries, Beneficiary
    from temporal_supervisor.activities.investments import Investments
    from temporal_supervisor.activities.open_account import OpenAccount, open_new_investment_account
    from google.adk.agents import LlmAgent
    from google.adk.events import Event
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from temporalio.contrib.google_adk_agents import TemporalModel
    from temporal_supervisor.activities.activity_tool import _activity_tool


MODEL = "gemini-2.5-flash"
APP_NAME = "wealth-management"


# ---------------------------------------------------------------------------
# ADK tool wrappers
#
# activity_tool() (from temporal_supervisor.activity_tool) handles most
# activities automatically. Manual wrappers are only needed when:
#   - The activity takes a dataclass arg (ADK can't generate a schema for it)
#   - The tool needs custom pre-processing before calling the activity
# ---------------------------------------------------------------------------

async def add_beneficiary(client_id: str, first_name: str, last_name: str, relationship: str) -> None:
    """Add a new beneficiary for a client.

    Args:
        client_id: The client's unique identifier.
        first_name: Beneficiary's first name.
        last_name: Beneficiary's last name.
        relationship: Relationship to the client (e.g. spouse, son, daughter).
    """
    await workflow.execute_activity(
        Beneficiaries.add_beneficiary,
        Beneficiary(client_id=client_id, first_name=first_name, last_name=last_name, relationship=relationship),
        start_to_close_timeout=timedelta(seconds=30),
    )


async def update_client_details(
    workflow_id: str,
    first_name: str = None,
    last_name: str = None,
    address: str = None,
    phone: str = None,
    email: str = None,
    marital_status: str = None,
) -> str:
    """Update one or more fields on the client record during account opening.

    Args:
        workflow_id: The open-account child workflow ID returned by open_new_investment_account.
        first_name: Updated first name (omit to leave unchanged).
        last_name: Updated last name (omit to leave unchanged).
        address: Updated address (omit to leave unchanged).
        phone: Updated phone number (omit to leave unchanged).
        email: Updated email address (omit to leave unchanged).
        marital_status: Updated marital status (omit to leave unchanged).

    Returns:
        Confirmation message.
    """
    client_fields = {
        k: v for k, v in {
            "first_name": first_name,
            "last_name": last_name,
            "address": address,
            "phone": phone,
            "email": email,
            "marital_status": marital_status,
        }.items() if v is not None
    }
    return await workflow.execute_activity(
        OpenAccount.update_client_details,
        args=[workflow_id, client_fields],
        start_to_close_timeout=timedelta(seconds=30),
    )


def init_agents() -> LlmAgent:
    """Initialize ADK agents and return the root supervisor agent."""
    temporal_model = TemporalModel(MODEL)

    open_account_agent = LlmAgent(
        name=OPEN_ACCOUNT_AGENT_NAME.replace(" ", "_"),
        model=temporal_model,
        description=OPEN_ACCOUNT_HANDOFF,
        instruction=OPEN_ACCOUNT_INSTRUCTIONS,
        tools=[
            open_new_investment_account,
            _activity_tool(
                OpenAccount.get_current_client_info,
                start_to_close_timeout=timedelta(seconds=30),
            ),
            _activity_tool(
                OpenAccount.approve_kyc,
                start_to_close_timeout=timedelta(seconds=30),
            ),
            update_client_details,  # wrapper: flat optional args → dict → activity
        ],
    )

    investment_agent = LlmAgent(
        name=INVEST_AGENT_NAME.replace(" ", "_"),
        model=temporal_model,
        description=INVEST_HANDOFF,
        instruction=INVEST_INSTRUCTIONS,
        tools=[
            _activity_tool(
                Investments.list_investments,
                start_to_close_timeout=timedelta(seconds=30),
            ),
            _activity_tool(
                Investments.close_investment,
                start_to_close_timeout=timedelta(seconds=30),
            ),
        ],
        sub_agents=[open_account_agent],
    )

    beneficiary_agent = LlmAgent(
        name=BENE_AGENT_NAME.replace(" ", "_"),
        model=temporal_model,
        description=BENE_HANDOFF,
        instruction=BENE_INSTRUCTIONS,
        tools=[
            _activity_tool(
                Beneficiaries.list_beneficiaries,
                start_to_close_timeout=timedelta(seconds=30),
            ),
            add_beneficiary,  # wrapper: flat args → Beneficiary dataclass → activity
            _activity_tool(
                Beneficiaries.delete_beneficiary,
                start_to_close_timeout=timedelta(seconds=30),
            ),
        ],
    )

    supervisor_agent = LlmAgent(
        name=SUPERVISOR_AGENT_NAME.replace(" ", "_"),
        model=temporal_model,
        description=SUPERVISOR_HANDOFF,
        instruction=SUPERVISOR_INSTRUCTIONS,
        sub_agents=[beneficiary_agent, investment_agent],
    )

    return supervisor_agent


@workflow.defn
class WealthManagementWorkflow:
    def __init__(self):
        self.wf_id = None  # set in run()
        self.pending_chat_messages: asyncio.Queue = asyncio.Queue()
        self.pending_status_updates: asyncio.Queue = asyncio.Queue()
        self.chat_history: list[ChatInteraction] = []
        self.end_workflow_flag = False
        self.sched_to_close_timeout = timedelta(seconds=5)
        self.retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2,
            maximum_interval=timedelta(seconds=30),
        )
        # ADK runner state — initialized in run()
        self._session_service: InMemorySessionService | None = None
        self._runner: Runner | None = None
        self._session_id: str | None = None
        self._user_id: str = "workflow-user"
        self._session_events_data: list = []

    @workflow.run
    async def run(self):
        self.wf_id = workflow.info().workflow_id

        if workflow.info().continued_run_id is None:
            await workflow.execute_local_activity(
                EventStreamActivities.delete_conversation,
                args=[self.wf_id],
                schedule_to_close_timeout=self.sched_to_close_timeout,
                retry_policy=self.retry_policy,
            )

        # Initialize ADK runner inside the workflow.
        # GoogleAdkPlugin configures TemporalModel as the LLM backend so that
        # model calls are routed through Temporal activities (deterministic).
        self._session_service = InMemorySessionService()
        self._session_id = str(workflow.uuid4())
        await self._session_service.create_session(
            app_name=APP_NAME,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        root_agent = init_agents()
        self._runner = Runner(
            agent=root_agent,
            app_name=APP_NAME,
            session_service=self._session_service,
        )

        while True:
            await workflow.wait_condition(
                lambda: (
                    not self.pending_chat_messages.empty()
                    or not self.pending_status_updates.empty()
                    or self.end_workflow_flag
                )
            )

            if self.end_workflow_flag:
                return

            if not self.pending_chat_messages.empty():
                message = self.pending_chat_messages.get_nowait()
                await self._process_chat_message(message)

            if not self.pending_status_updates.empty():
                status_message = self.pending_status_updates.get_nowait()
                await self._process_status_update(status_message)

            if workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(lambda: workflow.all_handlers_finished())
                workflow.continue_as_new(args=[])

    async def _process_chat_message(self, message: str):
        chat_interaction = ChatInteraction(user_prompt=message, text_response="")
        await self._process_user_message(chat_interaction, message)
        self.chat_history.append(chat_interaction)

        await workflow.execute_local_activity(
            EventStreamActivities.append_chat_interaction,
            args=[self.wf_id, chat_interaction],
            schedule_to_close_timeout=self.sched_to_close_timeout,
            retry_policy=self.retry_policy,
        )

    async def _process_status_update(self, status_message: str):
        status_update = StatusUpdate(status=status_message)
        await workflow.execute_local_activity(
            EventStreamActivities.append_status_update,
            args=[self.wf_id, status_update],
            schedule_to_close_timeout=self.sched_to_close_timeout,
            retry_policy=self.retry_policy,
        )

    async def _process_user_message(self, chat_interaction: ChatInteraction, message: str):
        # Restore session events lost due to InMemorySessionService not surviving
        # Temporal workflow replay between signals.
        session = await self._session_service.get_session(
            app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id
        )
        if session is None:
            session = await self._session_service.create_session(
                app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
        state_keys = list(session.state.keys()) if session.state else []
        workflow.logger.info(
            f"[session-debug] message='{message[:40]}' "
            f"session_events={len(session.events)} "
            f"state_keys={state_keys} "
            f"session_events_data={len(self._session_events_data)}"
        )
        if not session.events and self._session_events_data:
            for e_data in self._session_events_data:
                try:
                    event = Event.model_validate(e_data)
                    session.events.append(event)
                except Exception as exc:
                    workflow.logger.warning(f"Failed to restore session event: {exc}")
            workflow.logger.info(f"[session-debug] restored {len(session.events)} events")

        new_message = types.Content(
            role="user",
            parts=[types.Part(text=message)],
        )

        text_response = ""
        agent_trace = ""
        json_response = ""

        async for event in self._runner.run_async(
            user_id=self._user_id,
            session_id=self._session_id,
            new_message=new_message,
        ):
            func_calls = event.get_function_calls()
            func_responses = event.get_function_responses()

            # Track agent activity for trace
            if event.author:
                agent_trace += f"[{event.author}] "

            # Collect tool call info
            if func_calls:
                for fc in func_calls:
                    agent_trace += f"calling tool: {fc.name}\n"

            if func_responses:
                for fr in func_responses:
                    json_response += str(fr.response) + "\n"

            # Collect text response
            if event.content and event.content.parts and not func_calls:
                for i, part in enumerate(event.content.parts):
                    part_text = getattr(part, "text", None)
                    part_thought = getattr(part, "thought", None)
                    workflow.logger.info(
                        f"[event-part] author={event.author!r} part[{i}]: "
                        f"text={part_text!r} thought={part_thought}"
                    )
                    if part_text:
                        text_response += part_text
            elif not event.content:
                workflow.logger.info(f"[event-part] author={event.author!r} NO CONTENT")
            elif not event.content.parts:
                workflow.logger.info(f"[event-part] author={event.author!r} EMPTY PARTS")

        workflow.logger.info(
            f"[session-debug] run_async done: text_response='{text_response[:80]}' "
            f"agent_trace='{agent_trace[:80]}'"
        )

        # Persist session events so they survive the next Temporal replay.
        session_after = await self._session_service.get_session(
            app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id
        )
        if session_after and session_after.events:
            try:
                self._session_events_data = [
                    e.model_dump(mode="json") for e in session_after.events
                ]
                workflow.logger.info(f"[session-debug] saved {len(self._session_events_data)} session events")
            except Exception as exc:
                workflow.logger.warning(f"Failed to serialize session events: {exc}")

        chat_interaction.text_response = text_response
        chat_interaction.json_response = json_response
        chat_interaction.agent_trace = agent_trace

    @workflow.query
    def get_chat_history(self) -> list[ChatInteraction]:
        return self.chat_history

    @workflow.signal
    async def end_workflow(self):
        self.end_workflow_flag = True

    @workflow.signal
    async def update_account_opening_state(
        self, state_input: UpdateAccountOpeningStateInput
    ):
        status_message = (
            f"New {state_input.account_name} account status changed: {state_input.state}"
        )
        await self.pending_status_updates.put(status_message)

    @workflow.signal
    async def process_user_message(self, message_input: ProcessUserMessageInput):
        workflow.logger.info(f"processing user message {message_input}")
        await self.pending_chat_messages.put(message_input.user_input)
