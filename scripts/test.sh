#!/usr/bin/env bash
# Run the package's suite. Unit tests always; the live tests when either
# DSAIL_TEST_URL points at a running service or the dsail/agent_api:latest image
# is present (in which case a container is started for the run). Python 3.10+
# and the `mcp` dependency are needed, so a venv is created beside the package.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sdk_root="$(cd "$here/.." && pwd)"
venv="$sdk_root/.venv-test"
python="${PYTHON:-python3}"

if [ ! -x "$venv/bin/python" ]; then
  "$python" -m venv "$venv"
  "$venv/bin/pip" install --quiet --upgrade pip
fi
"$venv/bin/pip" install --quiet -e "$sdk_root"

cd "$sdk_root"
exec "$venv/bin/python" -m unittest discover -s test -t . -v "$@"
