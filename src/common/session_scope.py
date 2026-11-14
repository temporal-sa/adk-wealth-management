"""Per-browser-session scoping for the demo's state in Redis.

Many people use a hosted copy of this demo at once, so each browser session gets its own
chaos switches, failure log and sample bank data. The API gives every browser a random
session ID (a cookie) and starts chat workflows with IDs that carry it:

    <task queue>-session-<32 hex chars>-chat-<16 hex chars>

The open-account child workflows' IDs start with their parent's ID, so the activities of a
chat and of its account openings can all find the session from activity.info().workflow_id.
A workflow started some other way (a test, a script) has no session in its ID and uses the
"default" session.

Every key is also prefixed with the task queue (TEMPORAL_TASK_QUEUE), so separate
deployments or workers sharing one Redis never see each other's state.
"""

import os
import re
import secrets
from typing import Optional

DEFAULT_SESSION = "default"
# Session state in Redis (chaos switches, failure log, sample data) expires this long after
# its last change, so abandoned sessions don't pile up.
SESSION_TTL_SECONDS = 24 * 60 * 60

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SESSION_IN_WORKFLOW_ID_RE = re.compile(r"-session-([0-9a-f]{32})-chat-")


def task_queue() -> str:
    # Same variable and default as common.client_helper.ClientHelper
    return os.getenv("TEMPORAL_TASK_QUEUE", "Supervisor")


def new_session_id() -> str:
    return secrets.token_hex(16)


def is_session_id(value: Optional[str]) -> bool:
    return bool(value) and bool(_SESSION_ID_RE.match(value))


def chat_workflow_prefix(session_id: str) -> str:
    return f"{task_queue()}-session-{session_id}-chat-"


def new_chat_workflow_id(session_id: str) -> str:
    return chat_workflow_prefix(session_id) + secrets.token_hex(8)


def owns_workflow(session_id: str, workflow_id: str) -> bool:
    """True if workflow_id is one of this session's chats."""
    prefix = chat_workflow_prefix(session_id)
    return workflow_id.startswith(prefix) and len(workflow_id) > len(prefix)


def session_from_workflow_id(workflow_id: Optional[str]) -> str:
    match = _SESSION_IN_WORKFLOW_ID_RE.search(workflow_id or "")
    return match.group(1) if match else DEFAULT_SESSION


def scope(session_id: str = DEFAULT_SESSION) -> str:
    """The prefix of one session's keys in Redis."""
    return f"{task_queue()}:{session_id}"


def current_activity_scope() -> str:
    """The session scope of the workflow the running activity belongs to."""
    from temporalio import activity

    if not activity.in_activity():
        return scope()
    return scope(session_from_workflow_id(activity.info().workflow_id))
