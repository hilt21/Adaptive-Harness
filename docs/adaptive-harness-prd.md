# Adaptive Harness 产品需求文档

- 状态：Draft — 已完成需求访谈，可用于新仓库立项与开发
- 目标仓库：`adaptive-harness`
- CLI：`adp-harness`
- Python 包：`adaptive-harness`
- 许可证：Apache-2.0

## 1. 产品摘要

Adaptive Harness 是一个本地优先、客户端无关的渐进式 Agent Harness 产品。它位于模型、Agent 客户端与项目工具之间，为软件开发任务提供真实工作区身份、明确能力边界、可信执行记录、不可伪造的完成条件，以及基于真实反馈的可选模块调整建议。

产品不负责教模型如何分析、规划或编码。模型越强，默认加载的规划与流程内容越少；工作区、权限、副作用、证据、审批和完成门禁始终保留。

首版采用“本地 CLI + 可嵌入 Runtime + 仓库内声明式配置”。不提供云端控制台，不默认上传任何数据。

## 2. 产品原则

1. 不教模型思考，只提供真实上下文。
2. 不相信模型自报，只相信可验证证据。
3. 不按任务名称治理，按实际 capability 和副作用治理。
4. 不用文档数量代表严谨，用不可绕过的 invariant 代表严谨。
5. 模型越强，Harness 越薄；权限边界、外部事实与 Completion Gate 不变。
6. 最小内核不可关闭，也不可被反馈闭环修改或削弱。
7. 可选能力渐进加载，模板显式调用，任何配置调整均需用户确认。
8. 本地优先、离线可用、默认零遥测。

## 3. 问题与机会

现有 Agent Harness 常见两类问题：

- 过度补偿模型能力：对每个任务强制分析、评审、实现、测试、交付五阶段，并生成多份重复文档。对强模型而言，这会增加 token、延迟和上下文干扰。
- 治理不足：命令仅以字符串存在，工作区、真实副作用、审批范围、需求覆盖和 evidence 归属无法被机器验证。模型即使能力很强，也不能靠推理消除外部状态与权限风险。

Adaptive Harness 将二者分离：最小内核只负责治理和事实；规划、测试辅助、上下文管理、多 Agent、实验指标和高级恢复作为可选模块；长计划、persona、handoff 等降级为无执行权模板。

## 4. 目标与成功指标

### 4.1 产品目标

- 用户在 10 分钟内为新仓库生成最小但足够的 Harness 配置。
- 每次开发任务都具有可恢复的任务边界、可信执行事件和需求级验收证据。
- 产品根据真实任务反馈提出可解释、可回滚的可选层调整建议。
- 建议只能影响可选模块和模板，不能改变最小内核。
- Harness 开销可见、可配置，并在强模型场景下保持轻量。

### 4.2 一级成功指标

- 初始化成功率：初始化后首个基准任务能够执行并形成有效 evidence。
- 可避免失败率：workspace、capability、环境、验证遗漏与 scope deviation 等 Harness 可处理失败的下降幅度。
- 建议采纳价值：被采纳建议在后续匹配任务中的可验证收益。
- 治理完整度：required acceptance、审批、diff scope 与 evidence 完整率。
- Harness 开销：额外时间、token、文件数量与人工介入。

不以规则、skills 或文档数量作为成功指标。

## 5. 目标用户

首版核心用户是使用 Codex、Claude Code 等编码 Agent 维护真实仓库的个人开发者或项目技术负责人。用户希望减少流程负担，同时保留权限、证据和完成门禁。

企业平台团队、集中策略管理员和跨组织审计人员不是首版核心用户。

## 6. 典型使用场景

### 6.1 新仓库初始化

用户先通过一条官方安装命令把自包含 Adaptive Harness CLI 安装为当前用户全局可用的 `adp-harness` 命令，再在新项目执行 `adp-harness init`。安装 Runtime 不扫描或修改当前仓库。系统扫描项目与客户端，匹配模型能力档案，生成最小内核配置、Agent 指令接入块、可选模块建议和未启用模板清单。用户查看配置、成本、权限和全部文件 diff 后确认写入。

### 6.2 已有仓库迁移

系统只读盘点已有 `AGENTS.md`、skills、hooks 和 Harness 文件，将内容分类为保留、可选模块、模板、冲突或未知。用户逐项选择，先以 `observe` Trial 运行，再决定是否启用 `enforced`。

### 6.3 日常 feature、变更与修复

Agent 收到开发请求后，adapter 自动创建 draft Task Envelope。Runtime 校验 workspace、scope、requirements、acceptance 与 capability。普通本地可逆任务按预授权自动开始；歧义或 capability 升级才请求用户确认。

### 6.4 反馈与 Harness 调整

