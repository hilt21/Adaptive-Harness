import json
from pathlib import Path

import pytest

from adaptive_harness.init import Doctor, Initializer
from adaptive_harness.modules import (
    ActivationPolicy,
    ActivationState,
    ModuleManager,
    ModuleManifest,
    ModuleRegistry,
    decide_activation,
)


def _initialized(root: Path) -> None:
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))


def test_builtin_manifests_include_required_contract_and_stable_hashes() -> None:
    registry = ModuleRegistry()

    manifests = registry.list_builtin()

    assert {item.id for item in manifests} == {"tdd-guidance", "task-summary"}
    assert all(len(item.sha256) == 64 for item in manifests)
    assert all(item.success_metrics for item in manifests)
    assert all(item.rollback for item in manifests)
    assert registry.hashes() == {item.id: item.sha256 for item in manifests}


@pytest.mark.parametrize(
    ("policy", "manual", "expected"),
    [
        (ActivationPolicy.AUTO, False, ActivationState.ACTIVATED),
        (ActivationPolicy.SUGGEST, False, ActivationState.SUGGESTED),
        (ActivationPolicy.MANUAL, False, ActivationState.ENABLED),
        (ActivationPolicy.MANUAL, True, ActivationState.ACTIVATED),
        (ActivationPolicy.DISABLED, True, ActivationState.ENABLED),
    ],
)
def test_activation_policies_do_not_load_context_before_activation(
    policy: ActivationPolicy,
    manual: bool,
    expected: ActivationState,
) -> None:
    source = ModuleRegistry().get_builtin("tdd-guidance")
    document = source.to_dict()
    document["activation_policy"] = policy.value
    manifest = ModuleManifest.from_dict(document)

    decision = decide_activation(
        manifest,
        enabled=True,
        task_traits=frozenset({"code-change"}),
        available_capabilities=frozenset(),
        manually_requested=manual,
    )

    assert decision.state is expected
    assert (decision.context is not None) is (expected is ActivationState.ACTIVATED)


def test_activation_blocks_exclusions_capability_and_context_budget() -> None:
    executable = ModuleRegistry().get_builtin("task-summary")

    excluded = decide_activation(
        executable,
        enabled=True,
        task_traits=frozenset({"task-complete", "skip"}),
        available_capabilities=frozenset(),
        manually_requested=True,
    )
    missing_capability = decide_activation(
        executable,
        enabled=True,
        task_traits=frozenset({"task-complete"}),
        available_capabilities=frozenset(),
        manually_requested=True,
    )
    expensive_document = ModuleRegistry().get_builtin("tdd-guidance").to_dict()
    expensive_document["activation_policy"] = "auto"
    expensive_document["cost"]["context_tokens"] = 2001
    expensive = decide_activation(
        ModuleManifest.from_dict(expensive_document),
        enabled=True,
        task_traits=frozenset({"code-change"}),
        available_capabilities=frozenset(),
    )

    assert excluded.state is ActivationState.BLOCKED
    assert "capabilities" in missing_capability.reason
    assert expensive.state is ActivationState.BLOCKED


def test_enable_is_transactional_and_does_not_copy_builtin_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)

    assert all(not status.enabled for status in manager.list())
    plan = manager.plan_enable("tdd-guidance", policy=ActivationPolicy.AUTO)
    assert not (root / "adaptive_harness").exists()
    manager.apply(plan)

    status = next(item for item in manager.list() if item.manifest.id == "tdd-guidance")
    assert status.enabled is True
    assert status.activation_policy is ActivationPolicy.AUTO
    document = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    assert document["modules"][0]["source"] == "builtin"
    assert not (root / "adaptive_harness").exists()


def test_manager_resolves_project_policy_and_only_returns_activated_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)
    manager.apply(
        manager.plan_enable("tdd-guidance", policy=ActivationPolicy.MANUAL)
    )

    inactive = manager.activation_decisions(
        task_traits=frozenset({"code-change"}),
        available_capabilities=frozenset(),
    )
    activated = manager.activation_decisions(
        task_traits=frozenset({"code-change"}),
        available_capabilities=frozenset(),
        manually_requested=frozenset({"tdd-guidance"}),
    )

    inactive_tdd = next(item for item in inactive if item.module_id == "tdd-guidance")
    activated_tdd = next(
        item for item in activated if item.module_id == "tdd-guidance"
    )
    assert inactive_tdd.state is ActivationState.ENABLED
    assert inactive_tdd.context is None
    assert activated_tdd.state is ActivationState.ACTIVATED
    assert activated_tdd.context is not None


