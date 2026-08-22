# StratGauge（策衡）

> 衡量优势，以证据交易。

个人 A 股研究与决策助手：保存选股证据，连接时间点行情，评估历史表现，并记录人工交易。系统不连接券商、不自动下单，用户始终负责交易确认。

## 当前状态

项目目前处于**半手动研究闭环**：

```text
选股导入 → 人工评审 → 收盘行情成熟 → 客观结果
        → 特征/训练数据 → 模型验证 → shadow 证据
```

模型、推荐和交易记录彼此隔离。机器学习就绪状态可在 Web“导入数据”页面查看，也可调用：

```text
GET /v1/ml/readiness
```

模型训练不会由 Web 请求自动触发。实际完成情况见 [`docs/current-state.md`](docs/current-state.md)。

## 快速开始

环境要求：Python 3.12+、[`uv`](https://docs.astral.sh/uv/)、Node.js、Corepack 和 pnpm。

```bash
# 安装后端依赖
cd backend
uv sync

# 安装前端依赖
cd ../frontend
corepack pnpm install --frozen-lockfile

# 返回仓库根目录，启动前后端
cd ..
uv run --project backend python start.py
```

Windows 也可运行根目录的 `start.cmd`。默认地址：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://localhost:5173`

启动器不会自动安装依赖或打开浏览器。

## 日常验证

```bash
# 后端
cd backend
PYTHONPATH=src uv run python -m unittest discover -s tests -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy

# 前端
cd ../frontend
corepack pnpm typecheck
corepack pnpm build
corepack pnpm exec playwright test tests/visual
```

默认测试使用离线替身，不访问真实数据源。

## 让 AI 实际运行接口巡检

如果希望 AI 在本机启动服务、执行巡检并返回实际结果，可以直接发送：

> 请在当前 StratGauge 项目中实际运行 GET 性能巡检：启动本地 FastAPI，等待 `/healthz` 可用后执行 `backend/scripts/benchmark_get_endpoints.py`，返回完整 JSON 结果、退出码和失败接口；完成后停止临时启动的服务。不要只给命令，必须实际执行。

如果后端已经运行，则发送：

> 请直接调用运行中的 `http://127.0.0.1:8000`，执行 GET 性能巡检并报告实际结果，不要模拟数据。

AI 应报告实际执行的命令、退出码、通过/跳过/失败接口，以及各接口的中位和最大耗时。巡检本身不并发、不写入业务数据；退出码 `0` 表示通过，`1` 表示接口/网络/性能失败，`2` 表示样例配置错误。完整参数和样例维护规则见 [`docs/使用文档.md`](docs/使用文档.md) 的“顺序 GET 接口性能巡检”。

## 研究流程

1. 在 Web 或 CLI 导入通达信候选批次；
2. 追加人工评审和观察状态；
3. 显式创建、执行行情成熟任务；
4. 检查客观结果、统计和数据集；
5. 生成点时特征快照和训练切分；
6. 满足 readiness gate 后，人工按 CLI 分阶段执行训练、校准、注册、生命周期、shadow、走步和比较；内部通过 `backend/src/stratgauge/application/ml/` 显式 application service 执行，不自动触发下一阶段；
7. 只有人工确认后才允许进入后续研究流程。

重要边界：

- 原始文件、JSON、Parquet、manifest 和追加事件是事实源；索引可重建；
- 行情、复权因子、特征和模型证据必须满足时间点与血缘校验；
- `no_entry`、`unresolved`、`pending` 不会被当作亏损样本；
- 默认至少需要 100 条成熟 `win/loss/flat` 结果，并同时具备 `success/not_success`；
- benchmark 使用独立的 `000300.SH index_daily` 证据，不使用股票复权因子代替；
- shadow 分数不用于交易决策；
- provider、真实行情和 Scheduler 操作必须显式执行，凭证只从环境变量读取；四用途研究链与旧候选成功率运行时链保持隔离。

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/使用文档.md`](docs/使用文档.md) | 安装、CLI、API、Web 操作、数据格式和排错 |
| [`docs/系统规格说明.md`](docs/系统规格说明.md) | 架构、数据契约、存储、ML 和 API 索引 |
| [`docs/current-state.md`](docs/current-state.md) | 当前已实现事实和遗留事项 |
| [`docs/decisions.md`](docs/decisions.md) | 已确认的设计决策 |
| [`docs/session-log.md`](docs/session-log.md) | 实际验证和会话记录 |
| [`AGENTS.md`](AGENTS.md) | AI 协作与项目约定 |

## 开发约束

- 不连接券商、不自动交易、不伪造行情、概率或模型 ID；
- 防止未来函数、生存者偏差和静默降级；
- 理论结果、推荐结果和实际交易分开保存；
- 新行为必须补充针对性测试；
- 未授权不执行 commit、push 或真实 provider 请求。
