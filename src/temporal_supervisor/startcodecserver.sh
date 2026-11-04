#!/bin/bash
echo "****************************************"
echo "* Remember to start your Redis Server! *"
echo "****************************************"
if [ ! -f ../../setclaimcheck.sh ]; then
   echo "The codec server needs setclaimcheck.sh in the project root (see README \"Set up Claim Check\")"
   exit 1
fi
source ../../setclaimcheck.sh
uv run python -m temporal_supervisor.codec_server.codec_server
