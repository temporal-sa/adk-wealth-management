# React Web App for Wealth Management

A web front end (Create React App) for the Temporal version of the Wealth Management agent.
It uses the [API](../api/README.md) to talk to Temporal; it never connects to Temporal directly.

## Prerequisites

* [Node.js](https://nodejs.org/en/download) and npm
* The API running, which needs Temporal, Redis and the worker (see [the API README](../api/README.md) and
  [Running the Demo](../temporal_supervisor/README.md#running-the-demo))

## Running the Web App
```bash
cd src/frontend
npm install     # first time only
npm start
```

The app opens at http://localhost:3000. It expects the API at `http://127.0.0.1:8000`, set by `API_BASE_URL`
in `src/frontend/src/config.js`. The API's CORS settings allow port 3000 (`localhost` and `127.0.0.1`).

## Using it

* **Start Chat** starts a new chat workflow. Each chat gets a random workflow ID with the prefix
  `adk-temporal-agent-` (set in `src/App.js`), so you can run several chats, for example in different tabs.
  **End Chat** ends it.
* The app polls the API for new messages (every 2 seconds while waiting for a reply, otherwise every 5 seconds).
  Status updates from an account opening appear in the status area above the chat.
* **Chaos** in the header opens the chaos panel, which turns failure scenarios on and off and lists the
  failures they cause. See [Failure Scenarios](../temporal_supervisor/README.md#failure-scenarios-chaos-panel).

If you are opening a new investment account, send the compliance approval from another terminal:

```bash
cd src/temporal_supervisor
./localsendcomplianceapproval.sh <Child Workflow ID>     # or ./cloudsendcomplianceapproval.sh for Temporal Cloud
```

The chat doesn't show the child workflow ID. See [Running the Demo](../temporal_supervisor/README.md#locally) for its
format and how to find it in the Temporal UI.

## Tests

```bash
cd src/frontend
npm install     # first time only
CI=true npm test
```

The tests check that the chat renders with a **Start Chat** button and that the **Chaos** button opens and closes
the chaos panel.