任务结束后，minimal 模式只记录结构化事件和三档人工反馈，不调用模型。用户执行 `adp-harness suggest` 时才分析历史 episode，形成 Observation、Candidate 或 Recommendation。用户接受后先进入 Trial，验证收益后才能 promote。

## 7. 产品范围

### 7.1 MVP 范围

- Python 3.12+ Runtime 与 CLI。
- 五个最小内核组件。
- Generic CLI、Codex、Claude Code adapter。
- Node.js/TypeScript 和 Python 一等扫描器，其他项目通用模式。
- 三个仓库内 canonical Harness 状态配置文件。
- 对 `AGENTS.md`、`CLAUDE.md` 和客户端原生配置的受管接入。
- 可选模块三级加载、进程隔离协议和 Trial 生命周期。
- 无执行权模板系统。
- feedback `off/minimal/research`。
- failure taxonomy 与建议成熟度模型。
- macOS、Linux；WSL2 experimental。
- macOS/Linux `arm64` 与 `x86_64` 自包含发行构件和当前用户级安装器。
- 英文协议，英文/简体中文 CLI。

### 7.2 非目标

- 生成或修改业务代码。
- 托管、代理、训练或自动选择模型。
- 默认多 Agent 编排。
- 云端控制台、账号、团队同步和集中策略。
- 公共插件市场。
- 无审批的 Harness 自我修改。
- 替代 Git、CI、测试框架、OS sandbox、IAM 或 secret manager。
- 强制五阶段流程或每任务文档包。
- 将模型 confidence、自评或自然语言声明当作 evidence。
- 在 `observe` 模式下声称实现强制治理。

## 8. 最小内核

最小内核不可裁剪、不可关闭、不可被反馈系统修改。

### 8.1 Task Envelope

每个任务必须绑定：

- repository identity；
- 完整 base SHA；
- worktree path；
- 初始 dirty state；
- Harness/config/schema 版本；
- goal、non-goals、requirements；
- allowed scope；
- required acceptance；
- requested capabilities；
- timeout、retry 和预算；
- integration mode。

Envelope 支持 append-only revision。base SHA 不得在同一任务中改变。required acceptance 不得由模型自动删除。权限只能保持或升级。

### 8.2 Capability Gateway

Gateway 管理类型化 capability，不接受只有命令字符串的配置。每个 capability 至少声明：

- ID 与 argv；
- cwd；
- timeout；
- read/write paths；
- network/listener；
- side effects；
- reversibility；
- environment；
- approval policy；
- max executions；
- stop condition。

Gateway 在执行前验证 capability、workspace 和 policy。模型只能申请能力，不能扩大权限。

### 8.3 Executor

Executor 负责：

- workspace lease 与并发保护；
- sandbox；
- timeout、budget、attempt、取消与 stop condition；
- 启动并终止子进程；
- 记录实际 cwd、argv、时间、exit code 与副作用；
- stdout/stderr artifact；
- secret 脱敏与输出限流；
- 环境失败与产品失败的初步分类。

### 8.4 Verifier

Verifier 必须确认：

- 每个 requirement 有直接 acceptance coverage；
- required checks 全部具有合法终态；
- evidence 文件存在、属于当前任务且未越界；
- command evidence 来自可信 Executor；
- 实际 diff 在 allowed scope 内；
- workspace identity、lease 与 base SHA 仍有效；
- 没有未解决失败或未批准副作用。

模型声明“已完成”不能改变任务状态。

### 8.5 Task Store

Task Store 保存唯一 canonical task record：

- 原子写入；
- schema version；
- append-only event history；
- Task Envelope revisions；
- capability、approval、command、acceptance 与 artifact 引用；
- 中断恢复；
- `completed`、`accepted_with_risk`、`blocked`、`failed`、`abandoned` 等终态；
- 损坏检测与安全恢复。

## 9. 跨内核 Invariants

- 关键操作前重新验证 repository、SHA、worktree 和 lease。
- 权限取交集，限制取最严格；deny 优先。
- 高风险审批绑定 task、command、SHA、路径、副作用、次数和有效期。
- evidence 与 artifact 不得路径逃逸或通过符号链接越界。
- 完整日志保存在 artifact，模型只接收摘要。
- secret 不得回显给模型或写入未脱敏日志。
- 失败和不利证据不可删除或覆盖。
- `observe` 与 `enforced` 必须真实标注。
- 反馈、模块和模板不得降低内核 invariant。

## 10. 操作级风险策略

Fast/Standard/Controlled 不作为安全内核，只能是可选解释标签。Gateway 按操作治理：

- 只读操作：通常自动执行；
- 本地可逆代码修改：按项目预授权执行并记录 diff；
- 限定路径的隔离测试数据重建：策略允许时自动执行；
- 依赖、迁移、权限、凭证：升级策略；
- 生产或不可逆操作：必须人工审批。

