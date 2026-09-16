#!/usr/bin/env bash
# Generate the TypeScript types from the bundled OpenAPI document, type-check the
# client, and run the demo against a service — inside the repo's node image, so
# no local node is needed.
#
#   sdk/dsail/examples/typescript/run.sh http://127.0.0.1:8710
#
# The service URL must be reachable from a container on the host network.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
url="${1:-${DSAIL_URL:-https://agents.jaxon.ai}}"
image="${NODE_IMAGE:-dsail/node:latest}"

cp "$here/../../src/dsail/contract/openapi.json" "$here/openapi.json"
cp "$here/../policies/expenses.dsail" "$here/expenses.dsail"

exec docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e npm_config_cache=/tmp/npm-cache \
  -e DSAIL_URL="$url" -e DSAIL_CREDENTIAL="${DSAIL_CREDENTIAL:-}" \
  -v "$here:/work" -w /work "$image" \
  sh -ec 'npm install --no-audit --no-fund --loglevel=error && npm run -s generate && npm run -s typecheck && npm run -s demo'
