# Wealth Management Agent Example using Google ADK
Demonstrates how to use [Google ADK](https://adk.dev/) using subagents and instructions to delegate to other agents. 

The Temporal version of this example is located [here](../temporal_supervisor/README.md)

Scenarios currently implemented include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account. Here the Investment Agent opens it directly with its
  `open_investment` tool (no KYC, compliance review or saga; those are in the Temporal version)
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account
* Off-topic questions - anything not about beneficiaries or investment accounts is politely declined by the
  supervisor agent (handled in the agents' prompts)

The agents remember the client ID once you've given it, so you're only asked for it once.

## Prerequisites

* [uv](https://docs.astral.sh/uv/) - Python package and project manager
* Python 3.14 or later (`requires-python` in `pyproject.toml`; uv can install it for you)
* [Google / Gemini Key](https://console.cloud.google.com/apis/credentials) or [AI Studio](https://aistudio.google.com/api-keys) - Your key to access Gemini models

## Set up Python Environment
Execute this in the project root (not this folder -- up two levels)

```bash
uv sync
```

## Set up your Gemini API Key
Execute this in the project root (not this folder -- up two levels)

```bash
cp setgeminikey.example setgeminikey.sh
chmod +x setgeminikey.sh
```

Now edit the setgeminikey.sh file and paste in your Gemini API Key.
It should look something like this:
```text
export GEMINI_API_KEY="Your API Key Goes Here"
```

## Running the agent
Execute this in the project root (not this folder -- up two levels)

```bash
source ./setgeminikey.sh
uv run python src/adk_supervisor/main.py
```

`uv sync` also installs an `adk-supervisor` console script, so `uv run adk-supervisor` does the same.

Example Output
```
============================================================
  Wealth Management Assistant
============================================================
Type your message and press Enter. Type 'exit', 'quit', or 'end' to stop.

You: Who are my beneficiaries?

Assistant: I can help with that. What is your client ID?

You: 123

Assistant: Your current beneficiaries are:
John Doe (son)
Jane Doe (daughter)
Joan Doe (spouse)

What would you like to do: add, delete, or list beneficiaries?

You: What investments do I have?

Assistant: Your investment accounts are:
Checking: $1000
Savings: $2312.08
401K: $11070.89

What would you like to do: open, close, or list investment accounts?

You: Open a new account called Vacation with $500

Assistant: Your new Vacation account has been opened with a balance of $500.00.

You: end
Goodbye!
```

You can also add and delete beneficiaries and close investment accounts.

These changes are saved in the JSON files in `data/`. To reset the demo data, run `git checkout data/`
in the project root. 
