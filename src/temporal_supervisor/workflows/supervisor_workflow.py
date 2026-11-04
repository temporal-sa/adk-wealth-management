import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityConfig

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
    from temporalio.google_adk import TemporalModel
    from temporal_supervisor.activities.activity_tool import _activity_tool
    from temporal_supervisor.retry_policies import LLM_RETRY_POLICY, TOOL_RETRY_POLICY
    from temporal_supervisor.task_queues import LLM_TASK_QUEUE


MODEL = "gemini-2.5-flash"
APP_NAME = "wealth-management"
CLIENT_ID_STATE_KEY = "client_id"
# Sent when the model ends a turn with no text and no tool call, which Gemini occasionally does
EMPTY_RESPONSE_NUDGE = "Please continue, following your instructions."
EMPTY_RESPONSE_FALLBACK = "Sorry, I didn't catch that. Could you please say it again?"


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
        retry_policy=TOOL_RETRY_POLICY,
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
        retry_policy=TOOL_RETRY_POLICY,
    )


def remember_client_id(tool, args: dict, tool_context) -> None:
    """before_tool_callback: save the client ID in session state the first time a tool receives it.

    Every agent's instructions read it back through the {client_id?} placeholder, so the
    customer is asked for it once, no matter how many agent handoffs follow.
    """
    client_id = args.get("client_id")
    if client_id and tool_context.state.get(CLIENT_ID_STATE_KEY) != client_id:
        tool_context.state[CLIENT_ID_STATE_KEY] = client_id
    return None


async def get_account_opening_status(workflow_id: str) -> str:
    """Get the current status of an account opening.

    Args:
        workflow_id: The workflow ID returned by open_new_investment_account.

    Returns:
        The latest status the account opening reported. "Complete" means the account
        was opened; a status starting with "Rolled back" means it was not opened.
        Either way, that account opening is finished.
    """
    # Runs in the workflow, so it reads the states the open-account workflows reported
    state = workflow.instance().account_opening_states.get(workflow_id)
    if state is None:
        return f"No account opening with workflow ID {workflow_id} was found in this conversation."
    if state == "Complete" or state.startswith("Rolled back"):
        # The model has read a finished opening's status as the answer to a new request
        # to open the same account, so say what to do in that case.
        return (f"{state}\nThis account opening is finished. If the customer is asking to open an "
                "account now, call open_new_investment_account to start a new account opening.")
    return state


# Fields that must appear in the agent's reply for the client details to count as shown
KYC_DISPLAYED_FIELDS = ("first_name", "last_name", "email")


async def get_current_client_info(workflow_id: str) -> dict:
    """Retrieve the customer's current details for an account opening, to show them for KYC.

    Args:
        workflow_id: The workflow ID returned by open_new_investment_account.

    Returns:
        The customer's current details. Show all of them to the customer and ask whether they are correct.
    """
    details = await workflow.execute_activity(
        OpenAccount.get_current_client_info,
        workflow_id,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=TOOL_RETRY_POLICY,
    )
    workflow.instance().kyc_details_fetched[workflow_id] = details
    return details


async def approve_kyc(workflow_id: str) -> str:
    """Approve KYC for an account opening, after the customer confirmed their details are correct.

    Args:
        workflow_id: The workflow ID returned by open_new_investment_account.

    Returns:
        Whether KYC was approved and, if not, what to do first.
    """
    # The model sometimes approves KYC without showing the customer their details, or
    # treats a confirmation for an earlier account opening as approval for a new one.
    # Only approve once this opening's details were shown in a reply and the customer
    # has answered since.
    wf = workflow.instance()
    shown_turn = wf.kyc_details_shown_turn.get(workflow_id)
    if shown_turn is None or shown_turn >= wf.turn:
        return (
            "KYC was not approved. First show the customer all of their current details for this "
            "account opening (get_current_client_info) and ask whether they are correct. Approve KYC "
            "only after the customer confirms in their next message."
        )
    await workflow.execute_activity(
        OpenAccount.approve_kyc,
        workflow_id,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=TOOL_RETRY_POLICY,
    )
    return "KYC approved. The account is now waiting for compliance review."


def _details_displayed(details: dict | None, reply: str) -> bool:
    if not details:
        return False
    reply = reply.lower()
    values = [str(details.get(field) or "") for field in KYC_DISPLAYED_FIELDS]
    return all(value.lower() in reply for value in values if value)


def _model(agent_name: str) -> TemporalModel:
    """Gemini, called in an activity on the LLM task queue, retried until the model responds."""
    return TemporalModel(
        MODEL,
        activity_config=ActivityConfig(
            summary=agent_name,
            task_queue=LLM_TASK_QUEUE,
            retry_policy=LLM_RETRY_POLICY,
        ),
    )


