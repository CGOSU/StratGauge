# StratGauge 执行计划

## 当前里程碑：阶段 0——可信基础

状态：检查点 1—6 已完成；代码验收通过，阶段 0 退出仍等待 AKShare 网络集成成功。

### 目标

建立可维护的仓库，并用冻结的标准化 A 股日线数据证明：一个简单的版本化策略能够在无未来函数的条件下产生信号，并按下一交易日规则完成保守回测。

### 冻结约束

- 仅支持 A 股日线和收盘后信号；
- AKShare 为主数据源，BaoStock 为备用及交叉校验源；
- 用户手动决策和交易，不提供任何券商或自动下单能力；
- 所有结果记录数据快照、特征集、策略和可选模型版本；
- 数据冲突必须报告，质量门失败必须阻止发布；
- 信号生成与成交模拟分离；收盘后信号最早下一交易日成交；
- 示例策略仅用于验证基础设施，不代表用户最终策略；
- 阶段 0 不实现机器学习、LLM、新闻、分钟数据和无关部署设施。

## 文件级实施计划

### 检查点 1：Python 工程与领域契约（已完成）

文件：

- `backend/pyproject.toml`、`backend/.python-version`、`backend/uv.lock`；
- `backend/src/stratgauge/__init__.py`；
- `backend/src/stratgauge/domain/models.py`；
- `backend/src/stratgauge/domain/symbols.py`；
- `backend/tests/domain/test_models.py`；
- `backend/tests/domain/test_symbols.py`；
- 根目录 `.gitignore`。

公共契约：

- `Exchange`、`Security`、`DailyBar`、`StrategyVersion`、`Signal`、`Recommendation`；
- `canonical_symbol(local_code, exchange)`：只接受明确的交易所元数据，不根据代码前缀猜交易所；
- 所有时间戳必须带时区，价格和金额使用十进制定点数。

验收：模型拒绝非法代码、无时区时间和非法价格；沪、深、北交所代码规范化正确；测试、格式检查和类型检查均可运行。

验证：

```bash
cd backend
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy src tests
```

### 检查点 2：数据适配器与标准化（已完成）

文件：

- `backend/src/stratgauge/data_sources/base.py`；
- `backend/src/stratgauge/data_sources/akshare.py`；
- `backend/src/stratgauge/data_sources/baostock.py`；
- `backend/tests/data_sources/test_contract.py`；
- `backend/tests/data_sources/test_akshare.py`；
- `backend/tests/data_sources/test_baostock.py`。

公共契约：

- `DailyBarRequest`：已验证的 `Security`、开始日和结束日；
- `AdapterResult`：提供商、请求参数、抓取时间、原始记录和标准化候选记录；
- `MarketDataAdapter.fetch_daily_bars(request)`：两个适配器遵守同一协议，第三方库只在适配器内部出现。

验收：使用伪造的提供商响应运行契约测试；列名、单位、日期和代码转换可验证；提供商失败以带上下文的异常暴露，不静默回退。真实网络集成测试保持显式选择，不进入默认测试。

### 检查点 3：不可变快照、幂等入库与质量门（已完成）

文件：

- `backend/src/stratgauge/data_pipeline/store.py`；
- `backend/src/stratgauge/data_pipeline/quality.py`；
- `backend/src/stratgauge/data_pipeline/ingest.py`；
- `backend/tests/data_pipeline/test_store.py`；
- `backend/tests/data_pipeline/test_quality.py`；
- `backend/tests/data_pipeline/test_ingest.py`。

公共契约：

- `SnapshotManifest`：内容寻址的快照 ID、来源、抓取时间、行数和校验和；
- `QualityReport`：错误、警告和 `approved` 发布门结果；
- `ingest(adapter, request, store)`：相同内容重复执行返回同一快照，不覆盖历史证据。

数据布局：原始响应保存为不可变 JSON，标准化日线按交易日写入 Parquet，DuckDB 保存清单、质量结果和状态索引，并可直接查询 Parquet；`data/` 不进入 Git。

验收：重复入库幂等；OHLC、负成交量、重复键、缺失行和跨源差异可检测；失败报告阻止批准快照。

### 检查点 4：最小安全策略与下一交易日回测（已完成）

