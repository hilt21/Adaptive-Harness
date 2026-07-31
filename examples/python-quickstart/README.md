# Adaptive Harness Python quickstart

This deliberately broken Python project shows how Adaptive Harness keeps failed
evidence, accepts a later successful check, applies a completion gate, and loads
optional guidance only for matching tasks.

The walkthrough uses the Generic adapter in `observe` mode. Commands run through
the Harness Gateway and Executor and produce trusted evidence, but `observe`
cannot block tools that bypass Harness. GitHub Codespaces is an optional
onboarding environment; Adaptive Harness itself sends no telemetry, and the
walkthrough runs without network access after setup.

## Prepare the disposable project

GitHub Codespaces prepares the working copy automatically. From the repository
root, enter it:

```bash
cd .demo/python-quickstart/repo
```

For local macOS or Linux use, install Git, Python 3.12+, and `uv`, then run from
the Adaptive Harness repository root:

```bash
uv sync --dev --locked
export PATH="$PWD/.venv/bin:$PATH"
./scripts/prepare-quickstart.sh
cd .demo/python-quickstart/repo
```

Keep this terminal open. Give this run its own local Task Store and unique task
ID:

```bash
export XDG_DATA_HOME="$(cd ../state && pwd)"
export TASK_ID="quickstart-$(date +%s)"
```

The five-minute timer starts here.

## 1. Start a governed task

```bash
adp-harness task start \
  --id "$TASK_ID" \
  --goal "Fix subtraction without changing the test" \
  --scope src \
  --scope tests \
  --capability project-tests \
  --trait code-change
```

Expected state:

```text
Task quickstart-…: draft
Module tdd-guidance: installed
```

The Task Envelope now binds the goal, allowed paths, test capability, Git base
SHA, and current worktree.

## 2. Capture the failure

```bash
adp-harness capability run \
  --task "$TASK_ID" \
  --capability project-tests
```

This command intentionally exits with status 2 because the test fails:

```text
Capability project-tests: command_failure (exit 1)
```

The failure is now append-only task evidence; it is not discarded when a later
attempt succeeds.

## 3. Make the one-line fix

Open `src/quickstart_math.py` and change:

```python
return a + b
```

to:

```python
return a - b
```

Terminal alternative:

```bash
python -c 'from pathlib import Path; p=Path("src/quickstart_math.py"); p.write_text(p.read_text().replace("return a + b", "return a - b"))'
```

Do not commit yet. The task remains bound to its original base SHA.

## 4. Capture successful evidence

```bash
adp-harness capability run \
  --task "$TASK_ID" \
  --capability project-tests
```

Expected result:

```text
Capability project-tests: succeeded (exit 0)
```

The second authorized attempt produced current, trusted test evidence while the
earlier failure remains in task history.

## 5. Pass the completion gate

```bash
adp-harness task verify "$TASK_ID"
```

Expected result:

```text
Task quickstart-…: completed
```

Completion required successful acceptance evidence, an unchanged base SHA and
worktree, and a diff limited to `src` or `tests`.

## 6. Enable progressive TDD guidance

First review the proposed canonical configuration change:

```bash
adp-harness module enable tdd-guidance --policy auto
```

Then apply exactly that reviewed change:

```bash
adp-harness module enable tdd-guidance --policy auto --apply --yes
adp-harness module list
```

Expected module state:

```text
tdd-guidance 1.0.0: enabled, auto, builtin
```

Enablement affects future matching tasks; it never rewrites a completed task.

## Optional: see the module activate

Commit only after the first task is complete:

```bash
git add src .harness/modules.lock.json
git commit -m "Complete quickstart task and enable TDD guidance"
export MODULE_TASK_ID="module-$(date +%s)"
```

Start another code-change task:

```bash
adp-harness task start \
  --id "$MODULE_TASK_ID" \
  --goal "Demonstrate progressive module activation" \
  --scope src \
  --scope tests \
  --capability project-tests \
  --trait code-change
```

The new task reports:

```text
Module tdd-guidance: activated
Write a failing test, implement the smallest fix, then verify it.
```

End the demonstration without leaving a non-terminal task:

```bash
adp-harness task cancel \
  "$MODULE_TASK_ID" \
  --reason "Activation demonstrated"
```

## Replay safely

From the Adaptive Harness repository root, explicitly request a reset:

```bash
./scripts/prepare-quickstart.sh --reset
```

Reset moves the previous repository and Task Store into `.demo/backups/` before
creating a clean run. It does not delete prior evidence or touch the global
Adaptive Harness data directory.
