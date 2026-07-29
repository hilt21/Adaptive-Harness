#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TEMPLATE_DIR="$REPOSITORY_ROOT/examples/python-quickstart"
DEMO_PARENT="$REPOSITORY_ROOT/.demo"
RUN_ROOT="$DEMO_PARENT/python-quickstart"
RUN_REPOSITORY="$RUN_ROOT/repo"
RUN_STATE="$RUN_ROOT/state"
BACKUP_PARENT="$DEMO_PARENT/backups"
MARKER_NAME=".adaptive-harness-quickstart"
MARKER_VERSION="1"
RESET=0
STAGING_ROOT=
RESET_BACKUP=
NEW_RUN_CREATED=0

usage() {
    printf 'Usage: %s [--reset]\n' "$0"
}

fail() {
    printf 'quickstart setup: %s\n' "$1" >&2
    exit 1
}

valid_existing_run() {
    repository_physical=$(CDPATH= cd -- "$RUN_REPOSITORY" 2>/dev/null && pwd -P) ||
        return 1
    [ -f "$RUN_ROOT/$MARKER_NAME" ] &&
        [ "$(cat "$RUN_ROOT/$MARKER_NAME")" = "$MARKER_VERSION" ] &&
        [ -d "$RUN_REPOSITORY" ] &&
        [ -d "$RUN_STATE" ] &&
        [ -d "$RUN_REPOSITORY/.git" ] &&
        [ -d "$RUN_REPOSITORY/.venv" ] &&
        [ -f "$RUN_REPOSITORY/pyproject.toml" ] &&
        [ -f "$RUN_REPOSITORY/uv.lock" ] &&
        [ -f "$RUN_REPOSITORY/src/quickstart_math.py" ] &&
        [ -f "$RUN_REPOSITORY/tests/test_quickstart_math.py" ] &&
        [ -f "$RUN_REPOSITORY/.harness/config.json" ] &&
        [ -f "$RUN_REPOSITORY/.harness/capabilities.json" ] &&
        [ -f "$RUN_REPOSITORY/.harness/modules.lock.json" ] &&
        [ "$(git -C "$RUN_REPOSITORY" rev-parse --show-toplevel 2>/dev/null)" = "$repository_physical" ] &&
        git -C "$RUN_REPOSITORY" rev-parse --verify HEAD >/dev/null 2>&1 &&
        harness doctor --root "$RUN_REPOSITORY" >/dev/null 2>&1
}

cleanup() {
    status=$?
    trap - 0
    if [ -n "$STAGING_ROOT" ] && [ -d "$STAGING_ROOT" ]; then
        rm -rf -- "$STAGING_ROOT"
    fi
    if [ "$status" -ne 0 ] &&
        [ "$NEW_RUN_CREATED" -eq 1 ] &&
        [ -d "$RUN_ROOT" ]; then
        mkdir -p "$BACKUP_PARENT"
        mv -- "$RUN_ROOT" \
            "$BACKUP_PARENT/failed-python-quickstart-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    fi
    if [ "$status" -ne 0 ] &&
        [ -n "$RESET_BACKUP" ] &&
        [ -e "$RESET_BACKUP" ] &&
        [ ! -e "$RUN_ROOT" ]; then
        mv -- "$RESET_BACKUP" "$RUN_ROOT"
    fi
    exit "$status"
}

trap cleanup 0
trap 'exit 1' HUP INT TERM

case "${1-}" in
    "")
        ;;
    --reset)
        RESET=1
        ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

command -v git >/dev/null 2>&1 || fail "Git is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"
command -v harness >/dev/null 2>&1 || fail "harness is required"
PYTHON_BIN=$(command -v python3.12 || command -v python3 || true)
[ -n "$PYTHON_BIN" ] || fail "Python 3.12 or newer is required"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' ||
    fail "Python 3.12 or newer is required"

[ ! -L "$TEMPLATE_DIR" ] && [ -d "$TEMPLATE_DIR" ] ||
    fail "template directory is missing or is a symlink"
if [ -L "$DEMO_PARENT" ]; then
    fail "$DEMO_PARENT is a symlink; move it aside before continuing"
fi
if [ -L "$RUN_ROOT" ]; then
    fail "$RUN_ROOT is a symlink; move it aside before continuing"
fi
for unsafe_path in \
    "$RUN_REPOSITORY" \
    "$RUN_STATE" \
    "$RUN_ROOT/$MARKER_NAME" \
    "$RUN_REPOSITORY/.git" \
    "$RUN_REPOSITORY/.venv" \
    "$RUN_REPOSITORY/.harness" \
    "$RUN_REPOSITORY/.harness/config.json" \
    "$RUN_REPOSITORY/.harness/capabilities.json" \
    "$RUN_REPOSITORY/.harness/modules.lock.json" \
    "$RUN_REPOSITORY/pyproject.toml" \
    "$RUN_REPOSITORY/uv.lock" \
    "$RUN_REPOSITORY/src" \
    "$RUN_REPOSITORY/src/quickstart_math.py" \
    "$RUN_REPOSITORY/tests" \
    "$RUN_REPOSITORY/tests/test_quickstart_math.py" \
    "$BACKUP_PARENT"
do
    if [ -L "$unsafe_path" ]; then
        fail "$unsafe_path is a symlink; move it aside before continuing"
    fi
done

if [ -e "$RUN_ROOT" ]; then
    if ! valid_existing_run; then
        if [ "$RESET" -eq 0 ]; then
            fail "$RUN_ROOT is incomplete or from another version; rerun with --reset"
        fi
    elif [ "$RESET" -eq 0 ]; then
        printf 'Quickstart already prepared at %s\n' "$RUN_REPOSITORY"
        exit 0
    fi

    mkdir -p "$BACKUP_PARENT"
    RESET_BACKUP="$BACKUP_PARENT/python-quickstart-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mv -- "$RUN_ROOT" "$RESET_BACKUP"
fi

mkdir -p "$DEMO_PARENT"
STAGING_ROOT=$(mktemp -d "$DEMO_PARENT/.python-quickstart.stage.XXXXXX")
mkdir -p "$STAGING_ROOT/repo" "$STAGING_ROOT/state"
cp -R "$TEMPLATE_DIR/." "$STAGING_ROOT/repo/"

git -C "$STAGING_ROOT/repo" init -q
git -C "$STAGING_ROOT/repo" config user.name "Adaptive Harness Quickstart"
git -C "$STAGING_ROOT/repo" config user.email "quickstart@example.invalid"
git -C "$STAGING_ROOT/repo" add .
git -C "$STAGING_ROOT/repo" commit -q -m "Initialize quickstart project"

mv -- "$STAGING_ROOT" "$RUN_ROOT"
STAGING_ROOT=
NEW_RUN_CREATED=1

uv sync --no-config --locked --python "$PYTHON_BIN" --project "$RUN_REPOSITORY"
harness doctor --root "$RUN_REPOSITORY" >/dev/null

if [ -n "$(git -C "$RUN_REPOSITORY" status --porcelain)" ]; then
    fail "prepared repository is not clean"
fi

printf '%s\n' "$MARKER_VERSION" >"$RUN_ROOT/$MARKER_NAME"
NEW_RUN_CREATED=0
printf 'Quickstart prepared at %s\n' "$RUN_REPOSITORY"
if [ -n "$RESET_BACKUP" ]; then
    printf 'Previous run preserved at %s\n' "$RESET_BACKUP"
fi
