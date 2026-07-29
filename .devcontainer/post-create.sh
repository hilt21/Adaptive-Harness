#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

: "${UV_HTTP_TIMEOUT:=120}"
: "${UV_HTTP_RETRIES:=5}"
export UV_HTTP_TIMEOUT UV_HTTP_RETRIES

cd "$REPOSITORY_ROOT"
uv sync --dev --locked
TOOL_CONSTRAINTS=$(mktemp)
cleanup() {
    rm -f -- "$TOOL_CONSTRAINTS"
}
trap cleanup 0 HUP INT TERM
uv export \
    --locked \
    --no-dev \
    --no-emit-project \
    --no-hashes \
    --output-file "$TOOL_CONSTRAINTS" \
    >/dev/null
uv tool install \
    --editable "$REPOSITORY_ROOT" \
    --constraints "$TOOL_CONSTRAINTS" \
    --force
cleanup
trap - 0 HUP INT TERM
UV_TOOL_BIN=$(uv tool dir --bin)
PATH="$UV_TOOL_BIN:$PATH"
export PATH
"$REPOSITORY_ROOT/scripts/prepare-quickstart.sh"

printf '\nAdaptive Harness quickstart is ready.\n'
printf 'Read:  examples/python-quickstart/README.md\n'
printf 'Start: cd .demo/python-quickstart/repo\n'
