#!/bin/sh
set -eu

SOURCE_ROOT=/source
TARGET_ROOT=/workspaces/Adaptive-Harness

[ "$(id -un)" = "vscode" ] || {
    printf 'postCreate smoke must run as the vscode user\n' >&2
    exit 1
}

sudo mkdir -p "$TARGET_ROOT"
sudo chown -R "$(id -u):$(id -g)" "$TARGET_ROOT"
COPY_LIST=$(mktemp)
COPY_ARCHIVE=$(mktemp)
cleanup() {
    rm -f -- "$COPY_LIST" "$COPY_ARCHIVE"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

git \
    -c "safe.directory=$SOURCE_ROOT" \
    -C "$SOURCE_ROOT" \
    ls-files -co --exclude-standard -z >"$COPY_LIST"
tar --null -C "$SOURCE_ROOT" -T "$COPY_LIST" -cf "$COPY_ARCHIVE"
tar -C "$TARGET_ROOT" -xf "$COPY_ARCHIVE"

cleanup
trap - 0 HUP INT TERM

cd "$TARGET_ROOT"
sh .devcontainer/post-create.sh
UV_TOOL_BIN=$(uv tool dir --bin)
PATH="$PATH:$UV_TOOL_BIN"
export PATH
harness --version
test -d .demo/python-quickstart/repo/.git
test -d .demo/python-quickstart/state
