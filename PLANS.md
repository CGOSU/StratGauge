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

## 下一阶段任务清单

### P0：收口当前改动，保证可交付

- [x] **P0-01 清理运行残留**：确认并停止旧的 Vite/后端服务；清理本轮 E2E 产生的公式、批次、标签、manifest、raw 和 lifecycle 文件；保留明确的正式样例与 live-verification 快照。当前 5173 进程属于 `D:\Code\Rust\clash-forward-rev`，未误杀其他项目服务。
  - 验收：`backend/data/selection` 只保留经确认的样例/用户数据；端口无本项目残留服务；默认测试不读取真实数据。
- [x] **P0-02 提交前审查**：检查当前大 diff，确认前端依赖、shadcn/ui 文件、路径别名、`.gitignore` 和文档没有误提交；补齐 `README.md` 的管理页和 JSONL 下载说明。
  - 验收：`git diff --check` 通过；全量后端和前端检查通过；用户明确授权后再提交。
- [x] **P0-03 公式版本管理收口**：保留“编辑为新版本”语义，补一条 API/前端回归测试，验证旧公式 ID 和旧批次引用不变。
  - 验收：编辑后出现新公式，旧公式仍可查看；不能原地覆盖公式证据。

### P1：补齐“手动添加的数据”入口

- [x] **P1-01 手动创建候选批次 API**：新增明确的 `manual` 来源请求，字段至少包括选股日期、导出/记录时间、公式 ID、股票代码、交易所、名称和备注；普通六位代码必须显式选择交易所。
  - 约束：仍只支持 A 股日线和收盘后研究；不得通过代码前缀猜市场；人工输入作为原始证据追加保存。
- [x] **P1-02 手动候选存储和幂等**：复用 `SelectionBatch`/`SelectionCandidate` 证据链，增加来源类型和请求指纹；重复提交相同内容返回同一批次，不覆盖已有批次。
  - 验收：同一手动批次重复提交不产生重复候选；错误代码、交易所和时间字段返回结构化错误。
- [x] **P1-03 手动录入 Web 表单**：数据管理或研究工作台增加“手动添加候选”入口，支持逐条添加，显示候选预览，提交前校验并允许取消。
  - 验收：键盘可操作；校验错误明确显示；提交后自动进入研究队列和数据管理详情。
- [x] **P1-04 手动来源管理**：管理页区分 `manual` 与 `tongdaxin`，支持按来源筛选和查看原始输入摘要；仍支持归档/恢复，不提供硬删除。

### P1：冻结真实研究策略参数

- [ ] **P1-05 用户参数确认**：冻结持有交易日、入场规则、止盈/止损是否启用、胜负/平局阈值、费用方案、单笔名义金额和未成交处理。
  - 验收：形成 `docs/decisions.md` 决策条目和一组固定测试夹具；未冻结前不把示例结果描述为真实策略结论。
- [ ] **P1-06 结果政策升级**：把冻结参数映射为不可变 `tracking_policy_id`，成熟任务显式选择或引用政策；旧结果保持原政策，不原地重算。
- [ ] **P1-07 策略回归样例**：补充涨跌停、停牌、未成交、同日止盈/止损、T+1 和费用边界测试；统计按政策和候选取最新结果。

### P1：完成真实数据源退出条件

- [ ] **P1-08 AKShare 主源验证**：在网络允许时显式运行有限区间验证；记录请求区间、原始响应、质量门和失败原因。
- [ ] **P1-09 双源交叉校验**：使用同一证券、区间和不复权日线口径比较 AKShare/BaoStock；报告差异，不静默选择结果。
- [ ] **P1-10 验证收口**：只有 AKShare 主源通过、BaoStock 备用及固定区间交叉校验通过后，才把阶段 0 标记为退出。

### P2：成熟任务的可靠调度（不自动交易）

- [x] **P2-01 调度边界设计**：定义任务输入、目标评估日、来源、政策 ID、重试次数、超时、状态和失败原因；任务必须可恢复、可重跑、可审计。
- [x] **P2-02 幂等执行**：使用 `batch_id + evaluated_at + tracking_policy_id + provider` 作为任务指纹；同一任务不重复写入不同结果。
- [x] **P2-03 后台执行方式**：优先采用操作系统任务计划或显式 CLI 调度，不先引入 Redis/Celery 等新基础设施；服务内只负责查询任务状态和手动触发。
- [x] **P2-04 失败重试**：仅对可重试的网络/限流错误重试；质量门、参数错误和数据冲突不自动重试；所有重试保留事件。
- [x] **P2-05 Web 任务状态**：显示排队、执行中、完成、部分完成和失败；成熟结果继续只写客观证据，不连接券商、不自动下单。

### P2：研究统计和数据集可用性

- [ ] **P2-06 管理页统计筛选**：按公式版本、来源、选股日期、人工决策和结果政策筛选，统计显示样本数、待定数、胜率和平均净收益。
- [x] **P2-07 数据集导出确认**：为 JSONL 导出增加列版本和 schema 标识；继续保留待定、未入场和未解决样本，禁止覆盖长期导出文件。
- [x] **P2-08 训练集时间切分**：按选股日期生成 train/validation/test 清单，禁止随机打乱和未来信息泄漏；输出切分规则和数据快照 ID。

### P3：机器学习（策略参数冻结且样本足够后）

- [ ] **P3-01 特征定义**：只使用信号时点可获得的日线特征，记录特征版本、可用时间和来源快照。
- [ ] **P3-02 基线模型**：先实现可解释的基线和 LightGBM 候选，不直接输出权威收益承诺；缺失数据和未解决结果不强行标注输赢。
- [ ] **P3-03 走步验证**：按时间滚动训练/验证，输出样本覆盖、校准、收益和风险指标；与无模型策略比较。
- [ ] **P3-04 模型注册与人工晋升**：模型、特征、数据集和评估报告绑定版本；只有人工确认后才能进入影子评估，不自动替换策略。

### 明确不做

- 不解析或执行通达信公式；
- 不接入券商和自动交易；
- 不引入分钟/Tick 数据；
- 不在参数、数据质量和调度幂等冻结前引入大规模部署设施；
- 不把浏览器 F7 光标浏览、普通文字选择或 UI 指针问题误判为页面可编辑功能。

## 后续里程碑

- 阶段 1A：通达信半手动研究闭环；导入批次、候选队列、人工标签、固定持有期结果、统计和按时间切分的数据集；不实现通达信公式解析器。当前已完成导入、候选 API/前端、冻结标签、政策可追溯结果、显式成熟 CLI、成熟任务账本与调度 CLI、JSONL 出口、不可覆盖的数据集时间切分、Web 数据管理和手工候选录入；剩余真实样例兼容、真实数据源退出、用户策略参数冻结以及管理页高级统计筛选。
- 阶段 1：完整历史存储、策略工作台和可靠回测报告；
- 阶段 2：每日推荐、股票搜索和手工持仓台账；
- 阶段 3：LightGBM 评分、走步验证和模型注册；
- 阶段 4：AI 解释、公告摘要和桌面封装。

不因某一检查点能够编译而提前进入后续阶段；必须先满足当前检查点和阶段退出标准。
