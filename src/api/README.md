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
`TEMPORAL_TASK_QUEUE`, `REDIS_HOST`, `REDIS_PORT`, `USE_CLAIM_CHECK`, ...); see
[Configuration](../temporal_supervisor/README.md#configuration). It starts chat workflows on `TEMPORAL_TASK_QUEUE`
(default `Supervisor`), so it must match the worker's.

CORS allows the web application at `http://localhost:3000` and `http://127.0.0.1:3000`.

## Endpoints

All parameters are query parameters.

| Method | Path | Parameters | Description |
|---|---|---|---|
| GET | `/` | | Health check |
| POST | `/start-workflow` | `workflow_id` | Starts a chat workflow with that ID |
| POST | `/send-prompt` | `workflow_id`, `prompt` | Sends a customer message to the chat workflow (signal) |
| GET | `/get-chat-history` | `workflow_id`, `from_index` (default 0) | Chat events from Redis, starting at `from_index`: `chat_interaction` (prompt and reply) and `status_update` (Open Account status) |
| POST | `/end-chat` | `workflow_id` | Ends the chat workflow (signal) |
| GET | `/chaos` | | The chaos flags (with labels and on/off state) and the recently injected failures |
| POST | `/chaos` | `flag`, `enabled` (true/false) | Turns one chaos flag on or off. Flags: `tool_down`, `llm_down`, `llm_rate_limited`, `custodian_rejects`, `slow_saga`, `workflow_bug` |
| POST | `/chaos/reset` | | Turns every flag off and clears the failure log |

## Try it with curl

```bash
curl http://127.0.0.1:8000

curl -X POST 'http://127.0.0.1:8000/start-workflow?workflow_id=my-chat-1'

curl -X POST 'http://127.0.0.1:8000/send-prompt?workflow_id=my-chat-1&prompt=Who%20are%20my%20beneficiaries%3F'
curl -X POST 'http://127.0.0.1:8000/send-prompt?workflow_id=my-chat-1&prompt=123'

curl 'http://127.0.0.1:8000/get-chat-history?workflow_id=my-chat-1&from_index=0'

curl -X POST 'http://127.0.0.1:8000/chaos?flag=tool_down&enabled=true'
curl -X POST 'http://127.0.0.1:8000/chaos/reset'

curl -X POST 'http://127.0.0.1:8000/end-chat?workflow_id=my-chat-1'
```

Prompts are handled asynchronously: poll `/get-chat-history` for the reply. To fetch only new events, pass the
number of events you already have as `from_index`.