首版采用有限声明式 JSON Policy，不引入脚本策略或通用规则引擎。未知字段、副作用和环境默认拒绝或要求审批。

配置优先级：

```text
内核 invariant
  > 用户全局安全策略
  > 项目安全策略
  > 模块权限声明
  > Task Envelope
  > 单次作用域审批
```

## 11. 结构化验收

每个 acceptance 至少包含：

```json
{
  "id": "filter-by-title",
  "required": true,
  "covers": ["REQ-001"],
  "command_id": "master-pane-unit",
  "expected": {"exit_code": 0},
  "timeout_seconds": 120,
  "retry_budget": 1,
  "evidence_type": "command_event"
}
```

`completed` 仅在全部 required acceptance 和 invariant 满足时成立。验证缺口只能进入 `accepted_with_risk`，不得伪装为 `completed`。

不可豁免项包括 workspace identity、越权审批、secret 泄漏、artifact 越界、未声明生产/不可逆副作用和 record 损坏。

## 12. 安装与项目初始化

### 12.1 产品安装

正式发行的首选渠道是 GitHub Releases 中版本化、带 checksum 与签名证明的自包含 CLI。官方安装脚本根据 OS、CPU 和 libc 选择构件，默认安装到当前用户的 `~/.local/bin/adp-harness`，不要求预装 Python、`pipx`、`uv` 或管理员权限：

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/hilt21/Adaptive-Harness/releases/latest/download/install.sh | sh
```

安装脚本必须可审查，并提供下载后检查再执行的两步替代方式。它只能下载版本化构件，必须在原子替换前验证 SHA-256；正式 Release 同时发布可验证的构建来源证明。校验失败必须终止，不得回退到其他来源。用户可显式固定版本。MVP 只提供当前用户级安装，不提供 system-wide 或 `sudo` 安装。

若 `~/.local/bin` 未在默认 shell 的干净新会话中生效，交互安装必须为 zsh、bash 或 fish 选择实际加载的配置文件，展示受管区块 diff，并经一次确认后原子写入；用户拒绝时安装整体终止且不保留半成品。未知 shell 不猜测 `~/.profile`。非交互安装只有在持久 PATH 已生效或显式设置高级开关 `HARNESS_CONFIRM_PATH=1` 时才能继续。安装完成前必须以最小环境启动干净默认 shell，分别在 HOME 与普通仓库验证 `command -v adp-harness` 精确指向 `~/.local/bin/adp-harness` 且 `--version` 成功；任一失败回滚 launcher、Runtime、manifest 与 shell 配置。当前终端无需被临时修改，用户打开新终端即可。

安装器必须拒绝 PATH 中更高优先级的同名命令、目标位置的未知文件及无法证明归属的安装，不得覆盖或静默 shadow。已验证的正常安装只提示用户运行 `adp-harness self update`，安装器不承担常规升级。manifest 能证明属于 Adaptive Harness 但 launcher 或 Runtime 损坏时，安装器展示 repair 计划并再次确认；manifest 缺失或损坏到无法证明归属时停止并要求人工检查。PATH 修改与 repair 必须幂等、原子且失败可恢复。

PyPI wheel 继续作为高级用户、下游打包者和开发者渠道：

```sh
pipx install adaptive-harness
# 或
uv tool install adaptive-harness
```

源码开发可使用 editable install。安装后，`adp-harness --help`、`adp-harness init` 和 `adp-harness doctor` 必须可直接执行。Runtime、可信执行器、官方 adapter、内置模块与模板属于已安装包，不复制到每个用户仓库。

Linux 安装器探测 Bubblewrap 和 user namespace，但不得自动使用 `sudo` 或系统包管理器安装依赖。缺失时必须说明基础 CLI 与 `observe` 可用、`enforced` 不可用，并提供用户确认后执行的发行版安装指引。只有客户端拦截与 OS sandbox 都通过当前主机验证时才能标记 `enforced`。

### 12.2 初始化流程

```text
deterministic scan
  → model/client profile
  → optional semantic analysis
  → candidate configuration and managed projections
  → user review of every file diff
  → transactional apply
  → doctor