def init_agents() -> LlmAgent:
    """Initialize ADK agents and return the root supervisor agent."""

    open_account_agent = LlmAgent(
        name=OPEN_ACCOUNT_AGENT_NAME.replace(" ", "_"),
        model=_model(OPEN_ACCOUNT_AGENT_NAME),
        description=OPEN_ACCOUNT_HANDOFF,
        instruction=OPEN_ACCOUNT_INSTRUCTIONS,
        before_tool_callback=remember_client_id,
        tools=[
            open_new_investment_account,
            get_account_opening_status,
            get_current_client_info,  # wrapper: records the details for the KYC guard
            approve_kyc,  # wrapper: KYC guard
            update_client_details,  # wrapper: flat optional args → dict → activity
        ],
    )

    investment_agent = LlmAgent(
        name=INVEST_AGENT_NAME.replace(" ", "_"),
        model=_model(INVEST_AGENT_NAME),
        description=INVEST_HANDOFF,
        instruction=INVEST_INSTRUCTIONS,
        before_tool_callback=remember_client_id,
        tools=[
            _activity_tool(
                Investments.list_investments,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY_POLICY,
            ),
            _activity_tool(
                Investments.close_investment,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY_POLICY,
            ),
        ],
        sub_agents=[open_account_agent],
    )

    beneficiary_agent = LlmAgent(
        name=BENE_AGENT_NAME.replace(" ", "_"),
        model=_model(BENE_AGENT_NAME),
        description=BENE_HANDOFF,
        instruction=BENE_INSTRUCTIONS,
        before_tool_callback=remember_client_id,
        tools=[
            _activity_tool(
                Beneficiaries.list_beneficiaries,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY_POLICY,
            ),
            add_beneficiary,  # wrapper: flat args → Beneficiary dataclass → activity
            _activity_tool(
                Beneficiaries.delete_beneficiary,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY_POLICY,
            ),
        ],
    )

    supervisor_agent = LlmAgent(
        name=SUPERVISOR_AGENT_NAME.replace(" ", "_"),
        model=_model(SUPERVISOR_AGENT_NAME),
        description=SUPERVISOR_HANDOFF,
        instruction=SUPERVISOR_INSTRUCTIONS,
        before_tool_callback=remember_client_id,
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
        # Latest state reported by each open-account child workflow, keyed by its workflow ID.
        # Read by the get_account_opening_status tool.
        self.account_opening_states: dict[str, str] = {}
        # KYC guard (see approve_kyc): the number of the customer message being handled, the
        # client details fetched for each account opening this turn, and the turn in which
        # they were shown to the customer, keyed by the opening's workflow ID.
        self.turn = 0
        self.kyc_details_fetched: dict[str, dict] = {}
        self.kyc_details_shown_turn: dict[str, int] = {}
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
    async def run(
        self,
        account_opening_states: dict[str, str] | None = None,
        client_id: str | None = None,
    ):
        self.wf_id = workflow.info().workflow_id
        # Carried over from the previous run on continue-as-new. A signal may already
        # have been handled before run started, so don't overwrite newer states.
        for opening_id, state in (account_opening_states or {}).items():
            self.account_opening_states.setdefault(opening_id, state)

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
            state={CLIENT_ID_STATE_KEY: client_id} if client_id else None,
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
                session = await self._session_service.get_session(
                    app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id
                )
                client_id = session.state.get(CLIENT_ID_STATE_KEY) if session else None
                workflow.continue_as_new(args=[self.account_opening_states, client_id])

    async def _process_chat_message(self, message: str):
        self.turn += 1
        self.kyc_details_fetched = {}
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

        text_response, agent_trace, json_response = await self._run_agents(message)
        if not text_response:
            # The model ended the turn without saying anything. Nudge it once, then fall back
            # to a canned reply rather than showing the customer an empty message.
            workflow.logger.warning("[empty-response] model returned no text; nudging once")
            text_response, retry_trace, retry_json = await self._run_agents(EMPTY_RESPONSE_NUDGE)
            agent_trace += retry_trace
            json_response += retry_json
            if not text_response:
                workflow.logger.warning("[empty-response] still no text after nudge; using fallback")
                text_response = EMPTY_RESPONSE_FALLBACK

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

        # Details only count as shown if the reply the customer sees actually contains them
        for opening_id, details in self.kyc_details_fetched.items():
            if _details_displayed(details, text_response):
                self.kyc_details_shown_turn[opening_id] = self.turn

        chat_interaction.text_response = text_response
        chat_interaction.json_response = json_response
        chat_interaction.agent_trace = agent_trace

    async def _run_agents(self, message: str) -> tuple[str, str, str]:
        """Run one turn of the agents. Returns (text_response, agent_trace, json_response)."""
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
        return text_response, agent_trace, json_response

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
        if state_input.workflow_id:
            self.account_opening_states[state_input.workflow_id] = state_input.state
        status_message = (
            f"New {state_input.account_name} account status changed: {state_input.state}"
        )
        await self.pending_status_updates.put(status_message)

    @workflow.signal
    async def process_user_message(self, message_input: ProcessUserMessageInput):
        workflow.logger.info(f"processing user message {message_input}")
        await self.pending_chat_messages.put(message_input.user_input)
