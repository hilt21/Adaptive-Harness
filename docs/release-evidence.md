# MVP release evidence

Evidence date: 2026-07-16. This document records commands that were actually run against the current worktree. Temporary project paths and user-specific machine paths are intentionally omitted.

## Readiness and build

- Python 3.12.1 and 3.13.7 were both available and used.
- `uv build` produced `adaptive_harness-0.1.0-py3-none-any.whl` and `adaptive_harness-0.1.0.tar.gz`.
- The wheel was installed into a new virtual environment without an editable source link.
- Installed-package smoke checks passed for `harness --version`, English/Chinese help, module Trial planning, packaged schemas, and the built-in executable runner.
- Wheel-external Python and Node fixtures both produced the three canonical files. Python `generic` and Node `codex` Doctor reports were fully passing; the Codex fixture also produced the managed `AGENTS.md` projection.

## Automated quality gates

Python 3.12 and Python 3.13 each passed:

```text
ruff check .                         passed
mypy src tests                      passed (71 source files)
pytest -q                           168 passed, 4 skipped
coverage                            84.13% (minimum 80%)
```

The four default skips are the host OS sandbox tests. They were run separately with `HARNESS_RUN_OS_SANDBOX_E2E=1`:

```text
macOS sandbox-exec                  6 passed
Linux bubblewrap (Debian/Python)   6 passed
```

The Linux run exposed and then verified a fix for worktrees below `/tmp`: the private `/tmp` mount now rebinds the verified worktree read-only before adding declared writable paths.

The repository includes `.github/workflows/ci.yml` with an Ubuntu/macOS × Python 3.12/3.13 matrix, real OS sandbox tests, Ruff, Mypy, pytest, and package build. A hosted run requires the repository to be committed and pushed; no hosted result is claimed by this local evidence file.

## Real adapter enforcement

Claude Code 2.1.162 was run against a separate initialized project with `Read,Edit`, project settings, and `--dangerously-skip-permissions`. Its actual `PreToolUse:Edit` event returned:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "mutating tools must execute through `harness capability run`"
  }
}
```

The Edit tool reported a permission denial and the marker file remained byte-for-byte unchanged. The hook command also fails closed with `|| exit 2`. Generic and Codex remain explicitly `observe`; only Claude Code claims `enforced` after its native hook and sandbox path pass health checks.

## Three real progressive-loading tasks

Three tasks were run through the wheel-installed CLI in a separate Git Python fixture. Each used an offline, read-only declared capability, generated trusted Executor evidence, and reached `completed` through `harness task verify`.

| Task | Project module policy | Activation evidence | Context | Outcome |
|---|---|---|---|---|
| `real-task-1b` | not enabled | `installed` | `null` | `completed` |
| `real-task-2` | `auto`, trait `code-change` | `activated` | TDD guidance only after activation | `completed` |
| `real-task-3` | `manual`, explicit module request | `activated` | TDD guidance only after activation | `completed` |

An earlier task used an invalid executable path. It recorded `environment_failure` and was cancelled; its immutable base SHA and adverse evidence were not rewritten. This negative run is retained as evidence that the real workflow did not convert a failed attempt into success.

A separate `real-fix-task` changed `src/python_fixture/__init__.py` inside its declared `src` scope, ran the declared read-only acceptance capability, and reached `completed` with the in-scope diff still present. This is the release Gate's ordinary fix task; the three tasks in the table remain the progressive-loading sample.

## Release and security Gate mapping

- Initialization, idempotency, projection drift/uninstall, transaction recovery: initializer, integration, managed-projection, configuration, and Doctor suites.
- Task create/amend/cancel/resume semantics, immutable identity, append-only history, corruption detection: envelope, store, workspace, and task CLI suites.
- Unknown capability, undeclared write/network/side effect, production/credential/irreversible approval, scoped approval reuse/count: Gateway and capability CLI suites.
- Timeout, cancellation, output limits, artifact containment, redaction, trusted event linkage: Executor suite.
- Coverage/required-check failure, stale or forged evidence, diff escape, workspace drift, artifact escape, secret leak, lease conflict, and accepted-with-risk invariants: Verifier suite.
- Generic/Codex/Claude adapters, observe/enforced truthfulness, hook bypass denial, projection preservation, and adapter contract: adapter suites plus the real Claude run above.
- Installed/Enabled/Activated decisions, all four activation policies, total context budget, process/OS isolation, Trial evidence/budget/stop conditions/promote/rollback, and inert template boundaries: module, runner, CLI, and template suites.
- Feedback off/minimal/research, zero model calls in minimal, taxonomy, maturity, counterexamples, cooldown, and recommendation-to-Trial restriction: feedback suite.
- Compatibility, transactional upgrade/rollback, retention tiers, pinning, redacted export, and zero telemetry defaults: upgrade and storage/export suites.

## Performance observations

Using the isolated wheel environment on the fixture:

```text
harness doctor --json               0.29 s
deterministic harness init plan     0.16 s
```

Both are below the PRD limits of 2 seconds and 10 seconds respectively. Minimal feedback has a dedicated test proving no analyzer/model call. No benchmark claim is made for remote filesystems or loaded CI hosts.
