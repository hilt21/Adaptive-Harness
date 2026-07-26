"""Executable module entry point used by Python and standalone builds."""

from adaptive_harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
