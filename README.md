# Adaptive Harness

Adaptive Harness is a local-first, client-independent governance runtime for software-development agents. It binds each task to a real Git worktree and base SHA, authorizes typed capabilities, captures trusted command evidence, and applies a completion gate. Optional modules and templates cannot weaken those kernel invariants.

The MVP supports macOS and glibc Linux on arm64 and x86_64. Generic CLI and Codex integrations are explicitly `observe`; Claude Code uses a project `PreToolUse` hook and an OS-sandboxed executor for an end-to-end verified `enforced` path. The product requirements remain the scope and architecture source of truth: [docs/adaptive-harness-prd.md](docs/adaptive-harness-prd.md).

## Install

Install the published, self-contained CLI for the current user. This does not require Python, a Python package manager, or `sudo`, and it does not modify the current repository:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/hilt21/Adaptive-Harness/releases/latest/download/install.sh | sh
harness --version
```

To install a fixed release instead of `latest`, set its SemVer explicitly:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/hilt21/Adaptive-Harness/releases/latest/download/install.sh \
  | HARNESS_VERSION=0.1.0 sh
```

The installer selects the platform artifact, verifies its checksum, and installs `harness` in `~/.local/bin`. If that directory is not on `PATH`, an interactive install offers a reviewed shell-profile change; a non-interactive install prints the required command without editing the profile.

To inspect and verify the installer before executing it, download both release files and compare the published SHA-256 entry:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  -o install.sh \
  https://github.com/hilt21/Adaptive-Harness/releases/latest/download/install.sh
curl --proto '=https' --tlsv1.2 -LsSf \
  -o SHA256SUMS \
  https://github.com/hilt21/Adaptive-Harness/releases/latest/download/SHA256SUMS
expected=$(awk '$2 == "install.sh" { print $1 }' SHA256SUMS)
actual=$(shasum -a 256 install.sh | awk '{ print $1 }')
test -n "$expected" && test "$actual" = "$expected"
less install.sh
HARNESS_VERSION=0.1.0 sh install.sh
```

Python packaging remains available as an alternative for users who already manage isolated CLI tools:

```bash
pipx install adaptive-harness
# or
uv tool install adaptive-harness
```

Linux `enforced` execution also requires Bubblewrap and working user namespaces. The installer reports a missing sandbox dependency but never invokes `sudo` or a system package manager. Base CLI and explicit `observe` operation remain available.

For this source tree, use the development environment:

```bash
uv sync --dev
uv run harness --version
```

Self-contained installations update only on explicit request:

```bash
harness self update
harness self uninstall          # preserves local records and repository config
harness self uninstall --purge-data
```

Package-manager installations must be updated and removed with the package manager that installed them.

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

## Local records and storage

Runtime records are isolated by repository identity under the user data directory. The default is `~/.local/share/harness/projects/<repository-id>/`, or `$XDG_DATA_HOME/harness/projects/<repository-id>/` when XDG is configured. Project statistics are shared by linked worktrees, while task records and artifacts live under a worktree-specific `worktrees/<worktree-id>/` directory. Different repositories and worktrees do not share task evidence.

An advanced, per-clone mode keeps records in the Git common directory, normally `.git/adaptive-harness/`. It does not write runtime data beside the committed `.harness/*.json` files and is not offered during first-time initialization:

```bash
harness storage mode
harness storage migrate repository-local
harness storage migrate repository-local --apply
harness storage migrate user-data --rollback
harness storage migrate user-data --rollback --apply --yes
```

Migration is review-first. It is rejected while a task is active or when the target already contains records; apply copies and verifies data, atomically changes the local mode, and retains the source as a rollback copy. An immediate `--rollback` may switch to that retained copy only while it still exactly matches the active records. A later reverse migration follows the normal process after the retained target has been explicitly archived or pruned; existing records are never merged automatically.

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
