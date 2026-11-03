#!/bin/bash
# Starts the Java Open Account worker against Temporal Cloud, using the connection settings
# in ../setcloudenv.sh (API key or mTLS; see setcloudenv.example).
# The argument is the Open Account task queue; pass the same value to
# src/temporal_supervisor/startcloudworker.sh so the Python supervisor starts the workflow there.
if [ -z "$1" ]
  then
    echo "**** Missing required parameter: Task Queue Name for the Open Account Workflow"
    echo "(e.g. ./startcloudworker.sh OpenAccount, and use the same name when starting the Python worker)"
    exit 1
fi
cd "$(dirname "$0")"
export TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT="$1"
[ -f ../setclaimcheck.sh ] && source ../setclaimcheck.sh
source ../setcloudenv.sh
./runworker.sh