文件：

- `backend/src/stratgauge/strategies/ast.py`；
- `backend/src/stratgauge/strategies/example.py`；
- `backend/src/stratgauge/backtest/engine.py`；
- `backend/src/stratgauge/backtest/models.py`；
- `backend/tests/strategies/test_ast.py`；
- `backend/tests/backtest/test_engine.py`；
- `backend/tests/fixtures/phase0_bars.json`（已用于可复现黄金测试）。

公共契约：

- 仅允许数值、特征引用、比较和布尔组合的类型化 AST，不使用 `eval`/`exec`；
- `generate_signal(as_of, features, strategy)` 只产生订单意图；
- `run_backtest(bars, signals, policy)` 独立模拟成交。

验收：黄金测试覆盖同日不可成交、价格区间未触及、停牌、T+1 和同日止损/目标歧义的保守结果；结果携带快照、特征集和策略版本。

### 检查点 5：股票分析 API（已完成）

文件：

- `backend/src/stratgauge/api/app.py`（含 `/healthz`）；
- `backend/src/stratgauge/api/analysis.py`；
- `backend/tests/api/test_analysis.py`。

公共契约：

- `GET /v1/stocks/{symbol}/analysis?as_of=YYYY-MM-DD`；
- 响应包含数据新鲜度、资格判断、失败条件、动作状态和全部溯源 ID；
- 数据不存在、快照未批准和代码非法时返回统一结构化错误。

验收：API 测试使用冻结夹具，不依赖外网；不可用概率保持为空而不是零；未批准快照不能产生权威分析。

### 检查点 6：最小前端外壳与交付说明（已完成）

文件：

- `frontend/package.json`、`frontend/pnpm-lock.yaml`；
- `frontend/index.html`、`frontend/src/main.tsx`、`frontend/src/App.tsx`；
- `frontend/tsconfig*.json`、`frontend/vite.config.ts`；
- `README.md`、`docs/current-state.md`、`docs/session-log.md`。

验收：前端只提供启动页和 API 健康状态，不提前实现完整屏幕；后端和前端的安装、测试、检查及启动命令可从全新检出执行；真实数据验证脚本保持显式调用。

验证：

```bash
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

## 阶段 0 退出标准

- 全新检出可按文档完成安装；
- 两个适配器可幂等抓取并保存一个有限历史区间；
- 数据差异被报告而非静默覆盖；
- 示例策略从冻结快照产生可复现信号；
- 收盘后信号不可能在当日成交；
- 每个结果都标识来源快照、特征集和策略版本；
- 全部相关自动检查通过。

## 当前环境与风险

- 当前目录已初始化为 Git 仓库，已配置 `CGOSU <dev@cgosu.com>`；当前仅初始化和检查，尚未提交。
- 宿主机已安装 `uv` 和 Python 3.14，但尚无 Python 3.12；由 `uv` 管理项目所需的 Python 3.12。
- `pnpm` 尚未激活，前端检查点使用 Node 自带 Corepack，不全局安装包管理器。
- 当前 Pi 会话设置了 `PI_OFFLINE=1`；默认测试不得依赖真实数据源网络，真实抓取在网络可用时作为显式集成验证；可选依赖组为 `live-data`，脚本位于 `backend/scripts/verify_live_adapters.py`。
- 用户真实策略参数尚未提供；阶段 0 只保留明确标注的示例策略。

## 后续里程碑

- 阶段 1A：通达信半手动研究闭环；导入批次、候选队列、人工标签、固定持有期结果、统计和按时间切分的数据集；不实现通达信公式解析器。当前已完成导入、候选 API/前端、冻结标签、政策可追溯结果、统计 API、显式成熟 CLI 和 JSONL 出口；剩余后台调度、真实样例兼容和用户策略参数冻结。
- 阶段 1：完整历史存储、策略工作台和可靠回测报告；
- 阶段 2：每日推荐、股票搜索和手工持仓台账；
- 阶段 3：LightGBM 评分、走步验证和模型注册；
- 阶段 4：AI 解释、公告摘要和桌面封装。

不因某一检查点能够编译而提前进入后续阶段；必须先满足当前检查点和阶段退出标准。
