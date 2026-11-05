#!/bin/bash
# Run the open-account workflow's three scenarios without the chat or the LLM
# (see run_open_account_workflow.py). Starts a local Python worker in the background
# for the activities (and, without a queue argument, the workflow too), without the
# Google ADK plugin (SKIP_ADK_PLUGIN=True, so no Gemini key), and stops it when the
# scenarios finish.
# Usage: startlocalopenaccount.sh [open-account-task-queue] [extra args]
# With a task queue name, the workflow runs on that queue: start the Java worker with
# the same name first (java/startlocalworker.sh <queue>).
# Extra arguments after the queue go to run_open_account_workflow.py
# (e.g. --scenario 1 --amount 300000).
cd "$(dirname "$0")/../.." || exit 1
[ -f setclaimcheck.sh ] && source ./setclaimcheck.sh
if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then
  export TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT="$1"
  echo "*** Running the open-account workflow on task queue $1 (the Java worker must poll it). ***"
  shift
fi
export SKIP_ADK_PLUGIN=True
echo "Starting the worker and running the scenarios for the open-account workflow."
uv run python src/temporal_supervisor/run_worker.py &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null; wait $WORKER_PID 2>/dev/null' EXIT
uv run python src/temporal_supervisor/run_open_account_workflow.py "$@"
