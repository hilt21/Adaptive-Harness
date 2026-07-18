# Adaptive Harness

Adaptive Harness is a local-first, client-independent governance runtime for software-development agents. It binds each task to a real Git worktree and base SHA, authorizes typed capabilities, captures trusted command evidence, and applies a completion gate. Optional modules and templates cannot weaken those kernel invariants.

The MVP is implemented for Python 3.12+, macOS, and Linux. Generic CLI and Codex integrations are explicitly `observe`; Claude Code uses a project `PreToolUse` hook and an OS-sandboxed executor for an end-to-end verified `enforced` path. The product requirements remain the scope and architecture source of truth: [docs/adaptive-harness-prd.md](docs/adaptive-harness-prd.md).

## Install

For a published build, use an isolated CLI environment:

```bash
pipx install adaptive-harness
# or
uv tool install adaptive-harness
harness --version
```

For this source tree:

```bash
uv sync --dev
uv run harness --version
```

## Initialize a project

The project must be a Git repository with an initial commit. Deterministic scanning needs a declared test command; for example, a Python project can declare pytest in `pyproject.toml`, while a Node project can declare a test script in `package.json`.

Initialization is review-first. The first command prints a complete diff and writes nothing:

```bash
harness init --adapter generic
harness init --adapter generic --apply
harness doctor --verbose
```

Use `--adapter codex` to create a managed `AGENTS.md` block or `--adapter claude-code` to create a managed `CLAUDE.md` block plus `.claude/settings.json`. Existing user content is preserved. Commit the three canonical files and any selected client projection before starting the first task:

```text
.harness/config.json
.harness/capabilities.json
.harness/modules.lock.json
```

## Run the first governed task

Give the task an explicit ID, scope, capability, and optional module traits:

```bash
harness task start \
  --id first-task \
  --goal "Fix the failing example" \
  --scope src \
  --scope tests \
  --capability project-tests \
  --trait code-change

harness capability run \
  --task first-task \
  --capability project-tests

harness task verify first-task
```

`completed` is produced only when required acceptance evidence, workspace identity, scope, and security invariants all pass. `task accept-risk` can waive only explicitly waivable verification gaps.

Capabilities with `approval_policy: "ask"` or high-risk side effects require a separately reviewed, task/SHA/worktree/path/count/expiry-bound approval:

```bash
harness capability approve \
  --task first-task \
  --capability project-tests
harness capability approve \
  --task first-task \
  --capability project-tests \
  --apply
```

The plan command changes no task history. An applied approval is append-only and cannot be reused outside its exact scope or use count.

## Progressive modules and templates

Modules are installed but unloaded by default. Enablement is review-first; task JSON and human output report `installed`, `enabled`, `suggested`, `activated`, or `blocked`. Context appears only for `activated` modules.

```bash
harness module enable tdd-guidance --policy auto
harness module enable tdd-guidance --policy auto --apply
harness task start \
  --id tdd-task \
  --goal "Add validation" \
  --scope src \
  --capability project-tests \
  --trait code-change
```

Trials require explicit measured results before promotion:

```bash
harness module trial tdd-guidance --tasks 3 --apply
harness module trial-result tdd-guidance beneficial \
  --task task-1 \
  --evidence-ref record:task-1 \
  --apply
harness module promote tdd-guidance --apply
```

Templates are inert and only write after an explicit render, diff review, and confirmation:

```bash
harness template render handoff --output docs/handoff.md
harness template render handoff --output docs/handoff.md --apply
```

## Development and verification

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests
uv run pytest
uv build
```

Real host sandbox tests are opt-in locally because nested test-runner sandboxes may block them:

```bash
HARNESS_RUN_OS_SANDBOX_E2E=1 uv run pytest -q \
  tests/modules/test_runner.py \
  tests/core/test_verifier.py \
  tests/cli/test_cli.py \
  -k 'runner or enforced' --no-cov
```

All commands provide stable exit codes and JSON output through their local `--json` option. Mutations show the proposed change before confirmation; non-interactive JSON apply requires `--yes`.

Human summaries and top-level help support English and Simplified Chinese. The locale option precedes the command; JSON fields and protocol values remain stable English:

```bash
harness --locale zh-CN doctor
harness --locale zh-CN doctor --json
```
