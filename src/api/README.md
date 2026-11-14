# API to Google ADK + Temporal Workflow

A [FastAPI](https://fastapi.tiangolo.com/) app (`main.py`) that the [web application](../frontend/README.md)
uses to talk to Temporal. It starts the chat workflow (`WealthManagementWorkflow`), sends it prompts as
signals, reads the chat history and status updates from Redis, and turns the chaos panel's flags on and off.

## Prerequisites

* [uv](https://docs.astral.sh/uv/) and Python 3.14 or later, with `uv sync` run in the project root
* Temporal running (locally with `temporal server start-dev`, or Temporal Cloud)
* Redis running (chat history, status updates and chaos flags)
* The worker running (see [Running the Demo](../temporal_supervisor/README.md#running-the-demo))

## Start the API Server

The API listens on http://127.0.0.1:8000 (uvicorn's default) and reloads when the code changes. The start
scripts load `setclaimcheck.sh` from the project root if it exists, so the API uses the same claim check
setting as the worker.

### Connect locally
```bash
cd src/api
./startlocalapi.sh
```

### Connect to Temporal Cloud
Create `setcloudenv.sh` in the project root first (see [Temporal Cloud](../temporal_supervisor/README.md#temporal-cloud)).
```bash
cd src/api
./startcloudapi.sh
```

The API reads the same environment variables as the worker (`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
`TEMPORAL_TASK_QUEUE`, `REDIS_URL` or `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD`, `USE_CLAIM_CHECK`, ...); see
[Configuration](../temporal_supervisor/README.md#configuration). It starts chat workflows on `TEMPORAL_TASK_QUEUE`
(default `Supervisor`), so it must match the worker's.

The web app calls the API on its own origin: the React dev server proxies `/api` to `http://127.0.0.1:8000`, and
in the container image the API serves the built web app at `/` itself (from `src/frontend/build` when it exists,
or `FRONTEND_DIR`). CORS (`CORS_ORIGINS`, default `http://localhost:3000,http://127.0.0.1:3000`) only matters
when the app is served from another origin.

## Sessions

Every browser gets a random session cookie (`wm_session`) on its first call. The session scopes everything one
person does, so many people can use one deployment at once:

* The API picks each chat's workflow ID, `<task queue>-session-<session>-chat-<random>`, and answers 404 for a
  `workflow_id` that is not one of the caller's chats.
* The chaos flags and the failure log are per session.
* With `DEMO_DATA_STORE=redis` (as in the container setup), the sample bank data is per session too.

## Endpoints

All paths are under `/api`, and all parameters are query parameters.

| Method | Path | Parameters | Description |
|---|---|---|---|
| GET | `/api/health` | | Health check: the API is up and reaches Redis |
| POST | `/api/start-workflow` | | Starts a chat workflow for this session and returns its `workflow_id` |
| POST | `/api/send-prompt` | `workflow_id`, `prompt` | Sends a customer message to the chat workflow (signal) |
| GET | `/api/get-chat-history` | `workflow_id`, `from_index` (default 0) | Chat events from Redis, starting at `from_index`: `chat_interaction` (prompt and reply) and `status_update` (Open Account status) |
| POST | `/api/approve-compliance` | `workflow_id` | Approves the chat's account openings waiting in *Waiting Compliance Review* (signals `compliance_approved`) |
| POST | `/api/end-chat` | `workflow_id` | Ends the chat workflow (signal) |
| GET | `/api/chaos` | | This session's chaos flags (with labels and on/off state) and the failures they recently caused |
| POST | `/api/chaos` | `flag`, `enabled` (true/false) | Turns one chaos flag on or off. Flags: `tool_down`, `llm_down`, `llm_rate_limited`, `custodian_rejects`, `slow_saga`, `workflow_bug` |
| POST | `/api/chaos/reset` | | Turns every flag of this session off and clears its failure log |

## Try it with curl

The cookie jar (`-b jar -c jar`) keeps the calls in one session.

```bash
curl http://127.0.0.1:8000/api/health

curl -b jar -c jar -X POST http://127.0.0.1:8000/api/start-workflow
# {"message":"Workflow started.","workflow_id":"Supervisor-session-...-chat-..."}
WF=<the workflow_id>

curl -b jar -c jar -X POST "http://127.0.0.1:8000/api/send-prompt?workflow_id=$WF&prompt=Who%20are%20my%20beneficiaries%3F"
curl -b jar -c jar -X POST "http://127.0.0.1:8000/api/send-prompt?workflow_id=$WF&prompt=123"

curl -b jar -c jar "http://127.0.0.1:8000/api/get-chat-history?workflow_id=$WF&from_index=0"

curl -b jar -c jar -X POST 'http://127.0.0.1:8000/api/chaos?flag=tool_down&enabled=true'
curl -b jar -c jar -X POST 'http://127.0.0.1:8000/api/chaos/reset'

curl -b jar -c jar -X POST "http://127.0.0.1:8000/api/end-chat?workflow_id=$WF"
```

Prompts are handled asynchronously: poll `/api/get-chat-history` for the reply. To fetch only new events, pass
the number of events you already have as `from_index`.
