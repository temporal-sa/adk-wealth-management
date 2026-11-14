import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import TemporalError
from temporalio.google_adk import GoogleAdkPlugin
from temporalio.service import RPCError

from common.chaos_manager import CHAOS_FLAGS, ChaosFlag, ChaosManager
from common.event_stream_manager import EventStreamManager
from common.client_helper import ClientHelper
from common.redis_config import RedisConfig
from common.session_scope import is_session_id, new_chat_workflow_id, new_session_id, owns_workflow, scope
from common.user_message import ProcessUserMessageInput
from temporal_supervisor.claim_check.claim_check_plugin import ClaimCheckPlugin
from temporal_supervisor.workflows.supervisor_workflow import WealthManagementWorkflow

temporal_client: Optional[Client] = None
task_queue: Optional[str] = None

SESSION_COOKIE = "wm_session"
# The state an account opening (OpenInvestmentAccountWorkflow) waits in for the compliance approval
WAITING_COMPLIANCE_REVIEW = "Waiting Compliance Review"
# The built web app (src/frontend/build); served at / when present, e.g. in the container image.
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[1] / "frontend" / "build"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    print("API is starting up...")
    global temporal_client
    global task_queue
    client_helper = ClientHelper()
    task_queue = client_helper.taskQueue
    temporal_client = await Client.connect(
        **client_helper.client_config,
        plugins=[
            GoogleAdkPlugin(),
            ClaimCheckPlugin(),
        ]
    )
    yield
    print("API is shutting down...")

app = FastAPI(lifespan=lifespan)

# Only needed when the web app is served from another origin than the API. Locally the React
# dev server proxies /api to the API, and in the container the API serves the web app itself.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def session_id(request: Request, response: Response) -> str:
    """The caller's browser session, from its cookie; a new one if it has none.

    The session scopes everything one person does: their chat workflows, their chaos
    switches and their copy of the sample bank data (see common.session_scope).
    """
    current = request.cookies.get(SESSION_COOKIE)
    if is_session_id(current):
        return current
    current = new_session_id()
    response.set_cookie(
        SESSION_COOKIE,
        current,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return current


def owned_workflow_id(workflow_id: str, session: str = Depends(session_id)) -> str:
    """The workflow_id of a request, if it is one of the caller's own chats."""
    if not owns_workflow(session, workflow_id):
        raise HTTPException(status_code=404, detail="No such chat in this session")
    return workflow_id


api = APIRouter()


@api.get("/health")
async def health():
    """Cheap readiness check: the API is up and can reach Redis."""
    client = RedisConfig().async_client()
    try:
        await asyncio.wait_for(client.ping(), timeout=2)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")
    finally:
        await client.aclose()
    return {"status": "ok"}


@api.get("/get-chat-history")
async def get_chat_history(workflow_id: str = Depends(owned_workflow_id), from_index: int = Query(0)):
    try:
        history = await EventStreamManager().get_events_from_index(workflow_id=workflow_id, from_index=from_index)
        if history is None:
            return ""
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@api.post("/send-prompt")
async def send_prompt(prompt: str, workflow_id: str = Depends(owned_workflow_id)):
    message = ProcessUserMessageInput(user_input=prompt)
    try:
        handle = temporal_client.get_workflow_handle(workflow_id=workflow_id)
        await handle.signal(WealthManagementWorkflow.process_user_message, args=[message])
        response = "Message sent"
    except RPCError as e:
        response = f"Error: {e}"
    return {"response": response}

@api.post("/end-chat")
async def end_chat(workflow_id: str = Depends(owned_workflow_id)):
    try:
        handle = temporal_client.get_workflow_handle(workflow_id=workflow_id)
        await handle.signal("end_workflow")
        return {"message": "End chat signal sent."}
    except TemporalError as e:
        return {}

@api.post("/approve-compliance")
async def approve_compliance(workflow_id: str = Depends(owned_workflow_id)):
    """Play the compliance officer: approve this chat's account openings waiting for review.

    Sends compliance_approved to each of the chat's open-account child workflows in
    "Waiting Compliance Review", as run_send_compliance_approval.py does from a terminal.
    """
    try:
        states = await temporal_client.get_workflow_handle(workflow_id).query(
            WealthManagementWorkflow.get_account_opening_states
        )
        waiting = [
            child_id
            for child_id, state in states.items()
            if state == WAITING_COMPLIANCE_REVIEW and child_id.startswith(f"OpenAccount-{workflow_id}-")
        ]
        for child_id in waiting:
            await temporal_client.get_workflow_handle(child_id).signal("compliance_approved")
        return {"approved": waiting}
    except (RPCError, TemporalError) as e:
        raise HTTPException(status_code=502, detail=f"Could not approve: {e}")


@api.post("/start-workflow")
async def start_workflow(session: str = Depends(session_id)):
    """Start a new chat for the caller's session. The API picks the workflow ID."""
    workflow_id = new_chat_workflow_id(session)
    try:
        await temporal_client.start_workflow(
            WealthManagementWorkflow.run,
            args=[],
            id=workflow_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE
        )
        return {"message": f"Workflow started.", "workflow_id": workflow_id}
    except Exception as e:
        return {"message": f"An error occurred starting the workflow {e}"}


@api.get("/chaos")
async def get_chaos(session: str = Depends(session_id)):
    """This session's failure-injection flags for the UI chaos panel, plus the failures they recently caused."""
    manager = ChaosManager(scope(session))
    try:
        enabled = await manager.get_flags()
        return {
            "flags": [
                {
                    "flag": info.flag.value,
                    "label": info.label,
                    "description": info.description,
                    "enabled": enabled[info.flag.value],
                }
                for info in CHAOS_FLAGS
            ],
            "events": await manager.get_events(),
        }
    finally:
        await manager.close()


@api.post("/chaos")
async def set_chaos(flag: str, enabled: bool, session: str = Depends(session_id)):
    try:
        chaos_flag = ChaosFlag(flag)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown chaos flag: {flag}")
    manager = ChaosManager(scope(session))
    try:
        await manager.set_enabled(chaos_flag, enabled)
        return {"flag": flag, "enabled": enabled}
    finally:
        await manager.close()


@api.post("/chaos/reset")
async def reset_chaos(session: str = Depends(session_id)):
    """Turn every flag of this session off and clear its failure log."""
    manager = ChaosManager(scope(session))
    try:
        for info in CHAOS_FLAGS:
            await manager.set_enabled(info.flag, False)
        await manager.clear_events()
        return {"message": "All failure injection turned off."}
    finally:
        await manager.close()


app.include_router(api, prefix="/api")

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return {"message": "Google ADK + Temporal Agent!"}
