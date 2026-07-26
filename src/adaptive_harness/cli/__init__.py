"""Command-line entry point for Adaptive Harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from adaptive_harness import __version__
from adaptive_harness.adapters import ADAPTER_TYPES, IntegrationManager, adapter_for
from adaptive_harness.adapters.claude_hook import decide_pre_tool_use
from adaptive_harness.cli.i18n import (
    SUPPORTED_LOCALES,
    resolve_locale,
    set_locale,
    translate,
    translate_error,
)
from adaptive_harness.configuration import ConfigurationManager
from adaptive_harness.core.executor import ExecutorError
from adaptive_harness.core.gateway import CapabilityDeniedError, ScopedApproval
from adaptive_harness.core.store import TaskStoreError
from adaptive_harness.core.task_service import TaskService
from adaptive_harness.distribution import SelfManager
from adaptive_harness.feedback import (
    AnalysisPolicy,
    EffectObservation,
    FailureKind,
    FeedbackConfiguration,
    FeedbackMode,
    FeedbackStore,
    Maturity,
    RecommendationEngine,
    RecommendationTarget,
)
from adaptive_harness.init import Doctor, DoctorReport, InitializationError, Initializer
from adaptive_harness.modules import ActivationPolicy, ModuleManager
from adaptive_harness.storage import (
    ExportManager,
    StorageLocator,
    StorageManager,
    StorageMigrator,
    StorageMode,
)
from adaptive_harness.templates import TemplateCatalog
from adaptive_harness.upgrade import UpgradeManager


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Run the Adaptive Harness command-line interface."""
    raw_arguments = tuple(argv) if argv is not None else tuple(sys.argv[1:])
    selected_locale = resolve_locale(raw_arguments)
    set_locale(selected_locale)
    parser = _parser(default_locale=selected_locale)
    arguments = parser.parse_args(raw_arguments)
    set_locale(arguments.locale)
    if arguments.command is None:
        return 0
    try:
        if arguments.command == "init":
            return _run_init(arguments, input_fn)
        if arguments.command == "doctor":
            return _run_doctor(arguments)
        if arguments.command == "integration":
            return _run_integration(arguments, input_fn)
        if arguments.command == "module":
            return _run_module(arguments, input_fn)
        if arguments.command == "template":
            return _run_template(arguments, input_fn)
        if arguments.command == "feedback":
            return _run_feedback(arguments, input_fn)
        if arguments.command == "suggest":
            return _run_suggest(arguments)
        if arguments.command == "storage":
            return _run_storage(arguments, input_fn)
        if arguments.command == "export":
            return _run_export(arguments, input_fn)
        if arguments.command == "upgrade":
            return _run_upgrade(arguments, input_fn)
        if arguments.command == "self":
            return _run_self(arguments, input_fn)
        if arguments.command == "task":
            return _run_task(arguments)
        if arguments.command == "config":
            return _run_config(arguments, input_fn)
        if arguments.command == "adapter-hook":
            return _run_adapter_hook(arguments)
        if arguments.command == "capability":
            return _run_capability(arguments, input_fn)
    except (
        InitializationError,
        CapabilityDeniedError,
        ExecutorError,
        OSError,
        TaskStoreError,
        UnicodeError,
        ValueError,
    ) as error:
        _emit_error(str(error), json_output=arguments.json)
        return 1
    parser.error(f"unsupported command: {arguments.command}")
    return 2


class _LocalizedArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        add_help = kwargs.pop("add_help", True)
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = translate("positional arguments")
        self._optionals.title = translate("options")
        if add_help:
            self.add_argument(
                "-h",
                "--help",
                action="help",
                help=translate("show this help message and exit"),
            )

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", translate("usage: "), 1)

    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", translate("usage: "), 1)


