#!/bin/sh
# Cursor worker for this repo: agents started through the API (env.type "machine") run their tools here.
cd "$(dirname "$0")/.." || exit 1
exec agent worker start --name "${P2B_MACHINE:-p2b-mac}" --worker-dir "$PWD"
