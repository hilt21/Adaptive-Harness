# AGENTS.md

本文件适用于整个仓库。若子目录以后出现更具体的 `AGENTS.md`，以离目标文件最近的说明为准。

## 项目定位

Adaptive Harness 是本地优先、客户端无关的渐进式 Agent Harness。首版为 Python 3.12+ 的 CLI 与可嵌入 Runtime，核心目标是提供可信的工作区身份、能力边界、执行证据和完成门禁，而不是替模型增加固定的分析或开发流程。

当前仓库处于立项阶段。产品需求与架构的事实来源是 `docs/adaptive-harness-prd.md`；实现不得超出其中的 MVP 范围。需求存在歧义或需要改变产品边界时，先说明假设并请求确认，不要自行扩展。

## 不可破坏的约束

- 最小内核包含 Task Envelope、Capability Gateway、Executor、Verifier 和 Task Store；这些组件及其安全 invariant 不可关闭或被可选模块削弱。
- 按 capability、真实副作用和环境治理操作，不按任务名称或命令字符串推断安全性。
- 模型声明、自评和模板输出不能作为完成证据；`completed` 必须由当前任务的合法 acceptance evidence 支持。
- 权限取交集、限制取最严格值、deny 优先。未知字段、未知副作用和越界路径必须拒绝或升级审批。
- required acceptance 不得由模型自动删除；base SHA 在同一任务内不可改变；失败和不利证据不可覆盖。
- `observe` 与 `enforced` 必须如实标注。无法确认客户端具备拦截能力时使用 `observe`。
- 默认本地、离线、零遥测；不得回显 secret，也不得把未经脱敏的数据写入模型上下文或导出物。
- 初始化和配置修改必须先展示 diff、经用户确认后原子应用；失败时恢复原状态。

## 预期结构

实现代码放在 `src/adaptive_harness/`，按 PRD 中的边界组织：`core/`、`cli/`、`adapters/`、`scanners/`、`modules/`、`templates/`、`feedback/` 和 `schemas/`。测试放在 `tests/`，测试数据放在 `fixtures/`，设计与用户文档放在 `docs/`。

保持 CLI 与 Runtime 分层，通过稳定接口隔离 adapter、scanner 和 module。外部协议与配置使用英文及版本化 JSON Schema；面向用户的 CLI 输出可支持英文和简体中文。优先使用 Python 标准库，只有明确收益时才增加依赖。

## 工作方式

1. 开始修改前阅读 PRD 中与任务直接相关的章节，并检查工作区现状。
2. 将请求转成可验证的验收条件；修复缺陷时先添加能复现问题的测试。
3. 做满足验收条件的最小改动。不要顺手重构、格式化或清理无关代码。
4. 每条 requirement 都要有直接的 acceptance coverage；测试证据必须来自当前工作区和当前修改。
5. 完成前检查 diff 范围、失败输出、未跟踪文件和文档一致性，并明确报告未验证项。

## 构建与验证

使用 `uv` 管理开发环境。标准命令如下：

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src tests
```

完成修改前至少执行与改动相称的标准命令，并确认：

- 文档中的路径、CLI 名称和产品术语与 PRD 一致；
- JSON 示例可解析，新增 schema 示例与对应契约一致；
- `git diff --check` 通过；
- `git status --short` 中没有由本次任务意外生成的文件。

任何行为变更都必须有测试；合并前运行受影响测试，涉及核心 invariant、schema、adapter contract 或 CLI 契约时运行完整测试套件。

## 代码与提交边界

- 使用类型标注，保持函数和模块职责单一；为外部契约、状态转换和安全判断写清楚的错误信息。
- 只捕获能够处理的异常，不用宽泛异常或静默回退掩盖 record 损坏、权限问题和验证失败。
- 不提交本地任务记录、日志、artifact、secret、绝对路径或个人环境配置。
- 不修改用户已有的无关改动，不执行破坏性 Git 操作，不在未经明确要求时提交、推送或创建发布物。
