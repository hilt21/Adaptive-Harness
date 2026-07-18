import os
from pathlib import Path

import pytest

from adaptive_harness.modules import (
    ModuleExecutionError,
    ModuleManifest,
    ModuleRegistry,
    ModuleRunner,
)


def test_executable_module_requires_declared_capability(tmp_path: Path) -> None:
    manifest = ModuleRegistry().get_builtin("task-summary")

    with pytest.raises(ModuleExecutionError, match="not approved"):
        ModuleRunner().run(
            manifest,
            task={},
            available_capabilities=frozenset(),
            artifact_root=tmp_path / "artifacts",
        )


def test_declarative_module_cannot_be_executed(tmp_path: Path) -> None:
    manifest = ModuleRegistry().get_builtin("tdd-guidance")

    with pytest.raises(ModuleExecutionError, match="declarative"):
        ModuleRunner().run(
            manifest,
            task={},
            available_capabilities=frozenset(),
            artifact_root=tmp_path / "artifacts",
        )


@pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_OS_SANDBOX_E2E") != "1",
    reason="requires a host OS sandbox outside the test runner sandbox",
)
def test_builtin_executable_uses_real_os_sandbox(tmp_path: Path) -> None:
    runner = ModuleRunner()
    assert runner.sandbox_available is True

    result = runner.run(
        ModuleRegistry().get_builtin("task-summary"),
        task={"id": "task-1", "status": "completed"},
        available_capabilities=frozenset({"module-execution"}),
        artifact_root=tmp_path / "artifacts",
    )

    assert result.status == "success"
    assert result.summary == "Task fields summarized: 2"
    assert result.sandbox_enforced is True


@pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_OS_SANDBOX_E2E") != "1",
    reason="requires a host OS sandbox outside the test runner sandbox",
)
def test_os_sandbox_blocks_module_write_outside_workdir(tmp_path: Path) -> None:
    source = tmp_path / "module"
    source.mkdir()
    escaped = tmp_path / "escaped.txt"
    script = source / "runner.py"
    script.write_text(
        "from pathlib import Path\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "Path(request['task']['target']).write_text('escaped')\n"
        "json.dump({'status':'success','summary':'bad',"
        "'next_actions':[],'artifacts':[]}, sys.stdout)\n",
        encoding="utf-8",
    )
    document = ModuleRegistry().get_builtin("task-summary").to_dict()
    document["id"] = "sandbox-probe"
    document["entrypoint"] = ["runner.py"]
    manifest = ModuleManifest.from_dict(document)

    with pytest.raises(ModuleExecutionError, match="exited"):
        ModuleRunner().run(
            manifest,
            task={"target": str(escaped)},
            available_capabilities=frozenset({"module-execution"}),
            artifact_root=tmp_path / "artifacts",
            local_source_root=source,
        )

    assert not escaped.exists()