def _parser(*, default_locale: str = "en-US") -> argparse.ArgumentParser:
    parser = _LocalizedArgumentParser(
        prog="harness", description=translate("Adaptive Harness command-line interface")
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--locale", choices=SUPPORTED_LOCALES, default=default_locale
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar=(
            "{init,doctor,integration,module,template,feedback,suggest,storage,"
            "export,upgrade,self,task,config,capability}"
        ),
    )

    init_parser = subparsers.add_parser(
        "init",
        help=translate("plan or apply deterministic project initialization"),
    )
    init_parser.add_argument("--root", type=Path, default=Path.cwd())
    init_parser.add_argument(
        "--adapter",
        choices=("generic", "codex", "claude-code"),
        default="generic",
    )
    init_parser.add_argument("--model-profile", default="unknown-conservative")
    init_parser.add_argument("--apply", action="store_true")
    init_parser.add_argument("--yes", action="store_true")
    init_parser.add_argument("--json", action="store_true")
    init_parser.add_argument("--verbose", action="store_true")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help=translate("validate Harness configuration and workspace health"),
    )
    doctor_parser.add_argument("--root", type=Path, default=Path.cwd())
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--verbose", action="store_true")

    integration_parser = subparsers.add_parser(
        "integration",
        help=translate("inspect or manage coding-client integrations"),
    )
    integration_commands = integration_parser.add_subparsers(
        dest="integration_command", required=True
    )
    list_parser = integration_commands.add_parser(
        "list", help="show supported integrations and verified capability modes"
    )
    _integration_common_options(list_parser)
    for operation in ("install", "repair", "uninstall"):
        operation_parser = integration_commands.add_parser(
            operation, help=f"plan or apply integration {operation}"
        )
        operation_parser.add_argument("adapter", choices=tuple(ADAPTER_TYPES))
        operation_parser.add_argument("--apply", action="store_true")
        operation_parser.add_argument("--yes", action="store_true")
        _integration_common_options(operation_parser)

    module_parser = subparsers.add_parser(
        "module", help=translate("inspect and manage optional modules")
    )
    module_commands = module_parser.add_subparsers(
        dest="module_command", required=True
    )
    module_list = module_commands.add_parser("list", help="list installed modules")
    _integration_common_options(module_list)
    module_enable = module_commands.add_parser("enable", help="enable a module")
    module_enable.add_argument("module_id")
    module_enable.add_argument(
        "--policy", choices=tuple(item.value for item in ActivationPolicy)
    )
    module_enable.add_argument("--local-manifest", type=Path)
    _mutation_options(module_enable)
    for operation in ("disable", "promote", "rollback"):
        operation_parser = module_commands.add_parser(
            operation, help=f"{operation} a module or trial"
        )
        operation_parser.add_argument("module_id")
        _mutation_options(operation_parser)
    module_trial = module_commands.add_parser("trial", help="start a module trial")
    module_trial.add_argument("module_id")
    module_trial.add_argument("--tasks", type=int, default=3)
    _mutation_options(module_trial)
    module_trial_result = module_commands.add_parser(
        "trial-result", help="record an explicit measured trial result"
    )
    module_trial_result.add_argument("module_id")
    module_trial_result.add_argument(
        "result", choices=("beneficial", "not-beneficial")
    )
    module_trial_result.add_argument("--task", required=True)
    module_trial_result.add_argument("--evidence-ref", required=True)
    module_trial_result.add_argument("--overhead-ms", type=int, default=0)
    _mutation_options(module_trial_result)

    template_parser = subparsers.add_parser(
        "template", help=translate("list or explicitly render inert templates")
    )
    template_commands = template_parser.add_subparsers(
        dest="template_command", required=True
    )
    template_list = template_commands.add_parser("list", help="list templates")
    _integration_common_options(template_list)
    template_render = template_commands.add_parser(
        "render", help="plan or apply a template render"
    )
    template_render.add_argument("template_id")
    template_render.add_argument("--output", required=True)
    _mutation_options(template_render)

    feedback_parser = subparsers.add_parser(
        "feedback", help=translate("inspect or configure local feedback")
    )
    feedback_commands = feedback_parser.add_subparsers(
        dest="feedback_command", required=True
    )
    feedback_show = feedback_commands.add_parser(
        "show", help="show local feedback configuration and episodes"
    )
    _integration_common_options(feedback_show)
    feedback_show.add_argument("--data-root", type=Path)
    feedback_mode = feedback_commands.add_parser(
        "mode", help="plan or apply feedback mode changes"
    )
    feedback_mode.add_argument(
        "mode", choices=tuple(item.value for item in FeedbackMode)
    )
    feedback_mode.add_argument(
        "--analysis-policy",
        choices=tuple(item.value for item in AnalysisPolicy),
    )
    _mutation_options(feedback_mode)

    suggest_parser = subparsers.add_parser(
        "suggest",
        help=translate("derive local recommendations from mature evidence"),
    )
    _integration_common_options(suggest_parser)
    suggest_parser.add_argument("--data-root", type=Path)

    storage_parser = subparsers.add_parser(
        "storage", help=translate("inspect, migrate, prune, or pin local Harness data")
    )
    storage_commands = storage_parser.add_subparsers(
        dest="storage_command", required=True
    )
    storage_status = storage_commands.add_parser(
        "status", help=translate("show local storage usage")
    )
    _integration_common_options(storage_status)
    storage_status.add_argument("--data-root", type=Path)
    storage_mode = storage_commands.add_parser(
        "mode", help=translate("show local storage mode")
    )
    _integration_common_options(storage_mode)
    storage_mode.add_argument("--data-root", type=Path)
    storage_migrate = storage_commands.add_parser(
        "migrate", help=translate("plan or apply a local storage migration")
    )
    storage_migrate.add_argument(
        "target_mode", choices=tuple(item.value for item in StorageMode)
    )
    storage_migrate.add_argument("--rollback", action="store_true")
    _mutation_options(storage_migrate)
    storage_migrate.add_argument("--data-root", type=Path)
    storage_prune = storage_commands.add_parser(
        "prune", help=translate("plan local retention")
    )
    _mutation_options(storage_prune)
    storage_prune.add_argument("--data-root", type=Path)
    storage_pin = storage_commands.add_parser(
        "pin", help=translate("pin one local item")
    )
    storage_pin.add_argument("item_id")
    storage_pin.add_argument("--recommendation-ref")
    _mutation_options(storage_pin)
    storage_pin.add_argument("--data-root", type=Path)

    export_parser = subparsers.add_parser(
        "export",
        help=translate("plan or create a redacted local support export"),
    )
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--data-root", type=Path)
    _mutation_options(export_parser)

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help=translate("check, plan, apply, or rollback configuration upgrades"),
    )
    upgrade_commands = upgrade_parser.add_subparsers(
        dest="upgrade_command", required=True
    )
    upgrade_check = upgrade_commands.add_parser("check", help="check compatibility")
    _integration_common_options(upgrade_check)
    upgrade_check.add_argument("--data-root", type=Path)
    upgrade_plan = upgrade_commands.add_parser("plan", help="show upgrade diff")
    _integration_common_options(upgrade_plan)
    upgrade_plan.add_argument("--data-root", type=Path)
    upgrade_apply = upgrade_commands.add_parser("apply", help="apply upgrade")
    upgrade_apply.add_argument("--yes", action="store_true")
    _integration_common_options(upgrade_apply)
    upgrade_apply.add_argument("--data-root", type=Path)
    upgrade_rollback = upgrade_commands.add_parser(
        "rollback", help="restore an upgrade recovery point"
    )
    upgrade_rollback.add_argument("--recovery-id")
    upgrade_rollback.add_argument("--yes", action="store_true")
    _integration_common_options(upgrade_rollback)
    upgrade_rollback.add_argument("--data-root", type=Path)

    self_parser = subparsers.add_parser(
        "self", help=translate("manage a standalone Harness installation")
    )
    self_commands = self_parser.add_subparsers(dest="self_command", required=True)
    self_update = self_commands.add_parser(
        "update", help=translate("download and verify a standalone Runtime update")
    )
    self_update.add_argument("--version")
    self_update.add_argument("--json", action="store_true")
    self_update.add_argument("--verbose", action="store_true")
    self_uninstall = self_commands.add_parser(
        "uninstall", help=translate("remove a standalone Runtime installation")
    )
    self_uninstall.add_argument("--purge-data", action="store_true")
    self_uninstall.add_argument("--yes", action="store_true")
    self_uninstall.add_argument("--json", action="store_true")
    self_uninstall.add_argument("--verbose", action="store_true")

    task_parser = subparsers.add_parser(
        "task",
        help=translate("start, inspect, amend, cancel, or verify governed tasks"),
    )
    task_commands = task_parser.add_subparsers(dest="task_command", required=True)
    task_start = task_commands.add_parser("start", help="create a Task Envelope")
    task_start.add_argument("--id")
    task_start.add_argument("--goal", required=True)
    task_start.add_argument("--scope", action="append", required=True)
    task_start.add_argument("--requirement", action="append", default=[])
    task_start.add_argument("--capability", action="append", default=[])
    task_start.add_argument("--trait", action="append", default=[])
    task_start.add_argument("--module", action="append", default=[])
    task_start.add_argument("--timeout", type=int, default=120)
    task_start.add_argument("--retry-budget", type=int, default=1)
    _task_common_options(task_start)
    task_show = task_commands.add_parser("show", help="show one canonical record")
    task_show.add_argument("task_id")
    _task_common_options(task_show)
    task_amend = task_commands.add_parser("amend", help="expand task boundaries")
    task_amend.add_argument("task_id")
    task_amend.add_argument("--goal")
    task_amend.add_argument("--add-scope", action="append", default=[])
    task_amend.add_argument("--add-capability", action="append", default=[])
    _task_common_options(task_amend)
    task_cancel = task_commands.add_parser("cancel", help="abandon an active task")
    task_cancel.add_argument("task_id")
    task_cancel.add_argument("--reason", required=True)
    _task_common_options(task_cancel)
    task_verify = task_commands.add_parser(
        "verify", help="run the Completion Gate"
    )
    task_verify.add_argument("task_id")
    _task_common_options(task_verify)
    task_risk = task_commands.add_parser(
        "accept-risk", help="request a waivable-risk terminal outcome"
    )
    task_risk.add_argument("task_id")
    task_risk.add_argument("--reason", required=True)
    _task_common_options(task_risk)

    config_parser = subparsers.add_parser(
        "config",
        help=translate("explain, diff, or rebuild canonical configuration"),
    )
    config_commands = config_parser.add_subparsers(
        dest="config_command", required=True
    )
    config_explain = config_commands.add_parser(
        "explain", help="explain canonical configuration provenance"
    )
    _integration_common_options(config_explain)
    config_diff = config_commands.add_parser(
        "diff", help="show canonical formatting and projection drift"
    )
    _integration_common_options(config_diff)
    config_apply = config_commands.add_parser(
        "apply", help="apply the reviewed canonical rebuild"
    )
    config_apply.add_argument("--yes", action="store_true")
    _integration_common_options(config_apply)

    hook_parser = subparsers.add_parser("adapter-hook")
    hook_parser.add_argument("adapter", choices=("claude-code",))

    capability_parser = subparsers.add_parser(
        "capability",
        help=translate("run declared operations through Gateway and Executor"),
    )
    capability_commands = capability_parser.add_subparsers(
        dest="capability_command", required=True
    )
    capability_approve = capability_commands.add_parser(
        "approve", help="review and grant one task-scoped capability approval"
    )
    capability_approve.add_argument("--task", required=True)
    capability_approve.add_argument("--capability", required=True)
    capability_approve.add_argument("--max-uses", type=int, default=1)
    capability_approve.add_argument("--valid-for-minutes", type=int, default=15)
    capability_approve.add_argument("--apply", action="store_true")
    capability_approve.add_argument("--yes", action="store_true")
    _task_common_options(capability_approve)
    capability_run = capability_commands.add_parser(
        "run", help="authorize and execute a declared task capability"
    )
    capability_run.add_argument("--task", required=True)
    capability_run.add_argument("--capability", required=True)
    _task_common_options(capability_run)
    return parser