def test_manager_enforces_total_context_budget_across_modules(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)
    manager.apply(
        manager.plan_enable("tdd-guidance", policy=ActivationPolicy.AUTO)
    )
    local_dir = root / "modules/local-guidance"
    local_dir.mkdir(parents=True)
    local_manifest = local_dir / "manifest.json"
    document = ModuleRegistry().get_builtin("tdd-guidance").to_dict()
    document["id"] = "local-guidance"
    document["activation_policy"] = "auto"
    local_manifest.write_text(json.dumps(document), encoding="utf-8")
    manager.apply(
        manager.plan_enable(
            "local-guidance",
            policy=ActivationPolicy.AUTO,
            local_manifest=local_manifest,
        )
    )

    decisions = manager.activation_decisions(
        task_traits=frozenset({"code-change"}),
        available_capabilities=frozenset(),
        context_budget_tokens=200,
    )

    states = {item.module_id: item.state for item in decisions}
    assert states["tdd-guidance"] is ActivationState.ACTIVATED
    assert states["local-guidance"] is ActivationState.BLOCKED


def test_trial_promote_then_rollback_restores_previous_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)

    manager.apply(manager.plan_trial("tdd-guidance"))
    for index, beneficial in enumerate((True, True, False), start=1):
        manager.apply(
            manager.plan_record_trial_result(
                "tdd-guidance",
                task_id=f"task-{index}",
                evidence_ref=f"record:task-{index}",
                beneficial=beneficial,
            )
        )
    manager.apply(manager.plan_promote("tdd-guidance"))

    document = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    assert document["trials"][0]["state"] == "promoted"
    assert document["trials"][0]["history"] == [
        "proposed",
        "approved",
        "trial",
        "promoted",
    ]
    assert document["trials"][0]["success_metrics"] == [
        "required-checks-pass"
    ]
    assert document["trials"][0]["control"]["enabled"] is False
    manager.apply(manager.plan_rollback("tdd-guidance"))
    document = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    assert document["modules"] == []
    assert document["trials"][0]["state"] == "rolled_back"


def test_trial_cannot_promote_without_completed_measurable_benefit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)
    manager.apply(manager.plan_trial("tdd-guidance", matching_tasks=1))

    with pytest.raises(ValueError, match="remaining"):
        manager.plan_promote("tdd-guidance")

    manager.apply(
        manager.plan_record_trial_result(
            "tdd-guidance",
            task_id="task-no-benefit",
            evidence_ref="record:task-no-benefit",
            beneficial=False,
        )
    )
    with pytest.raises(ValueError, match="benefit"):
        manager.plan_promote("tdd-guidance")


def test_trial_stops_when_measured_overhead_exceeds_budget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    manager = ModuleManager(root)
    manager.apply(manager.plan_trial("task-summary", matching_tasks=1))

    manager.apply(
        manager.plan_record_trial_result(
            "task-summary",
            task_id="task-expensive",
            evidence_ref="record:task-expensive",
            beneficial=True,
            overhead_ms=2001,
        )
    )

    document = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    assert document["trials"][0]["state"] == "rejected"
    assert document["trials"][0]["history"][-1] == "rejected"


def test_explicit_local_module_is_hash_locked_and_doctor_detects_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root)
    module_dir = root / "local-module"
    module_dir.mkdir()
    manifest_path = module_dir / "manifest.json"
    document = ModuleRegistry().get_builtin("tdd-guidance").to_dict()
    document["id"] = "local-guidance"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    manager = ModuleManager(root)

    manager.apply(
        manager.plan_enable("local-guidance", local_manifest=manifest_path)
    )
    locked = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )["modules"][0]
    assert locked["source"] == "local"
    assert locked["location"] == "local-module/manifest.json"

    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "local-guidance", "changed-guidance"
        ),
        encoding="utf-8",
    )
    report = Doctor(root).run()
    assert report.check("module-hashes").status == "error"