```

初始化不得无确认写入或启用配置。Apply 必须先验证并暂存全部输出，再提交修改；任一写入失败时恢复初始化前状态。进程崩溃后必须能检测未完成事务并恢复或继续，不能遗留半配置仓库。

### 12.3 Project Profile

扫描结果区分：

- `observed`：工具直接发现；
- `declared`：用户或项目文档声明；
- `inferred`：模型推断，含来源与置信度；
- `unknown`：需要用户决定。

确定性扫描优先覆盖 Git、语言、框架、包管理器、lockfile、CI、测试命令、源码/测试/迁移目录、服务、数据库和现有 Agent/Harness 文件。模型只分析语义问题。

### 12.4 模型能力档案

模型配置依据版本化 capability profile，而非只按品牌或模型自报。档案描述工具调用、结构化输出、长任务恢复、上下文、已知失败模式和模块建议。未知模型使用保守 profile，但不能改变内核。

模型 profile 只能影响可选模块、模板、上下文和成本预算。

### 12.5 Canonical 配置与仓库输出

```text
.harness/
├── config.json
├── capabilities.json
└── modules.lock.json
```

- `config.json`：版本、profile 引用、项目 policy、模块策略、feedback、所选 adapter，以及受管投影的路径、版本与期望 hash。
- `capabilities.json`：类型化 capability 与副作用。
- `modules.lock.json`：模块/模板精确版本、来源与 hash。

以上三份 JSON 是唯一的 canonical Harness 状态配置，不是完整 Runtime 的源码副本。Runtime 在执行时读取它们并加载已安装包中的内核、adapter、模块与模板。

初始化还可以在用户确认后生成以下受管投影：

- 在所选客户端对应的 `AGENTS.md` 或 `CLAUDE.md` 中写入短接入块；
- 客户端实现 `enforced` 所必需的原生 hook、权限或 Gateway 配置；
- adapter 必需且可从 canonical 配置重建的薄 shim。

受管投影必须带稳定的开始/结束标记、生成器版本和内容 hash。初始化应保留已有文件与用户规则，只添加或更新 Harness 所有的区块；重复运行必须幂等，卸载集成只能删除受管内容。`adp-harness config diff|apply` 负责预览和重建投影，`adp-harness doctor` 负责检测缺失、漂移或冲突。

Agent 指令文件中的接入块保持简短，例如：

```md
<!-- adaptive-harness:start version="1" -->
Use Adaptive Harness for repository-changing tasks.
Start or resume the task through `"$HOME/.local/bin/adp-harness" task`, request capability
escalation through Harness, and run Harness verification before completion.
Treat Harness task status and executor evidence as authoritative.
<!-- adaptive-harness:end -->
```

Agent 指令接入块只负责引导客户端通过 Harness 开始任务、申请 capability 和验证完成，不能复制完整 policy，也不能被宣传为安全边界。真正的强制能力来自可拦截工具调用的 adapter；只有指令接入时必须标记为 `observe`。

不默认生成 README、skills、计划、示例文件或整套 Harness 脚本。用户显式渲染的模板属于用户内容，不属于 canonical Harness 状态。

## 13. Agent 集成模式

### 13.1 Enforced

仅在客户端能够通过 hook、权限回调、MCP Gateway 或受控执行环境拦截操作时启用。绕过 Gateway 必须阻止 Completion Gate。

### 13.2 Observe

记录已知操作并验证 diff，但不能保证 Agent 未绕过。产品必须显示“非强制模式”，不得声称强制治理。

初始化时探测客户端能力；无法确认时默认 `observe`。

### 13.3 Adapter Contract

```text
detect_client()
detect_model()
capability_probe()
install_integration()
uninstall_integration()
before_tool_call()
after_tool_call()
request_user_approval()
deliver_observation()
health_check()
```

adapter 不参与任务语义、risk policy 和完成判断。`enforced` adapter 故障时 fail closed；`observe` adapter 故障时明确降级。

MVP 支持 Generic CLI、Codex 与 Claude Code，至少一个真实实现 `enforced`。

### 13.4 接入物化

`install_integration()` 根据客户端能力生成最小接入投影：

- prompt-only 客户端：生成受管 `AGENTS.md` 或 `CLAUDE.md` 区块，模式为 `observe`；
- 支持 hook、权限回调或 Gateway 的客户端：同时生成客户端原生配置，经端到端验证后才可标记 `enforced`；
- Generic CLI：不假设存在 Agent 指令文件，由用户通过 `adp-harness task` 或受控启动入口显式进入 Harness。

终端交互可以依赖 `adp-harness` 的持久 PATH；受管客户端 hook 与接入投影必须使用 `"$HOME/.local/bin/adp-harness"`，不得依赖 GUI 应用是否继承 shell PATH，也不得提交某台机器的个人绝对路径。doctor 分别报告终端命令解析与客户端固定 launcher 的健康状态。

项目级接入投影应提交到版本控制，使其他贡献者获得同一入口；仅含缓存、临时 shim、日志或机器路径的输出必须进入本地数据目录，不得提交。所有投影均可由 canonical 配置和固定版本 Runtime 重建。

## 14. 渐进式可选模块

### 14.1 生命周期

```text
Installed → Enabled → Activated
```

- Installed：可发现，不加载内容。
- Enabled：项目批准，只读取轻量 manifest。
- Activated：当前任务匹配且策略允许时才加载，任务结束后卸载。

激活策略：`auto`、`suggest`、`manual`、`disabled`。

### 14.2 模块 Manifest

必须包含：ID、版本、category、适用/排除条件、所需 capability、上下文与运行成本、激活策略、成功指标、兼容范围和 rollback。

### 14.3 模块类型与隔离

- Declarative module：无代码，只提供规则、索引或建议。
- Executable module：独立进程运行，通过版本化 JSON 协议通信。

Executable module 不得 import Runtime 内部包；必须受 timeout、sandbox、输出限制和 capability policy 约束。返回统一的 `status`、`summary`、`next_actions` 和 `artifacts`。

官方模块默认从已安装包读取：声明式内容在任务激活时动态送入 Agent 上下文，可执行内容以隔离子进程运行，均不向用户仓库复制源码或脚本。本地模块保持在用户明确指定的位置，`modules.lock.json` 只记录固定来源和 hash。只有客户端协议要求项目级薄 shim 时才生成可重建投影。

### 14.4 来源

MVP 仅允许官方内置模块和用户指定的本地模块。禁止模型自动搜索安装、未固定 URL、运行时拉脚本和模板携带 hook。

## 15. 降级模板

模板是纯内容，具有以下边界：

- 无执行权；
- 无状态权；
- 无自动激活权；
- 不能修改 Envelope、policy、Completion Gate 或 canonical record；
- 输出不是 evidence，除非引用的 artifact 被独立验证；
- 使用后不会自动成为项目规则。

典型模板包括完整计划、Request Analysis、Review、TDD 清单、handoff、persona、详细失败分析和完整 rollback 文档。

模板不会在 `init` 或模块激活时自动写入。只有用户显式执行 `adp-harness template render <id> --output <path>` 后才创建文件；生成前显示 diff。渲染结果随即成为用户拥有的普通项目内容，可选择提交，Harness 不自动覆盖，除非用户再次确认渲染。

## 16. 反馈系统

### 16.1 模式

```json
{
  "feedback": {
    "mode": "minimal",
    "analysis_policy": "on_demand",
    "include_token_usage": true,
    "retention_days": 30
  }
}
```

- `off`：不创建反馈 episode、不询问、不分析；内核安全审计仍保留。
- `minimal`：本地结构化事件与三档反馈，不产生额外模型调用。默认模式。
- `research`：完整实验指标与模型辅助归因。

分析策略：`on_demand`、`scheduled`、`after_each_task`；默认 `on_demand`。

### 16.2 自动与人工反馈

自动记录模块/模板使用、capability、审批、command events、失败、coverage、diff、结果、耗时和可用的 token/cost。

人工反馈保持轻量：

- `有帮助 / 无明显影响 / 造成干扰`；
- 人工介入原因标签；
- 建议的接受、稍后或拒绝。

不采集模型思维过程、无限终端历史、未脱敏凭证或长篇强制复盘。

### 16.3 Failure Taxonomy

- `workspace_mismatch`
- `capability_denied`
- `environment_failure`
- `command_failure`
- `verification_gap`
- `product_regression`
- `scope_deviation`
- `model_reasoning_failure`
- `flaky_or_nondeterministic`
- `user_or_requirement_change`
- `unknown`

采用最早可观察主因，支持 contributing causes。只有重复且 Harness 可解决的失败才形成调整建议。

### 16.4 建议成熟度

```text
Observation → Candidate → Recommendation
```

- Observation：单次事实，不建议改配置。
- Candidate：至少两次同类事件，或一次高影响事件。
- Recommendation：至少三次相似任务重复，且适用条件、收益和成本明确。

每条建议包含证据引用、反例、预期开销、改善指标、置信度、回滚和目标模块/模板。拒绝建议进入冷却期。

### 16.5 调整范围

反馈系统只能建议：

- 启停、升级可选模块；
- 修改模块触发或激活策略；
- 推荐、隐藏模板；
- 调整反馈和保留策略。

不得修改内核、降低权限、放宽 Completion Gate 或绕过审批。

### 16.6 Trial

接受 Recommendation 后先进入 Trial：

```text
proposed → approved → trial → promoted
                         ↘ rejected / rolled_back
