# Wealth Management Agent Example using Google ADK 
Demonstrates how to use Google ADK with Temporal using subagents and instructions to delegate to other agents. 

The supervisor agent is responsible for directing the actions to the appropriate helper agents.   

Scenarios currently implemented include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account using a **Child Workflow**
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account

## Application Architecture

The overall application architecture looks like this:

![](../../images/application-architecture.png)

There is a React UX which is where the customer interacts with the application. 
The React UX leverages an API which exposes endpoints to start a workflow, send a prompt,
retrieving the chat history, and ending the chat. The React frontend uses adaptive polling
to retrieve new events from Redis, providing real-time status updates as the Open Account 
child workflow progresses through the different steps. 

The API in turn, communicates with Temporal to start workflows and send signals. Finally, 
the worker contains the two workflows - supervisor and open account - which contain the 
agents and business logic that drive the agentic application.

## Prerequisites

* [uv](https://docs.astral.sh/uv/) - Python package and project manager
* [Google / Gemini Key](https://console.cloud.google.com/apis/credentials) or [AI Studio](https://aistudio.google.com/api-keys) - Your key to access Gemini models
* [Temporal CLI](https://docs.temporal.io/cli#install) - Local Temporal service
* [Redis](https://redis.io/downloads/) - Stores conversation history and status updates

## Set up Python Environment
Execute this in the project root (not this folder -- up two levels)
```bash
uv sync
```

## Set up your Gemini API Key
Execute this in the project root (not this folder -- up two levels)

```bash
cp setgeminikey.example setgeminikey.sh
chmod +x setoaikey.sh
```

Now edit the setgeminikey.sh file and paste in your Gemini API Key.
It should look something like this:
```text
export GEMINI_API_KEY="Your API Key Goes Here"
```

## Set up Redis

Redis is used for storing conversation history and providing real-time status updates. If you don't have an existing Redis server, you can run one locally after installing it. 

In a new terminal / shell run the following command:

```bash
redis-server
```

By default, the application expects to find Redis running locally. You can override the location of Redis
by setting the environment variables:

```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

## Set up Claim Check (optional)

TODO

## Running the Demo Locally
Start Temporal Locally.

```bash
temporal server start-dev
```

### Start the Worker

```bash
cd src/temporal_supervisor
./startlocalworker.sh
```

### Start the API

```bash
cd src/api
./startlocalapi.sh
```
### Start the UX

```bash
cd src/frontend
npm start
```

A new browser window opens where you can interact with the application. 

If you are opening a new investment account, in another terminal
### Send the Compliance Reviewed Signal 
```bash
cd src/temporal_supervisor
./localsendcomplianceapproval.sh <Child Workflow ID>
```

## Running the Demo in Temporal Cloud

Copy the setcloudenv.example, located in the src/temporal_supervisor folder to the project root and name it setcloudenv.sh .

```bash
cp  src/temporal_supervisor/setcloudenv.example setcloudenv.sh
```

Edit setcloudenv.sh to match your Temporal Cloud account:
```bash
export TEMPORAL_ADDRESS=<namespace>.<accountID>.tmprl.cloud:7233
export TEMPORAL_NAMESPACE=<namespace>.<accountID>
export TEMPORAL_TLS_CLIENT_CERT_PATH="/path/to/cert.pem"
export TEMPORAL_TLS_CLIENT_KEY_PATH="/path/to/key.key"
```

### Start the Worker
```bash
cd src/temporal_supervisor
./startcloudworker.sh
```

### Start the API

```bash
cd src/api
./startcloudapi.sh
```
### Start the UX

```bash
cd src/frontend
npm start
```

If you are opening a new investment account, in another terminal
### Send the Compliance Reviewed Signal 
```bash
cd src/temporal_supervisor
./cloudsendcomplianceapproval.sh <Child Workflow ID>
```

