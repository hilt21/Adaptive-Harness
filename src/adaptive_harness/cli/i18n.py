"""Small deterministic catalog for CLI-only human messages."""

from __future__ import annotations

import os

SUPPORTED_LOCALES = ("en-US", "zh-CN")

_locale = "en-US"

_ZH_CN = {
    "Adaptive Harness command-line interface": "Adaptive Harness 命令行界面",
    "plan or apply deterministic project initialization": (
        "规划或应用确定性的项目初始化"
    ),
    "validate Harness configuration and workspace health": (
        "验证 Harness 配置和工作区健康状态"
    ),
    "inspect or manage coding-client integrations": "查看或管理编码客户端集成",
    "inspect and manage optional modules": "查看和管理可选模块",
    "list or explicitly render inert templates": "列出或显式渲染无执行权模板",
    "inspect or configure local feedback": "查看或配置本地反馈",
    "derive local recommendations from mature evidence": "从成熟证据生成本地建议",
    "inspect, prune, or pin local Harness data": "查看、清理或固定本地 Harness 数据",
    "plan or create a redacted local support export": "规划或创建脱敏的本地支持导出",
    "check, plan, apply, or rollback configuration upgrades": (
        "检查、规划、应用或回滚配置升级"
    ),
    "start, inspect, amend, cancel, or verify governed tasks": (
        "启动、查看、修订、取消或验证受治理任务"
    ),
    "explain, diff, or rebuild canonical configuration": (
        "解释、比较或重建 canonical 配置"
    ),
    "run declared operations through Gateway and Executor": (
        "通过 Gateway 和 Executor 运行已声明操作"
    ),
    "No initialization changes are required.": "无需初始化变更。",
    "Initialization cancelled; no files were written.": "初始化已取消；未写入文件。",
    "Initialization applied.": "初始化已应用。",
    "Apply the reviewed initialization changes? [y/N] ": (
        "应用已审阅的初始化变更？[y/N] "
    ),
    "No integration changes are required.": "无需集成变更。",
    "Integration change cancelled; no files were written.": (
        "集成变更已取消；未写入文件。"
    ),
    "Integration change applied.": "集成变更已应用。",
    "Apply the reviewed integration changes? [y/N] ": "应用已审阅的集成变更？[y/N] ",
    "Apply the reviewed changes? [y/N] ": "应用已审阅的变更？[y/N] ",
    "No evidence has matured into a suggestion.": "尚无证据成熟为建议。",
    "No upgrade changes are required.": "无需升级变更。",
    "Canonical configuration is current.": "Canonical 配置已是最新。",
    "Approval granted.": "审批已授予。",
    "Approval planned; no task history was changed.": (
        "审批已规划；任务历史未发生变化。"
    ),
    "Doctor passed.": "Doctor 检查通过。",
    "Doctor found blocking issues.": "Doctor 发现阻断问题。",
    "error": "错误",
    "cancelled; no data was changed.": "已取消；数据未发生变化。",
    "change cancelled; no files were written.": "变更已取消；未写入文件。",
    "change applied.": "变更已应用。",
}


def resolve_locale(arguments: tuple[str, ...]) -> str:
    for index, value in enumerate(arguments):
        if value == "--locale" and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith("--locale="):
            return value.split("=", 1)[1]
    environment = os.environ.get("LC_ALL") or os.environ.get("LANG", "")
    return "zh-CN" if environment.lower().startswith("zh") else "en-US"


def set_locale(locale: str) -> None:
    global _locale
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    _locale = locale


def translate(message: str) -> str:
    return _ZH_CN.get(message, message) if _locale == "zh-CN" else message


__all__ = ["SUPPORTED_LOCALES", "resolve_locale", "set_locale", "translate"]
