# StratGauge AI 协作指南

本文件定义本项目长期有效的 AI Coding 协作规则。开始任务前先阅读本文件，并按顺序读取：

1. `docs/clean-code.md`：代码、测试、重构、审查和文档修改的 Clean Code 规则；
2. `docs/current-state.md`：当前目标、已知状态和未完成事项；
3. `docs/decisions.md`：已经确认的设计决策；
4. `docs/session-log.md` 中最近的相关记录；
5. `docs/pitfalls.md` 中与当前任务相关的历史问题；
6. 仅当任务需要沉淀可复用的跨项目知识时，更新知识库 `https://github.com/CGOSU/knowledge.git`；更新前先在其本地检出中执行 `git pull`，完成后使用中文提交信息并执行 `git push`；
7. 本仓库 Git 身份使用 `git config user.name CGOSU` 和 `git config user.email dev@cgosu.com`。

## 项目定位

是一个把个人选股和买卖策略自动化、用历史数据与机器学习评估胜率和风险，并由用户手动决策交易的 A 股 AI 策略助手

## 自动角色编排

当用户请求包含“实现、开发、修复、重构、迁移或完整交付”时，自动按以下顺序执行。

### 阶段一：架构分析

首先调用 `architect` Agent。

等待架构师返回 `ARCHITECT_HANDOFF`。

如果 `status` 是 `BLOCKED` 或 `NEEDS_USER_INPUT`，立即停止自动流程，向用户说明阻塞原因和待确认问题。

只有当 `ARCHITECT_HANDOFF.status = READY_FOR_IMPLEMENTATION` 时，才能进入开发测试阶段。

### 阶段二：开发和测试

把架构师的交接结果完整传递给 `test-engineer` Agent。

开发测试工程师必须：

- 按架构范围实施最小代码修改；
- 补充针对性测试；
- 执行真实验证命令；
- 输出 `TEST_HANDOFF`；
- 不覆盖其他协作者已有的未提交改动。

如果 `TEST_HANDOFF.status = FAIL`：

- 将失败命令、真实输出和失败原因返回给 `test-engineer`；
- 最多自动修复和重试两轮；
- 两轮后仍失败时停止流程并向用户报告。

如果 `TEST_HANDOFF.status = BLOCKED`，停止流程并向用户询问。

只有当 `TEST_HANDOFF.status = PASS` 时，才能进入文档收尾阶段。

### 阶段三：文档和发布收尾

把以下内容传递给 `docs-release-engineer` Agent：

- 架构师交接结果；
- 测试交接结果；
- 当前 `git diff`；
- 已执行的验证命令；
- 验证命令的真实结果。

文档收尾工程师负责：

- 更新 README、技术文档、使用文档和变更摘要；
- 核对版本号；
- 执行 `git status`、`git diff --stat` 和 `git diff --check`；
- 核对文档链接、命令、路径和版本信息；
- 输出提交范围和建议提交信息；
- 默认不执行 `git add`、`git commit` 或 `git push`。

### 总体约束

- 三个 Agent 必须按顺序运行，不要并行修改同一个工作区。
- `architect` 只负责分析，不负责实现。
- `test-engineer` 只负责实现和验证，不负责发布文档。
- `docs-release-engineer` 不修改业务代码。
- 任一阶段遇到关键歧义、权限问题或阻塞，都必须停止并询问用户。
- 不得跳过架构交接、测试验证和收尾检查。
- 只记录真实执行过的命令和结果。
- 用户只要求分析时，只调用 `architect`。
- 用户只要求修复测试问题时，可直接调用 `test-engineer`。
- 用户只要求更新文档时，可直接调用 `docs-release-engineer`。
- 除非用户明确要求，不执行提交和推送。

## 运行环境与命令约定

- 初始化时检测到的宿主系统：Windows (`win32`)，CPU 架构：`x64`。
- 这是运行 Pi 的宿主环境快照，不一定是项目部署目标；如果实际执行发生在 WSL、容器、远程主机或其他环境中，应重新检测并以当前环境为准。
- Pi 的内置 `bash` 工具在 Windows 上通常通过 Bash 执行；扩展使用 `pi.exec` 时是直接启动进程，不会经过 Bash。
- 查找命令优先使用 `where.exe` 或当前 shell 支持的 `command -v`；不要把 Linux-only 的 `which` 作为唯一检查。
- npm 全局 CLI 可能通过 Windows `.cmd` shim 暴露；直接启动时要选择当前平台可用的执行入口。
- 如果工具提示 CLI 不存在，先用 `where.exe <command>`（以及对应的 `.cmd` shim）核实，再决定是否安装。

## 常用命令

- 测试：`待项目维护者补充。`

如果命令尚未补充，先检查项目现有脚本和工具链，不要猜测命令。

## 工作约定

- 修改前检查工作区和相关实现，不覆盖其他协作者的改动。
- 优先进行最小、局部、可验证的修改，不为不确定需求增加兼容层。
- 遵循项目已有的代码风格、目录结构和工具链。
- 不在代码、文档、日志或提交中记录令牌、密码、私钥等敏感信息。

## 验证要求

- 新增或修复行为时补充针对性测试。
- 至少运行与改动直接相关的测试、类型检查或构建命令。
- 只记录实际执行的验证及真实结果，不把未执行检查描述为通过。

## 会话收尾

完成任务后：

1. 更新 `docs/current-state.md`，只保留当前事实和未完成事项；
2. 将影响后续实现的重要选择追加到 `docs/decisions.md`；
3. 在 `docs/session-log.md` 记录完成内容、验证命令和遗留问题；
4. 将新发现的隐蔽且可复发问题沉淀到 `docs/pitfalls.md`。

仅在产生新事实时更新对应文件，不为留痕进行无意义修改。一个事实只在一个文件中维护；其他文件需要引用时，使用摘要和相对路径指向唯一来源。