```

默认 3 个匹配任务。Trial 声明指标、成本预算、对照、停止条件和回滚 diff。无收益或超预算时建议回滚。模型或项目结构明显变化后，旧结论必须重新验证。

## 17. CLI 需求

```text
adp-harness init
adp-harness doctor
adp-harness integration list|install|uninstall|repair

adp-harness task start|show|amend|cancel|verify|accept-risk
adp-harness module list|enable|disable|trial|promote|rollback
adp-harness template list|render
adp-harness feedback show|mode
adp-harness suggest
adp-harness config explain|diff|apply
adp-harness storage status|prune|pin|mode|migrate
adp-harness export
adp-harness upgrade check|plan|apply|rollback
adp-harness self update|rollback|uninstall
```

所有命令必须支持人类摘要和稳定 JSON 输出、稳定 exit code、artifact 引用和 `--verbose`。修改命令先显示 canonical 配置与受管投影的完整 diff，再以可恢复事务应用。不能提供直接编辑 canonical record 的入口。

`adp-harness self update|rollback|uninstall` 只管理官方自包含 Runtime。Runtime 不后台检查或自动升级；`self update` 这一显式命令本身即授权更新，不再要求第二个 `--apply` 或确认提示，默认安装最新 stable，也可通过 `--version` 固定。更新必须先验证 installation manifest、launcher、当前 Runtime 与正式构件 checksum，原子切换后再次通过稳定 launcher 自检，失败时自动恢复。`self rollback` 离线切回唯一一个已验证的上一 Runtime，成功后消费恢复点以避免来回翻转。`adp-harness upgrade` 只管理当前仓库配置与受管投影。pipx、uv 或 PyPI 安装必须交由原包管理器更新和卸载。Runtime 卸载默认保留本地数据与所有仓库配置；彻底删除数据需要显式 `--purge-data` 和二次确认。

## 18. 数据与存储

### 18.1 仓库与本地边界

仓库保存三份 canonical Harness 状态配置，以及用户确认的可复现接入投影，例如 `AGENTS.md`/`CLAUDE.md` 受管区块和客户端原生项目配置。用户显式渲染的模板是普通项目内容，不是 Harness 状态。

已安装包保存 Runtime、可信执行器、官方 adapter、内置模块与模板源码。本地数据目录保存 Project Profile、Task Envelope、record、episode、日志、指标、反馈、缓存和含机器路径的临时 shim。运行时产物不得写入已提交的接入投影。

默认位置遵循 XDG 数据目录，未配置时为 `~/.local/share/harness/`。项目以 repository identity 隔离，同一仓库不同 worktree 共享项目统计但隔离任务状态。

用户可为当前 clone 显式选择高级 `repository-local` 模式。该选择保存在本地 Git 配置中，不写入可提交的 canonical 配置，也不进入首次 `init` 问答。运行数据保存到 Git common directory 下的 `adaptive-harness/`，普通仓库通常为 `.git/adaptive-harness/`；不得写入工作区 `.harness/`、污染 Git diff 或向普通 capability 暴露记录写权限。linked worktree 共享该位置，独立 clone 各自选择。

存储模式切换必须先拒绝活动、blocked 或等待审批的任务，展示源、目标、记录数、大小和冲突。Apply 先复制全部数据并验证完整性，再原子切换本地模式；默认保留源副本供回滚。目标已有记录时不得自动合并，失败或中断后继续使用原位置。`--data-root` 只用于测试、CI 和故障排查，不是持久化项目配置。

### 18.2 保留策略

- 活跃 record：任务结束前永久；
- 完成 record：30 天；
- 大 artifact：7 天；
- approval 与 `accepted_with_risk`：90 天；
- minimal episode：30 天；
- research episode：180 天；
- Recommendation 引用 episode 自动 pin。

清理不得改写事件历史；被删除 artifact 标记为 expired。运行中、blocked 或等待审批的任务不得清理。

### 18.3 跨项目学习

默认项目隔离。可选本机跨项目学习只共享抽象 failure pattern、模块 ID、profile 版本和效果指标，不共享文件、代码、日志、remote 或 artifact。跨项目结论最多成为 Candidate，目标项目仍需 Trial。

## 19. 隐私与遥测

- 默认零遥测、零上传。
- `feedback.mode` 只控制本地功能。
- 崩溃报告需用户确认。
- `adp-harness export` 先展示字段和文件。
- 导出默认移除 secret、绝对路径、remote、用户名和环境变量。
- 云端团队能力必须是未来独立 opt-in 功能。

## 20. 威胁模型

MVP 防御 Agent 错误、配置错误、模块越权、approval 复用、路径/符号链接逃逸、record 损坏、日志泄密、输出耗尽、prompt injection 试图修改 policy，以及未声明网络/生产/不可逆操作。

MVP 不声称对抗已经控制同一系统用户的恶意攻击者，不替代 OS sandbox/IAM/secret manager，也不能静态证明任意脚本的全部副作用。

## 21. 离线行为

最小内核、CLI、确定性 init、doctor、task lifecycle、Gateway、Executor、Verifier、Task Store、本地模块和模板必须离线可用。

无模型时不做语义项目分析、自动 requirements 推断和建议生成，但已有策略继续执行。Harness 自身不得因遥测、许可证或更新检查阻止离线任务。

## 22. 性能与上下文预算

- `doctor`：2 秒内，不调用模型。
- 确定性 init：10 秒内。
- 模型语义 init：60 秒内，提前显示 token 上限。
- 本地 Task Store 操作：500 ms 内。
- Gateway 开销：命令时长 2% 内；短命令目标 100 ms 内。
- 固定内核上下文：800 tokens 内。
- 单任务激活模块上下文默认总计 2,000 tokens 内。
- minimal feedback：零额外模型调用。
- `suggest` 调用前显示预计 token/cost。

## 23. 平台与国际化

- 正式自包含构件支持 macOS `arm64`/`x86_64` 与 glibc Linux `arm64`/`x86_64`。
- Alpine/musl 可使用 Python wheel，但不属于首版自包含构件范围。
- WSL2 experimental；Windows 原生后置。
- CI 覆盖 Ubuntu LTS、稳定 macOS、两种 CPU 架构、Python 3.12/3.13。
- 协议、配置、error code 使用英文。
- CLI 人类输出支持 `en-US` 与 `zh-CN`。
- 日志原文不翻译；adapter 不能依赖自然语言解析。

## 24. 版本、升级与回滚

- Runtime 使用语义化版本。
- 自包含 Runtime 只在用户执行 `adp-harness self update` 时联网；不后台检查或自动更新。
- standalone installation manifest 使用 schema `2.0`，记录稳定 `product_id`、channel/version/path 关系、launcher SHA-256、Runtime SHA-256、release archive SHA-256，以及 PATH profile、受管区块 hash 和 profile 是否由安装器创建。ownership 或 hash 不匹配时 fail closed，并给出一条可复制的 repair 命令，不向普通用户暴露原始 hash 细节。
- Runtime 更新保留一个已验证的上一构件并原子替换；`self rollback` 在锁内验证 current/previous、切换并通过稳定 launcher 自检，失败恢复 current，成功消费恢复点。项目配置迁移仍由 `adp-harness upgrade` 独立计划和应用。
- config、capabilities、record 分别声明 schema version。
- modules.lock 固定来源、版本与 hash。
- 不兼容升级必须阻止启动，不得忽略未知字段。
- `upgrade plan` 显示配置 diff、迁移风险和模块变化。
- `apply` 以可恢复事务更新 canonical 配置和受管投影，并保存恢复点。
- 历史 record 保持可读，不强制重写。
- 内核 invariant 变化只能通过 major version。

## 25. 技术架构

### 25.1 技术栈

- Python 3.12+。
- 核心标准库优先。
- JSON Schema 作为外部契约。
- CLI 与 Runtime 分层。
- adapter、scanner、module 通过稳定接口解耦。

### 25.2 建议仓库结构

```text
src/adaptive_harness/
├── core/
│   ├── envelope.py
│   ├── gateway.py
│   ├── executor.py
│   ├── verifier.py
│   ├── store.py
│   └── policy.py
├── cli/
├── adapters/
│   ├── generic.py
│   ├── codex.py
│   └── claude_code.py
├── scanners/
│   ├── generic.py
│   ├── node.py
│   └── python.py
├── modules/
├── templates/
├── feedback/
└── schemas/
tests/
fixtures/
docs/
```

## 26. MVP 功能与验收

### F-001 初始化与 Doctor

验收：受支持的干净主机能以一条命令安装经过校验的当前用户级自包含构件，无需 Python、额外 Python 包管理器或 `sudo`，并在新 shell 与任意仓库提供 `adp-harness` 命令；安装不修改仓库。Node 和 Python fixture 能生成三个 canonical 配置文件及所选客户端的最小接入投影；用户确认前不写入；重复初始化幂等；多文件提交失败或中断后全量恢复；doctor 检测 schema、客户端、module hash、受管投影漂移、workspace 与 enforced sandbox 前置条件。

### F-002 Task Envelope 与状态存储

验收：自动创建、amend、cancel、恢复；base SHA 不可变；history append-only；record 损坏被检测。

### F-003 Gateway 与 Policy

验收：声明 capability 可执行；未知写入、网络和副作用被拒绝；approval 不能跨 task/SHA/path/count 复用。

### F-004 Executor 与 Evidence

验收：timeout、取消、attempt、stdout/stderr artifact、secret 脱敏、输出限制和副作用事件可验证。

### F-005 Verifier 与 Completion Gate

验收：coverage 缺失、required check 失败、diff 越界、伪 evidence 和 workspace 变化均不能 completed；支持 accepted_with_risk 且不可豁免项仍被拒绝。

### F-006 项目与模型 Profile

验收：扫描结果区分 observed/declared/inferred/unknown；未知模型不能降低内核；Node/Python 一等扫描可用。

### F-007 Adapter

验收：Generic observe、Codex、Claude Code 可初始化；`AGENTS.md`/`CLAUDE.md` 受管区块保留用户内容并可安全更新、移除；prompt-only 接入不得标记 enforced；至少一个真实 enforced E2E；adapter contract suite 全部通过；故障降级符合模式。

### F-008 模块与模板

验收：Installed/Enabled/Activated、四种激活策略、进程隔离、权限约束、Trial/promote/rollback 可用；官方模块不复制源码到用户仓库；模板无执行和状态权限，只能由显式 render 写入用户指定路径且不会无确认覆盖。

### F-009 Feedback 与 Suggest

验收：off/minimal/research 行为正确；minimal 无模型调用；taxonomy、成熟度、冷却期和建议配置 diff 可验证；建议不能影响内核。

### F-010 升级、存储与导出

验收：Runtime 自更新与项目配置升级边界清晰；版本兼容、事务式升级/回滚、分层清理、pin、脱敏导出和零遥测默认可验证。默认用户数据目录按 repository identity 隔离；当前 clone 可显式切换 Git common directory 存储；迁移拒绝活动任务和目标冲突，复制验证后原子切换并默认保留源副本。

## 27. 开发顺序

1. Kernel Foundation。
2. Generic CLI 与 Node/Python scanners。
3. Generic、Codex、Claude Code adapters。
4. 模块、模板与 Trial。
5. Feedback 与 Recommendation。
6. Hardening、打包、文档与发布。

在 invariant 测试完成前不开发自动建议；在真实 enforced adapter 完成前不声称强制治理；没有 module Trial 时 Recommendation 不得进入默认配置。

## 28. MVP 发布 Gate

必须完成：

- 新 Node/Python 仓库初始化；
- 从正式签名构建产物执行无 Python 前置的当前用户级安装、版本固定、自更新和保留数据的卸载；
- macOS/Linux `arm64` 与 `x86_64` 构件、checksum、来源证明和安装后 smoke test；
- `AGENTS.md`/`CLAUDE.md` 接入块的创建、幂等更新、漂移检测和卸载；
- 普通修复任务；
- capability 升级与作用域审批；
- accepted_with_risk；
- module Trial 与 promote/rollback；
- 三种 feedback mode；
- Generic observe、两个 adapter、至少一个 enforced；
- 至少三个真实任务的渐进加载验证；
- Linux/macOS E2E；
- config 升级与回滚。
- 默认与 repository-local 存储、迁移中断/冲突/回滚。

安全 Gate 必须 100% 通过，不能用风险豁免：错误 SHA/worktree、lease 冲突、未声明写入/网络/secret/生产操作、approval 复用、coverage 缺失、diff 越界、artifact 逃逸、record 损坏、日志未脱敏和中断恢复。

## 29. 主要风险与缓解

- 客户端无法强制拦截：明确 observe，不虚假承诺；优先完成一个 enforced adapter。
- 命令副作用不可完全静态识别：声明式 metadata、运行前 review、sandbox 与实际事件对照。
- 模块推荐造成上下文膨胀：三级加载、预算、Trial 和自动卸载。
- 反馈归因错误：固定 taxonomy、最早主因、证据成熟度和用户确认。
- 配置复杂：仅三个 canonical 状态文件；接入投影必须可解释、可重建，并由 explain/diff 展示。
- 安装供应链：版本化构件、checksum、来源证明、平台矩阵 smoke test、原子替换和上一版本回滚。
- 插件供应链：MVP 仅 builtin/local、hash lock、进程隔离和权限声明。
- 用户关闭反馈：内核不依赖反馈；minimal 零模型调用；明确成本。

## 30. 后续路线图

以下方向不承诺固定日期，只有在真实需求和成本证据满足时进入：

- Rust、Go、Java/Kotlin、.NET 扫描器；
- Windows 原生；
- 本机跨项目学习；
- 团队控制平面和集中策略；
- Dashboard；
- 远程签名模块 registry；
- 自动 test selection、semantic diff review；
- 多 Agent、独立 evaluator；
- 模型 routing/fallback；
- 可复现容器、bisect、flaky 检测；
- 企业审计、SSO 和策略分发。

每项路线图必须证明：存在重复用户需求，当前流程有可测失败或成本，功能不削弱本地核心与 invariant，并具备 Trial 和回滚路径。

## 31. 开源与分发

- Apache-2.0。
- GitHub Releases 提供自包含构件、安装脚本、checksum、签名来源证明与变更日志；PyPI 提供通用 wheel 与 sdist 备选渠道。
- Runtime、CLI、内置模块、模板、adapter、schema 和 integration protocol 开源。
- 官方模块发布包含签名、兼容矩阵和变更日志。
- 未来商业能力围绕团队控制面、托管指标和企业支持，不限制本地核心。

## 32. 完成定义

MVP 只有在 F-001 至 F-010 的验收全部满足、发布 Gate 通过、两类 fixture 和真实任务均有可复查证据、无未解决安全 invariant 缺陷、文档能够让新开发者在空仓库完成初始化和首个任务时，才可以标记完成。
