# Wealth Management Agent Example using Google ADK
Demonstrates how to use [Google ADK](https://adk.dev/) using subagents and instructions to delegate to other agents. 

The Temporal version of this example is located [here](../temporal_supervisor/README.md)

Scenarios currently implemented include
* Add Beneficiary - add a new beneficiary to your account
* List Beneficiaries - shows a list of beneficiaries and their relationship to the account owner
* Delete Beneficiary - delete a beneficiary from your account
* Open Investment Account - opens a new investment account
* List Investments - shows a list of accounts and their current balances
* Close Investment Account - closes an investment account

## Prerequisities 

* [uv](https://docs.astral.sh/uv/) - Python package and project manager

## Set up Python Environment
Execute this in the project root (not this folder -- up one level)

```bash
uv sync
```

## Set up your Gemini API Key
Execute this in the project root (not this folder -- up one level)

```bash
cp setgeminikey.example setgeminikey.sh
chmod +x setoaikey.sh
```

Now edit the setgeminikey.sh file and paste in your Gemini API Key.
It should look something like this:
```text
export GEMINI_API_KEY="Your API Key Goes Here"
```

## Running the agent
Execute this in the project root (not this folder -- up one level)

```bash
source ./setgeminikey.sh
uv run pyton src/adk_supervisor/main.py
```

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

You: end
Goodbye!
```

You can also add and delete beneficiaries and open and close investment accounts. 
