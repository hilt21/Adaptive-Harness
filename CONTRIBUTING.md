# Contributing to Adaptive Harness

Thank you for helping improve Adaptive Harness. Contributions should keep the
project local-first, client-agnostic, and honest about which controls are
`observe` versus `enforced`.

## Choose a contribution

- Browse the
  [good first issues](https://github.com/hilt21/Adaptive-Harness/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
  for small, bounded tasks.
- Check the
  [public roadmap](https://github.com/hilt21/Adaptive-Harness/issues/4)
  before proposing a larger change.
- Open an issue before starting substantial behavior, architecture, or product
  scope changes.

The [product requirements](docs/adaptive-harness-prd.md) are the source of
truth for the MVP. Native Windows support, a public plugin marketplace, cloud
control planes, and default multi-agent orchestration are not part of the
current MVP.

## Set up the development environment

Adaptive Harness uses Python 3.12+ and
[uv](https://docs.astral.sh/uv/) for development:

```bash
git clone https://github.com/hilt21/Adaptive-Harness.git
cd Adaptive-Harness
uv sync --dev
uv run adp-harness --version
```

The supported runtime environments are macOS and glibc Linux. WSL2 support is
experimental; native Windows support is an uncommitted post-MVP candidate.

## Make a focused change

1. Fork the repository and create a branch for one issue.
2. Add or update tests for every behavior change.
3. Keep the change within the issue's scope and preserve the existing project
   structure and terminology.
4. Update documentation when a command, contract, or user-visible behavior
   changes.
5. Do not commit task records, logs, artifacts, secrets, absolute paths, or
   personal environment configuration.

The core safety invariants are not optional. In particular:

- permissions are intersected, restrictions use the strictest value, and deny
  wins;
- unknown fields, side effects, and out-of-scope paths fail closed or require
  approval;
- model claims and self-assessment are not completion evidence;
- required acceptance evidence cannot be removed automatically;
- prompt-only integrations must not be described as enforced.

## Verify the change

Run the checks that match your change:

```bash
uv run pytest
uv run ruff check .
uv run mypy src tests
git diff --check
```

Changes to core invariants, schemas, adapter contracts, or CLI contracts should
run the full test suite. For documentation-only changes, verify every command,
path, and link, run `git diff --check`, and state any checks you did not run in
the pull request.

## Open a pull request

In the pull request:

- link the issue it addresses;
- explain the user-visible outcome and any security implications;
- list the verification commands and results;
- call out unverified platforms or scenarios;
- keep unrelated cleanup out of the diff.

By contributing, you agree that your contribution is licensed under the
repository's Apache-2.0 license.