def _integration_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")


def _mutation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    _integration_common_options(parser)


def _task_common_options(parser: argparse.ArgumentParser) -> None:
    _integration_common_options(parser)
    parser.add_argument("--data-root", type=Path)


def _run_init(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    initializer = Initializer(arguments.root)
    plan = initializer.plan(
        adapter=arguments.adapter,
        model_profile=arguments.model_profile,
    )
    diff = plan.diff()
    files = [change.path for change in plan.changes]
    if not arguments.apply:
        if arguments.json:
            _emit_json(
                {
                    "status": "planned",
                    "root": str(plan.root),
                    "adapter": plan.adapter,
                    "files": files,
                    "diff": diff,
                }
            )
        else:
            print(
                diff
                if diff
                else translate("No initialization changes are required.")
            )
        return 0

    if not arguments.json:
        print(
            diff if diff else translate("No initialization changes are required.")
        )
    if not arguments.yes:
        if arguments.json:
            raise ValueError("JSON apply requires --yes to avoid an interactive prompt")
        answer = input_fn(
            translate("Apply the reviewed initialization changes? [y/N] ")
        )
        if answer.strip().lower() not in {"y", "yes"}:
            print(translate("Initialization cancelled; no files were written."))
            return 1

    initializer.apply(plan)
    report = Doctor(arguments.root).run()
    if arguments.json:
        _emit_json(
            {
                "status": "applied" if report.ok else "applied_with_issues",
                "root": str(plan.root),
                "adapter": plan.adapter,
                "files": files,
                "diff": diff,
                "doctor": _report_document(report),
            }
        )
    else:
        print(translate("Initialization applied."))
        _print_report(report, verbose=arguments.verbose)
    return 0 if report.ok else 2


def _run_doctor(arguments: argparse.Namespace) -> int:
    report = Doctor(arguments.root).run()
    if arguments.json:
        _emit_json(_report_document(report))
    else:
        _print_report(report, verbose=arguments.verbose)
    return 0 if report.ok else 2


def _run_integration(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    if arguments.integration_command == "list":
        entries = []
        for adapter_id in ADAPTER_TYPES:
            adapter = adapter_for(
                adapter_id, arguments.root, environment=os.environ
            )
            detection = adapter.detect_client()
            model = adapter.detect_model()
            probe = adapter.capability_probe()
            health = adapter.health_check()
            entries.append(
                {
                    "id": adapter_id,
                    "detected": detection.detected,
                    "detection_evidence": list(detection.evidence),
                    "model": model.model_id,
                    "model_evidence": model.evidence,
                    "mode": probe.mode.value,
                    "can_intercept": probe.can_intercept,
                    "verified_e2e": probe.verified_e2e,
                    "health": health.state.value,
                    "installed": health.installed,
                    "message": health.message,
                }
            )
        if arguments.json:
            _emit_json(
                {
                    "root": str(Path(arguments.root).resolve()),
                    "adapters": entries,
                }
            )
        else:
            for entry in entries:
                detected = "detected" if entry["detected"] else "not detected"
                print(
                    f"{entry['id']}: {entry['mode']}, {detected}, "
                    f"{entry['health']} - {entry['message']}"
                )
        return 0

    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    manager = IntegrationManager(arguments.root)
    plan_method = getattr(manager, f"plan_{arguments.integration_command}")
    plan = plan_method(arguments.adapter)
    diff = plan.diff()
    files = [change.path for change in plan.changes]
    if not arguments.apply:
        if arguments.json:
            _emit_json(
                {
                    "status": "planned",
                    "operation": plan.operation,
                    "adapter": plan.adapter,
                    "root": str(plan.root),
                    "files": files,
                    "diff": diff,
                }
            )
        else:
            print(
                diff
                if diff
                else translate("No integration changes are required.")
            )
        return 0

    if not arguments.json:
        print(
            diff if diff else translate("No integration changes are required.")
        )
    if not arguments.yes:
        if arguments.json:
            raise ValueError("JSON apply requires --yes to avoid an interactive prompt")
        answer = input_fn(
            translate("Apply the reviewed integration changes? [y/N] ")
        )
        if answer.strip().lower() not in {"y", "yes"}:
            print(translate("Integration change cancelled; no files were written."))
            return 1
    manager.apply(plan)
    if arguments.json:
        _emit_json(
            {
                "status": "applied",
                "operation": plan.operation,
                "adapter": plan.adapter,
                "root": str(plan.root),
                "files": files,
                "diff": diff,
            }
        )
    else:
        print(translate("Integration change applied."))
    return 0


def _run_module(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    manager = ModuleManager(arguments.root)
    if arguments.module_command == "list":
        statuses = [
            {
                "id": status.manifest.id,
                "version": status.manifest.version,
                "source": status.source,
                "type": status.manifest.module_type.value,
                "state": "enabled" if status.enabled else "installed",
                "activation_policy": status.activation_policy.value,
                "sha256": status.manifest.sha256,
            }
            for status in manager.list()
        ]
        if arguments.json:
            _emit_json({"root": str(manager.root), "modules": statuses})
        else:
            for status in statuses:
                print(
                    f"{status['id']} {status['version']}: {status['state']}, "
                    f"{status['activation_policy']}, {status['source']}"
                )
        return 0

    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    if arguments.module_command == "enable":
        policy = (
            ActivationPolicy(arguments.policy) if arguments.policy is not None else None
        )
        plan = manager.plan_enable(
            arguments.module_id,
            policy=policy,
            local_manifest=arguments.local_manifest,
        )
    elif arguments.module_command == "trial":
        plan = manager.plan_trial(
            arguments.module_id, matching_tasks=arguments.tasks
        )
    elif arguments.module_command == "trial-result":
        plan = manager.plan_record_trial_result(
            arguments.module_id,
            task_id=arguments.task,
            evidence_ref=arguments.evidence_ref,
            beneficial=arguments.result == "beneficial",
            overhead_ms=arguments.overhead_ms,
        )
    else:
        method = getattr(manager, f"plan_{arguments.module_command}")
        plan = method(arguments.module_id)
    return _apply_reviewed_plan(
        plan=plan,
        apply_fn=manager.apply,
        apply_requested=arguments.apply,
        assume_yes=arguments.yes,
        json_output=arguments.json,
        input_fn=input_fn,
        kind="module",
    )


def _run_template(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    catalog = TemplateCatalog(arguments.root)
    if arguments.template_command == "list":
        templates = [
            {
                "id": template.id,
                "version": template.version,
                "sha256": template.sha256,
                "permissions": [],
            }
            for template in catalog.list()
        ]
        if arguments.json:
            _emit_json({"root": str(catalog.root), "templates": templates})
        else:
            for template in templates:
                print(f"{template['id']} {template['version']}: inert content")
        return 0
    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    plan = catalog.plan_render(arguments.template_id, arguments.output)
    return _apply_reviewed_plan(
        plan=plan,
        apply_fn=catalog.apply,
        apply_requested=arguments.apply,
        assume_yes=arguments.yes,
        json_output=arguments.json,
        input_fn=input_fn,
        kind="template",
    )


def _run_feedback(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    configuration = FeedbackConfiguration(arguments.root)
    current = configuration.current()
    if arguments.feedback_command == "show":
        data_root = arguments.data_root or _default_data_root()
        locator = StorageLocator(arguments.root, data_root)
        force_user_data = arguments.data_root is not None
        project_data = locator.location(
            force_user_data=force_user_data
        ).project_data
        store = FeedbackStore(
            None,
            None,
            project_data=project_data,
            mode=FeedbackMode(current["mode"]),
            analysis_policy=AnalysisPolicy(current["analysis_policy"]),
            include_token_usage=bool(current["include_token_usage"]),
            storage_locator=locator,
            force_user_data=force_user_data,
        )
        episodes = store.list()
        document = {
            "root": str(configuration.root),
            "feedback": current,
            "episode_count": len(episodes),
            "episodes": list(episodes) if arguments.verbose else [],
            "telemetry": "disabled",
        }
        if arguments.json:
            _emit_json(document)
        else:
            print(
                f"Feedback: {current['mode']} ({current['analysis_policy']}), "
                f"{len(episodes)} local episodes; telemetry disabled."
            )
        return 0
    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    policy = (
        AnalysisPolicy(arguments.analysis_policy)
        if arguments.analysis_policy is not None
        else None
    )
    plan = configuration.plan_mode(FeedbackMode(arguments.mode), analysis_policy=policy)
    return _apply_reviewed_plan(
        plan=plan,
        apply_fn=configuration.apply,
        apply_requested=arguments.apply,
        assume_yes=arguments.yes,
        json_output=arguments.json,
        input_fn=input_fn,
        kind="feedback",
    )


def _run_suggest(arguments: argparse.Namespace) -> int:
    configuration = FeedbackConfiguration(arguments.root)
    current = configuration.current()
    data_root = arguments.data_root or _default_data_root()
    locator = StorageLocator(arguments.root, data_root)
    force_user_data = arguments.data_root is not None
    project_data = locator.location(
        force_user_data=force_user_data
    ).project_data
    store = FeedbackStore(
        None,
        None,
        project_data=project_data,
        mode=FeedbackMode(current["mode"]),
        analysis_policy=AnalysisPolicy(current["analysis_policy"]),
        include_token_usage=bool(current["include_token_usage"]),
        storage_locator=locator,
        force_user_data=force_user_data,
    )
    observations: list[EffectObservation] = []
    for episode in store.list():
        failure = episode.get("failure")
        primary = (
            FailureKind(failure["primary"])
            if isinstance(failure, dict)
            else FailureKind.UNKNOWN
        )
        for module_id in episode.get("module_ids", []):
            observations.append(
                EffectObservation(
                    task_id=episode["task_id"],
                    target=RecommendationTarget.MODULE,
                    target_id=module_id,
                    failure_kind=primary,
                    beneficial=episode["result"]
                    in {"completed", "accepted_with_risk"},
                    high_impact=primary is FailureKind.PRODUCT_REGRESSION,
                    evidence_ref=f"episode:{episode['episode_id']}",
                    overhead_ms=int(episode["duration_ms"]),
                )
            )
    recommendations = RecommendationEngine().evaluate(tuple(observations))
    documents = []
    for recommendation in recommendations:
        config_diff: str | None = None
        if recommendation.maturity is Maturity.RECOMMENDATION:
            try:
                config_diff = configuration.accept_recommendation(
                    recommendation
                ).diff()
            except ValueError:
                config_diff = None
        documents.append(
            {
                "key": recommendation.key,
                "maturity": recommendation.maturity.value,
                "target": recommendation.target.value,
                "target_id": recommendation.target_id,
                "evidence_refs": list(recommendation.evidence_refs),
                "counterexamples": list(recommendation.counterexamples),
                "expected_overhead_ms": recommendation.expected_overhead_ms,
                "improvement_metric": recommendation.improvement_metric,
                "confidence": recommendation.confidence,
                "rollback": recommendation.rollback,
                "matching_tasks": recommendation.matching_tasks,
                "config_diff": config_diff,
            }
        )
    if arguments.json:
        _emit_json({"root": str(configuration.root), "suggestions": documents})
    elif not documents:
        print(translate("No evidence has matured into a suggestion."))
    else:
        for item in documents:
            print(
                f"{item['maturity']}: {item['target']} {item['target_id']} "
                f"({item['matching_tasks']} matching tasks)"
            )
    return 0


def _default_data_root() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured) / "harness"
    return Path.home() / ".local/share/harness"


def _selected_project_data(root: Path, data_root: Path | None) -> Path:
    locator = StorageLocator(root, data_root or _default_data_root())
    return locator.location(force_user_data=data_root is not None).project_data


def _run_storage(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    data_root = getattr(arguments, "data_root", None)
    locator = StorageLocator(
        arguments.root, data_root or _default_data_root()
    )
    location = locator.location(force_user_data=data_root is not None)
    document: dict[str, Any]
    if arguments.storage_command == "mode":
        document = {
            "mode": location.mode.value,
            "project_data": str(location.project_data),
            "scope": "local-clone",
        }
        if arguments.json:
            _emit_json(document)
        else:
            print(
                translate("Storage mode: {mode}").format(mode=document["mode"])
            )
            print(
                translate("Project data: {project_data}").format(
                    project_data=document["project_data"]
                )
            )
            if arguments.verbose:
                print(translate("Scope: {scope}").format(scope=document["scope"]))
        return 0
    if arguments.storage_command == "migrate":
        if arguments.yes and not arguments.apply:
            raise ValueError("--yes requires --apply")
        migrator = StorageMigrator(locator)
        plan = migrator.plan(
            StorageMode(arguments.target_mode), rollback=arguments.rollback
        )
        document = {
            "status": (
                "rolled_back"
                if arguments.apply and plan.reuse_target
                else "applied" if arguments.apply else "planned"
            ),
            "source_mode": plan.source_mode.value,
            "target_mode": plan.target_mode.value,
            "source": str(plan.source),
            "target": str(plan.target),
            "item_count": len(plan.items),
            "bytes_to_copy": plan.bytes_to_copy,
            "conflicts": list(plan.conflicts),
            "source_retained": plan.source.exists(),
            "rollback": plan.reuse_target,
        }
        if arguments.apply:
            review_document = {**document, "status": "planned"}
            if arguments.json:
                print(
                    json.dumps(review_document, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                )
            else:
                _print_storage_migration(review_document, verbose=True)
        if arguments.apply and not _confirm_mutation(
            "storage migrate", arguments, input_fn
        ):
            return 1
        if arguments.apply:
            migrator.apply(plan)
        if arguments.json:
            _emit_json(document)
        else:
            _print_storage_migration(document, verbose=arguments.verbose)
        return 0
    manager = StorageManager(
        location.project_data,
        storage_locator=locator,
        force_user_data=data_root is not None,
    )
    if arguments.storage_command == "status":
        status = manager.status()
        document = {
            "root": str(manager.root),
            "item_count": status.item_count,
            "active_count": status.active_count,
            "pinned_count": status.pinned_count,
            "expired_count": status.expired_count,
            "bytes_on_disk": status.bytes_on_disk,
        }
        if arguments.json:
            _emit_json(document)
        else:
            print(
                translate(
                    "{items} items, {bytes} bytes; {active} active, "
                    "{pinned} pinned."
                ).format(
                    items=status.item_count,
                    bytes=status.bytes_on_disk,
                    active=status.active_count,
                    pinned=status.pinned_count,
                )
            )
        return 0
    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    if arguments.storage_command == "prune":
        prune_plan = manager.plan_prune()
        document = {
            "status": "applied" if arguments.apply else "planned",
            "item_ids": list(prune_plan.item_ids),
            "paths": list(prune_plan.paths),
            "bytes_reclaimable": prune_plan.bytes_reclaimable,
        }
        if arguments.apply and not _confirm_mutation(
            "storage prune", arguments, input_fn
        ):
            return 1
        if arguments.apply:
            manager.apply_prune(prune_plan)
        if arguments.json:
            _emit_json(document)
        else:
            print(
                translate(
                    "{items} items, {bytes} bytes pruned."
                    if arguments.apply
                    else "{items} items, {bytes} bytes eligible for pruning."
                ).format(
                    items=len(prune_plan.item_ids),
                    bytes=prune_plan.bytes_reclaimable,
                )
            )
        return 0
    pin_plan = manager.plan_pin(
        arguments.item_id,
        recommendation_ref=arguments.recommendation_ref,
    )
    document = {
        "status": "applied" if arguments.apply else "planned",
        "item_id": pin_plan.item_id,
        "index": "storage-index.json",
    }
    if arguments.apply and not _confirm_mutation("storage pin", arguments, input_fn):
        return 1
    if arguments.apply:
        manager.apply_pin(pin_plan)
    if arguments.json:
        _emit_json(document)
    else:
        print(
            translate(
                "Storage pin applied."
                if arguments.apply
                else "Storage pin planned."
            )
        )
    return 0


def _run_export(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    if arguments.yes and not arguments.apply:
        raise ValueError("--yes requires --apply")
    manager = ExportManager(
        arguments.root,
        _selected_project_data(arguments.root, arguments.data_root),
    )
    plan = manager.plan(arguments.output)
    document = {
        "status": "applied" if arguments.apply else "planned",
        **plan.summary(),
    }
    if arguments.apply and not _confirm_mutation("export", arguments, input_fn):
        return 1
    if arguments.apply:
        manager.apply(plan)
    if arguments.json:
        _emit_json(document)
    else:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


def _run_upgrade(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    data_root = arguments.data_root or _default_data_root()
    locator = StorageLocator(arguments.root, data_root)
    force_user_data = arguments.data_root is not None
    project_data = locator.location(
        force_user_data=force_user_data
    ).project_data
    manager = UpgradeManager(
        arguments.root,
        project_data=project_data,
        storage_locator=locator,
        force_user_data=force_user_data,
    )
    if arguments.upgrade_command == "check":
        status = manager.check()
        document = {
            "configured_version": status.configured_version,
            "runtime_version": status.runtime_version,
            "compatible": status.compatible,
            "needs_upgrade": status.needs_upgrade,
            "message": status.message,
        }
        if arguments.json:
            _emit_json(document)
        else:
            print(status.message)
        return 0 if status.compatible else 2
    if arguments.upgrade_command == "rollback":
        plan = manager.plan_rollback(arguments.recovery_id)
        apply_fn = manager.apply_rollback
    else:
        plan = manager.plan()
        apply_fn = manager.apply
    if arguments.upgrade_command == "plan":
        if arguments.json:
            _emit_json(
                {
                    "status": "planned",
                    "recovery_id": plan.recovery_id,
                    "files": [change.path for change in plan.changes],
                    "risks": list(plan.risks),
                    "module_changes": list(plan.module_changes),
                    "diff": plan.diff(),
                }
            )
        else:
            print(
                plan.diff()
                if plan.diff()
                else translate("No upgrade changes are required.")
            )
        return 0
    return _apply_reviewed_plan(
        plan=plan,
        apply_fn=apply_fn,
        apply_requested=True,
        assume_yes=arguments.yes,
        json_output=arguments.json,
        input_fn=input_fn,
        kind="upgrade",
    )


def _run_self(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    manager = SelfManager()
    document: dict[str, Any]
    if arguments.self_command == "update":
        update_result = manager.update(arguments.version)
        document = {
            "status": "updated",
            "previous_version": update_result.previous_version,
            "version": update_result.version,
            "binary_path": str(update_result.binary_path),
            "backup_path": str(update_result.backup_path),
            "cleanup_pending": [
                str(path) for path in update_result.cleanup_pending
            ],
        }
        human_message = translate(
            "Updated Adaptive Harness from {previous} to {version}."
        ).format(
            previous=update_result.previous_version,
            version=update_result.version,
        )
    else:
        uninstall_plan = manager.plan_uninstall(purge_data=arguments.purge_data)
        review_document = {
            "status": "planned",
            "binary_path": str(uninstall_plan.manifest.binary_path),
            "runtime_path": str(uninstall_plan.manifest.runtime_path),
            "runtime_root": str(uninstall_plan.runtime_root),
            "launcher_backup": str(uninstall_plan.launcher_backup),
            "affected_paths": [
                str(path) for path in uninstall_plan.affected_paths
            ],
            "manifest_path": str(manager.manifest_path),
            "path_profile": (
                str(uninstall_plan.manifest.path_profile)
                if uninstall_plan.manifest.path_profile is not None
                else None
            ),
            "data_root": str(uninstall_plan.manifest.data_root),
            "purge_data": uninstall_plan.purge_data,
            "profile_diff": uninstall_plan.profile_diff,
        }
        if arguments.json:
            print(
                json.dumps(review_document, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
        else:
            _print_uninstall_plan(review_document)
        if not arguments.yes:
            if arguments.json:
                raise ValueError("JSON standalone uninstall requires --yes")
            answer = input_fn(
                translate("Apply the reviewed standalone uninstall? [y/N] ")
            )
            if answer.strip().lower() not in {"y", "yes"}:
                print(translate("Standalone uninstall cancelled; no data was changed."))
                return 1
        uninstall_result = manager.uninstall(uninstall_plan)
        document = {
            "status": "uninstalled",
            "binary_path": str(uninstall_result.binary_path),
            "data_root": str(uninstall_result.data_root),
            "data_purged": uninstall_result.data_purged,
            "cleanup_pending": [
                str(path) for path in uninstall_result.cleanup_pending
            ],
        }
        if uninstall_result.data_purged:
            human_message = translate(
                "Uninstalled Adaptive Harness and purged local records."
            )
        elif uninstall_plan.purge_data:
            human_message = translate(
                "Uninstalled Adaptive Harness; local data purge is incomplete."
            )
        else:
            human_message = translate(
                "Uninstalled Adaptive Harness; local records were preserved."
            )
    if arguments.json:
        _emit_json(document)
    else:
        print(human_message)
        if document["cleanup_pending"]:
            print(
                translate("Cleanup pending: {paths}").format(
                    paths=", ".join(document["cleanup_pending"])
                )
            )
        if arguments.verbose:
            print(
                translate("Launcher: {path}").format(path=document["binary_path"])
            )
            if arguments.self_command == "update":
                print(
                    translate("Previous Runtime: {path}").format(
                        path=document["backup_path"]
                    )
                )
            else:
                print(
                    translate("Data root: {path}").format(path=document["data_root"])
                )
    return 0


def _print_uninstall_plan(document: dict[str, Any]) -> None:
    print(translate("Standalone uninstall plan."))
    print(translate("Launcher: {path}").format(path=document["binary_path"]))
    print(translate("Runtime: {path}").format(path=document["runtime_path"]))
    print(
        translate("Runtime root: {path}").format(path=document["runtime_root"])
    )
    print(
        translate("Previous launcher: {path}").format(
            path=document["launcher_backup"]
        )
    )
    print(translate("Manifest: {path}").format(path=document["manifest_path"]))
    profile = document["path_profile"] or translate("none")
    print(translate("Shell profile: {path}").format(path=profile))
    print(translate("Data root: {path}").format(path=document["data_root"]))
    print(translate("Purge data: {purge}").format(purge=document["purge_data"]))
    if document["profile_diff"]:
        print(document["profile_diff"], end="")


def _run_task(arguments: argparse.Namespace) -> int:
    data_root = arguments.data_root or _default_data_root()
    service = TaskService(
        arguments.root,
        data_root=data_root,
        force_user_data=arguments.data_root is not None,
    )
    return _run_task_locked(arguments, service)


def _run_task_locked(
    arguments: argparse.Namespace,
    service: TaskService,
) -> int:
    if arguments.task_command == "start":
        record = service.start(
            goal=arguments.goal,
            allowed_scope=tuple(arguments.scope),
            requirements=tuple(arguments.requirement),
            capability_ids=tuple(arguments.capability),
            task_traits=tuple(arguments.trait),
            manually_requested_modules=tuple(arguments.module),
            task_id=arguments.id,
            timeout_seconds=arguments.timeout,
            retry_budget=arguments.retry_budget,
        )
        return _emit_task_record(record.to_payload(), arguments.json)
    if arguments.task_command == "show":
        return _emit_task_record(
            service.show(arguments.task_id).to_payload(), arguments.json
        )
    if arguments.task_command == "amend":
        record = service.amend(
            arguments.task_id,
            goal=arguments.goal,
            add_scope=tuple(arguments.add_scope),
            add_capabilities=tuple(arguments.add_capability),
        )
        return _emit_task_record(record.to_payload(), arguments.json)
    if arguments.task_command == "cancel":
        record = service.cancel(arguments.task_id, reason=arguments.reason)
        return _emit_task_record(record.to_payload(), arguments.json)
    report = service.verify(
        arguments.task_id,
        accept_risk=arguments.task_command == "accept-risk",
        risk_reason=(
            arguments.reason if arguments.task_command == "accept-risk" else None
        ),
    )
    document = {
        "task_id": report.task_id,
        "envelope_revision": report.envelope_revision,
        "outcome": report.outcome,
        "acceptances": [
            {
                "id": item.acceptance_id,
                "status": item.status,
                "evidence_event_sequence": item.evidence_event_sequence,
            }
            for item in report.acceptances
        ],
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "waivable": issue.waivable,
            }
            for issue in report.issues
        ],
        "risk_reason": report.risk_reason,
    }
    if arguments.json:
        _emit_json(document)
    else:
        print(f"Task {report.task_id}: {report.outcome}")
        for issue in report.issues:
            print(f"- {issue.code}: {issue.message}")
    return 0 if report.outcome in {"completed", "accepted_with_risk"} else 2


def _emit_task_record(document: dict[str, Any], json_output: bool) -> int:
    if json_output:
        _emit_json(document)
    else:
        envelope = document["envelope_revisions"][-1]
        print(
            f"Task {document['task_id']}: {document['state']} "
            f"(revision {envelope['revision']})"
        )
        print(envelope["goal"])
        for event in document.get("events", []):
            if event.get("type") != "module.activation":
                continue
            data = event["data"]
            print(f"Module {data['module_id']}: {data['state']} - {data['reason']}")
            if data.get("context") is not None:
                print(data["context"])
    return 0


def _run_config(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    manager = ConfigurationManager(arguments.root)
    if arguments.config_command == "explain":
        document = manager.explain()
        if arguments.json:
            _emit_json(document)
        else:
            print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    plan = manager.plan()
    if arguments.config_command == "diff":
        if arguments.json:
            _emit_json(
                {
                    "status": "planned",
                    "root": str(plan.root),
                    "files": [change.path for change in plan.changes],
                    "diff": plan.diff(),
                }
            )
        else:
            print(
                plan.diff()
                if plan.diff()
                else translate("Canonical configuration is current.")
            )
        return 0
    return _apply_reviewed_plan(
        plan=plan,
        apply_fn=manager.apply,
        apply_requested=True,
        assume_yes=arguments.yes,
        json_output=arguments.json,
        input_fn=input_fn,
        kind="config",
    )


def _run_adapter_hook(arguments: argparse.Namespace) -> int:
    if arguments.adapter != "claude-code":
        print("unsupported enforced adapter hook", file=sys.stderr)
        return 2
    try:
        value = json.loads(sys.stdin.read())
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("Adaptive Harness hook received invalid JSON", file=sys.stderr)
        return 2
    decision = decide_pre_tool_use(value)
    print(decision.to_json())
    return 0


def _run_capability(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> int:
    data_root = arguments.data_root or _default_data_root()
    service = TaskService(
        arguments.root,
        data_root=data_root,
        force_user_data=arguments.data_root is not None,
    )
    return _run_capability_locked(arguments, input_fn, service)


def _run_capability_locked(
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
    service: TaskService,
) -> int:
    if arguments.capability_command == "approve":
        if arguments.yes and not arguments.apply:
            raise ValueError("--yes requires --apply")
        approval = service.plan_approval(
            arguments.task,
            arguments.capability,
            max_uses=arguments.max_uses,
            valid_for_minutes=arguments.valid_for_minutes,
        )
        document = _approval_document(approval)
        if arguments.apply and not _confirm_mutation(
            "scoped capability approval", arguments, input_fn
        ):
            return 1
        if arguments.apply:
            service.grant_approval(approval)
        if arguments.json:
            _emit_json(
                {
                    "status": "applied" if arguments.apply else "planned",
                    **document,
                }
            )
        else:
            print(json.dumps(document, ensure_ascii=False, indent=2))
            print(
                translate("Approval granted.")
                if arguments.apply
                else translate("Approval planned; no task history was changed.")
            )
        return 0
    result = service.run_capability(arguments.task, arguments.capability)
    document = {
        "task_id": result.task_id,
        "capability_id": result.capability_id,
        "status": result.status,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "output_truncated": result.output_truncated,
        "stdout_artifact": str(result.stdout_artifact),
        "stderr_artifact": str(result.stderr_artifact),
        "authorization_event_sequence": result.authorization_event_sequence,
        "attempt": result.attempt,
    }
    if arguments.json:
        _emit_json(document)
    else:
        print(
            f"Capability {result.capability_id}: {result.status} "
            f"(exit {result.exit_code})"
        )
    return 0 if result.status == "succeeded" else 2


def _approval_document(approval: ScopedApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.id,
        "task_id": approval.task_id,
        "capability_id": approval.capability_id,
        "base_sha": approval.base_sha,
        "worktree_path": str(approval.worktree_path),
        "read_paths": list(approval.read_paths),
        "write_paths": list(approval.write_paths),
        "side_effects": [item.value for item in approval.side_effects],
        "network": approval.network.value,
        "listener": approval.listener,
        "environment": approval.environment.value,
        "max_uses": approval.max_uses,
        "expires_at": approval.expires_at.isoformat(),
    }


def _print_storage_migration(document: dict[str, Any], *, verbose: bool) -> None:
    status_message = (
        "Storage migration rolled back."
        if document["status"] == "rolled_back"
        else "Storage migration applied."
        if document["status"] == "applied"
        else "Storage migration planned."
    )
    print(translate(status_message))
    print(translate("Source: {source}").format(source=document["source"]))
    print(translate("Target: {target}").format(target=document["target"]))
    print(
        translate("Items: {items}; bytes: {bytes}").format(
            items=document["item_count"], bytes=document["bytes_to_copy"]
        )
    )
    if document["conflicts"]:
        print(
            translate("Conflicts: {conflicts}").format(
                conflicts=", ".join(document["conflicts"])
            )
        )
    if verbose:
        print(
            translate("Rollback: {rollback}; source retained: {retained}").format(
                rollback=document["rollback"],
                retained=document["source_retained"],
            )
        )


def _confirm_mutation(
    label: str,
    arguments: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> bool:
    if arguments.yes:
        return True
    if arguments.json:
        raise ValueError("JSON apply requires --yes to avoid an interactive prompt")
    localized_label = translate(label)
    answer = input_fn(
        translate("{label}: Apply the reviewed changes? [y/N] ").format(
            label=localized_label
        )
    )
    if answer.strip().lower() in {"y", "yes"}:
        return True
    print(
        translate("{label} cancelled; no data was changed.").format(
            label=localized_label.capitalize()
        )
    )
    return False


def _apply_reviewed_plan(
    *,
    plan: Any,
    apply_fn: Callable[[Any], None],
    apply_requested: bool,
    assume_yes: bool,
    json_output: bool,
    input_fn: Callable[[str], str],
    kind: str,
) -> int:
    diff = plan.diff()
    files = [change.path for change in plan.changes]
    operation = getattr(plan, "operation", "render")
    item_id = getattr(plan, "module_id", getattr(plan, "template_id", "unknown"))
    document = {
        "operation": operation,
        "id": item_id,
        "root": str(plan.root),
        "files": files,
        "diff": diff,
    }
    recovery_id = getattr(plan, "recovery_id", None)
    if recovery_id is not None:
        document["recovery_id"] = recovery_id
    if not apply_requested:
        if json_output:
            _emit_json({"status": "planned", **document})
        else:
            print(diff if diff else f"No {kind} changes are required.")
        return 0
    if not json_output:
        print(diff if diff else f"No {kind} changes are required.")
    if not assume_yes:
        if json_output:
            raise ValueError("JSON apply requires --yes to avoid an interactive prompt")
        answer = input_fn(
            f"{kind}: {translate('Apply the reviewed changes? [y/N] ')}"
        )
        if answer.strip().lower() not in {"y", "yes"}:
            print(f"{kind.capitalize()} change cancelled; no files were written.")
            return 1
    apply_fn(plan)
    if json_output:
        _emit_json({"status": "applied", **document})
    else:
        print(f"{kind.capitalize()} change applied.")
    return 0


def _report_document(report: DoctorReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "root": str(report.root),
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def _print_report(report: DoctorReport, *, verbose: bool) -> None:
    for check in report.checks:
        if verbose or check.status != "pass":
            print(f"[{check.status.upper()}] {check.name}: {check.message}")
    print(
        translate("Doctor passed.")
        if report.ok
        else translate("Doctor found blocking issues.")
    )


def _emit_json(document: dict[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _emit_error(message: str, *, json_output: bool) -> None:
    if json_output:
        _emit_json({"status": "error", "message": message})
    else:
        print(
            f"{translate('error')}: {translate_error(message)}",
            file=sys.stderr,
        )


__all__ = ["main"]
