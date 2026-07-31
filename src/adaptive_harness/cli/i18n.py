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
    "inspect, migrate, prune, or pin local Harness data": (
        "查看、迁移、清理或固定本地 Harness 数据"
    ),
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
    "Apply the reviewed standalone uninstall? [y/N] ": (
        "应用已审阅的独立安装卸载？[y/N] "
    ),
    "Standalone uninstall cancelled; no data was changed.": (
        "独立安装卸载已取消；数据未发生变化。"
    ),
    "show local storage usage": "显示本地存储用量",
    "show local storage mode": "显示本地存储模式",
    "plan or apply a local storage migration": "规划或应用本地存储迁移",
    "plan local retention": "规划本地保留策略",
    "pin one local item": "固定一个本地条目",
    "download and verify a standalone Runtime update": "下载并验证独立 Runtime 更新",
    "remove a standalone Runtime installation": "移除独立 Runtime 安装",
    "manage a standalone Harness installation": "管理独立 Harness 安装",
    "Storage mode: {mode}": "存储模式：{mode}",
    "Project data: {project_data}": "项目数据：{project_data}",
    "Scope: {scope}": "作用域：{scope}",
    "Storage migration planned.": "存储迁移已规划。",
    "Storage migration applied.": "存储迁移已应用。",
    "Storage migration rolled back.": "存储迁移已回滚。",
    "Source: {source}": "源：{source}",
    "Target: {target}": "目标：{target}",
    "Items: {items}; bytes: {bytes}": "条目：{items}；字节：{bytes}",
    "Rollback: {rollback}; source retained: {retained}": (
        "回滚：{rollback}；源已保留：{retained}"
    ),
    "Updated Adaptive Harness from {previous} to {version}.": (
        "已将 Adaptive Harness 从 {previous} 更新到 {version}。"
    ),
    "Rolled back Adaptive Harness from {previous} to {version}.": (
        "已将 Adaptive Harness 从 {previous} 回滚到 {version}。"
    ),
    "Uninstalled Adaptive Harness and purged local records.": (
        "已卸载 Adaptive Harness 并彻底删除本地记录。"
    ),
    "Uninstalled Adaptive Harness; local records were preserved.": (
        "已卸载 Adaptive Harness；本地记录已保留。"
    ),
    "Uninstalled Adaptive Harness; local data purge is incomplete.": (
        "已卸载 Adaptive Harness；本地数据清理未完成。"
    ),
    "Launcher: {path}": "启动器：{path}",
    "Previous Runtime: {path}": "上一 Runtime：{path}",
    "Data root: {path}": "数据根目录：{path}",
    "Standalone uninstall plan.": "独立安装卸载计划。",
    "Runtime: {path}": "Runtime：{path}",
    "Manifest: {path}": "Manifest：{path}",
    "Shell profile: {path}": "Shell 配置：{path}",
    "Purge data: {purge}": "彻底删除数据：{purge}",
    "Runtime root: {path}": "Runtime 根目录：{path}",
    "Previous launcher: {path}": "上一启动器：{path}",
    "Conflicts: {conflicts}": "冲突：{conflicts}",
    "Cleanup pending: {paths}": "待清理：{paths}",
    "{items} items, {bytes} bytes; {active} active, {pinned} pinned.": (
        "{items} 个条目，{bytes} 字节；{active} 个活跃，{pinned} 个固定。"
    ),
    "{items} items, {bytes} bytes pruned.": (
        "已清理 {items} 个条目、{bytes} 字节。"
    ),
    "{items} items, {bytes} bytes eligible for pruning.": (
        "{items} 个条目，{bytes} 字节可清理。"
    ),
    "Storage pin applied.": "存储固定已应用。",
    "Storage pin planned.": "存储固定已规划。",
    "storage migrate": "存储迁移",
    "{label}: Apply the reviewed changes? [y/N] ": (
        "{label}：应用已审阅的变更？[y/N] "
    ),
    "{label} cancelled; no data was changed.": (
        "{label}已取消；数据未发生变化。"
    ),
    "none": "无",
    "options": "选项",
    "positional arguments": "位置参数",
    "show this help message and exit": "显示此帮助信息并退出",
    "usage: ": "用法：",
    "--yes requires --apply": "--yes 需要同时指定 --apply",
    (
        "standalone installation metadata is unavailable; "
        "use the original package manager"
    ): (
        "独立安装元数据不可用；请使用原包管理器"
    ),
}

_ZH_CN_ERROR_PREFIXES = {
    "standalone installation metadata is unavailable": "独立安装元数据不可用",
    "standalone installation metadata is invalid": "独立安装元数据无效",
    "standalone installation changed": "独立安装在审阅后发生了变化",
    "another standalone installation operation is in progress": (
        "另一个独立安装操作正在进行"
    ),
    "self-update version must be a semantic version": "自更新版本必须是语义化版本",
    "release checksum verification failed": "发布校验和验证失败",
    "storage mode is already": "存储模式已经是",
    "storage migration target has conflicts": "存储迁移目标存在冲突",
    "storage migration target already exists": "存储迁移目标已存在",
    "storage migration target changed": "存储迁移目标在审阅后发生了变化",
    "storage source changed": "存储源在审阅后发生了变化",
    "cannot migrate storage while task": "任务未结束，无法迁移存储：",
    "managed PATH block is malformed": "受管 PATH 块格式无效：",
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


def translate_error(message: str) -> str:
    if _locale != "zh-CN":
        return message
    exact = _ZH_CN.get(message)
    if exact is not None:
        return exact
    for source, target in _ZH_CN_ERROR_PREFIXES.items():
        if message.startswith(source):
            return target + message[len(source) :]
    return "操作失败；请使用 --json 查看机器可读错误详情"


__all__ = [
    "SUPPORTED_LOCALES",
    "resolve_locale",
    "set_locale",
    "translate",
    "translate_error",
]
