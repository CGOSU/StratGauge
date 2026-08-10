# StratGauge 执行计划

## 当前里程碑：阶段 0——可信基础

状态：检查点 1—6 已完成；代码验收通过，阶段 0 退出仍等待 Tushare Pro 主源令牌验证和与 BaoStock 的固定区间交叉校验。

### 目标

建立可维护的仓库，并用冻结的标准化 A 股日线数据证明：一个简单的版本化策略能够在无未来函数的条件下产生信号，并按下一交易日规则完成保守回测。

### 冻结约束

- 仅支持 A 股日线和收盘后信号；
- Tushare Pro 为新增自动流程主数据源，BaoStock 为备用及交叉校验源，AKShare 保留为兼容适配器；
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

- 当前目录已初始化为 Git 仓库，已配置 `CGOSU <dev@cgosu.com>`；此前前端样式提交为 `9e2246b`，本轮 Tushare 客户端与验证收口改动尚未提交。
- 宿主机已安装 `uv` 和 Python 3.14，但尚无 Python 3.12；由 `uv` 管理项目所需的 Python 3.12。
- `pnpm` 尚未激活，前端检查点使用 Node 自带 Corepack，不全局安装包管理器。
- 当前 Pi 会话设置了 `PI_OFFLINE=1`；默认测试不得依赖真实数据源网络，真实抓取在网络可用时作为显式集成验证；Tushare 日线和参考数据使用官方 HTTPS Data API，脚本位于 `backend/scripts/verify_live_adapters.py`、`backend/scripts/sync_tushare_universe.py` 和 `backend/scripts/sync_tushare_calendar.py`。
- 当前会话的 `TUSHARE_TOKEN` 已可由后端 Python 进程读取；用户真实策略参数尚未提供，阶段 0 只保留明确标注的示例策略。

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

- [x] **P1-08 Tushare 主源验证**：通过官方 HTTPS Data API 完成 `000001.SZ` 2024-01-02—2024-01-05 有限区间验证；记录请求区间、原始响应、质量门和失败原因。
- [x] **P1-09 双源交叉校验**：同一证券、区间和不复权日线口径下，Tushare/BaoStock 均 4 行且交叉校验通过；报告差异，不静默选择结果。
- [ ] **REF-01 参考接口验证**：沪深主板 `stock_basic` 已验证并生成 3193 条记录（manifest：`tushare-reference-278819ba7c8d5c92fe37`，股票池：`universe-d8b8d8f30a3f50c43fd9`）；`trade_cal` 因当前 Token 限制为 5 次/月仍阻塞，交易日文件尚未生成。
- [ ] **P1-10 验证收口**：Tushare 主源和 BaoStock 固定区间条件已满足；待 `trade_cal` 参考日历验证完成并复核全量检查后，才把阶段 0 标记为退出。

### P1：趋势交易条件胜率与买入价分析

- [x] **A1 买入规则契约**：新增不可变 `entry-limit-chase-v1`，固定收盘后信号、后续 3 个交易日有效期、日线触达近似、开盘价成交和 1% 追价上限；执行窗口由显式交易日历生成。
- [x] **A2 胜负和成本规则**：默认 10 个交易日后收盘退出；扣费后净收益大于 0 为胜、等于 0 为平、小于 0 为负；未入场和数据不足分别保留为 `no_entry`/`unresolved`；默认无止盈止损，沪深主板按 100 股整手、佣金 0.03%（最低 5 元）、卖出印花税 0.05%、滑点 0 计算；结果记录 `outcome_policy_id`，数据集升级为 v2。
- [x] **B1 全市场单日增量行情**：Tushare `daily(trade_date=...)` 按显式沪深主板股票池获取单个交易日，生成内容寻址快照；已批准的“交易日 + 股票池 + 数据源”自动幂等跳过，覆盖率低于 90% 阻止发布。
- [x] **B2 多交易日增量编排**：CLI 支持显式交易日历范围，逐日复用 B1；拒绝重复/乱序日期和未来股票池，供应商异常返回非零，已批准日期继续幂等跳过。
- [x] **C1 交易日历自动缓存**：交易日文件带覆盖范围 manifest，已覆盖请求零调用，范围缺口只查询缺失区间并原子更新；篡改或半成品缓存拒绝消费。
- [x] **C2 复权因子与研究特征输入**：Tushare `adj_factor` 按收盘行情逐日同步，因子快照绑定同日已批准日线；低覆盖率阻断发布，特征引擎可显式使用前复权价格且保留因子快照血缘。
- [x] **C2.1 历史批量因子补齐**：新增显式交易日历范围 CLI，只为已有已批准全市场日线快照补齐因子；缺少日线快照时不请求供应商并报告阻断，重复执行复用已批准因子快照。
- [x] **C3 已批准行情自动成熟队列**：使用显式交易日历扫描已批准全市场快照，满足固定持有期后创建不可变快照重放任务；重复扫描幂等，执行不再请求供应商。
- [x] **A3 条件胜率与买入价分析**：新增同公式、同追踪政策的时间点历史分析 API 和研究页候选面板；完成样本少于 100 个时隐藏胜率，买入价只展示历史实际入场价描述统计，不生成推荐价。

### P2：成熟任务的可靠调度（不自动交易）

- [x] **P2-01 调度边界设计**：定义任务输入、目标评估日、来源、政策 ID、重试次数、超时、状态和失败原因；任务必须可恢复、可重跑、可审计。
- [x] **P2-02 幂等执行**：使用 `batch_id + evaluated_at + tracking_policy_id + provider` 作为任务指纹；同一任务不重复写入不同结果。
- [x] **P2-03 后台执行方式**：优先采用操作系统任务计划或显式 CLI 调度，不先引入 Redis/Celery 等新基础设施；服务内只负责查询任务状态和手动触发。
- [x] **P2-04 失败重试**：仅对可重试的网络/限流错误重试；质量门、参数错误和数据冲突不自动重试；所有重试保留事件。
- [x] **P2-05 Web 任务状态**：显示排队、执行中、完成、部分完成和失败；成熟结果继续只写客观证据，不连接券商、不自动下单。

### P2：研究统计和数据集可用性

- [x] **P2-06 管理页统计筛选**：按公式版本、来源、选股日期、人工决策和结果政策筛选，统计显示样本数、待定数、胜率和平均净收益。
- [x] **P2-07 数据集导出确认**：为 JSONL 导出增加列版本和 schema 标识；继续保留待定、未入场和未解决样本，禁止覆盖长期导出文件。
- [x] **P2-08 训练集时间切分**：按选股日期生成 train/validation/test 清单，禁止随机打乱和未来信息泄漏；输出切分规则和数据快照 ID。

### P3：机器学习（策略参数冻结且样本足够后）

#### P3 进入条件

以下条件全部满足后，才允许把模型用于研究展示或候选排序：

- [x] **P3-00 机器学习准入检查**：新增 `audit_selection_ml_readiness.py`，显式声明策略参数是否冻结，检查样本量、目标类别、策略政策、重复行和结果证据；默认至少要求 100 条完成结果，可通过参数调整。
  - 验收：生成带源文件 SHA-256、样本数量、日期范围、阻断原因和警告的准入报告；不满足条件返回退出码 2，不启动训练。

#### P3 数据和特征

- [x] **P3-01 训练样本审计**：复用公共 JSONL 解析器审计 `selection-dataset-v2`，统计完成、待定、未入场/未解决、人工决策缺失、政策范围和重复候选；训练专用标签清单仍由 P3-05 生成。
  - 验收：每个完成结果能追溯到候选、选股日期、公式版本、追踪政策和行情快照；未知 schema、重复候选和缺少结果证据会阻断准入。
- [x] **P3-02 历史行情补齐**：通过 `backfill_selection_history.py` 为候选和显式提供的基准/行业标的补齐选股日前的日线历史；使用显式交易日历选择最长回看窗口，只消费质量门批准快照。
  - 验收：输出缺失证券、缺失交易日、历史窗口不足和质量失败报告；拒绝未批准快照，不以填充数据伪造特征。
- [x] **P3-03 特征契约**：`features/contract.py` 和 `features/engine.py` 实现第一版确定性日线特征（收益、动量、波动率、成交量/流动性、均线距离和基准相对强弱）；每项声明回看窗口、`available_at`、空值策略和单位，特征快照使用内容 ID。
  - 验收：特征集和快照具有不可变版本 ID；相同输入和版本重复计算结果一致。
- [x] **P3-04 特征防泄漏测试**：特征引擎拒绝选股日及之后的行情、`available_at` 晚于 `as_of` 的修订/延迟数据、缺失窗口和未对齐基准；训练切分按选股日归桶。
  - 验收：未来行、边界日期、缺失窗口、时区和同日分区均有回归测试；不满足时阻止生成特征行。

#### P3 训练和评估

- [x] **P3-05 可复现训练入口**：新增离线 `train_selection_baseline.py`，输入版本化特征 JSONL、显式特征集版本、追踪政策、标签字段、随机种子、时间边界和输出目录；不在 Web 请求中训练。
  - 验收：同一输入和环境生成相同训练清单及等价指标；未知 schema、空训练/验证样本、单一标签类别、重复行和多特征版本返回明确错误。
- [x] **P3-06 简单基线模型**：实现无额外依赖的常数概率基线；输出概率、并列排名、覆盖率、Log Loss、Brier、模型版本和 manifest，不把分数称为收益保证。
  - 验收：保存训练配置、特征版本、数据源 SHA-256、时间切分和指标；测试覆盖类别不平衡、单类别训练和小样本。
- [x] **P3-07 LightGBM 候选模型**：在基线流程稳定后，以 `ml` 可选依赖加入固定配置 LightGBM；训练仅使用版本化特征 JSONL、单一特征集/追踪政策和显式时间切分，输出未校准概率、指标、模型文本和不可覆盖 manifest，不接入 Web 或自动晋升。
  - 验收：候选模型与基线使用同一时间切分和标签约束；缺少可选依赖、单类别训练或输入不一致时明确失败，不影响原始策略流程。
- [ ] **P3-08 走步验证**：已增加常数基线的显式折边界、标签可见性清除期、最终测试集隔离和 `validate_walk_forward.py` CLI；当前特征行不包含成交/净收益序列，因此成交率、净收益、回撤、换手和市场状态稳定性仍未实现。
  - 验收：报告每折日期、样本数、缺失率、成交率、Log Loss/Brier、排序指标、净收益、回撤、换手和市场状态稳定性。
- [ ] **P3-09 概率校准和不确定性**：在未参与训练的数据上校准模型输出，并显示样本量、校准日期、模型版本和适用范围。
  - 验收：未经校准的树模型分数不得在 Web 中显示为胜率；校准失败时概率保持不可用。

#### P3 注册、影子运行和展示

- [x] **P3-10 本地模型注册**：`ModelRegistryStore` 保存模型工件、评估报告、训练 manifest（含训练参数和运行环境）的内容副本和 SHA-256；模型版本不可覆盖。
  - 验收：给定模型版本会校验全部工件哈希；支持 `challenger`、`shadow`、`active`、`retired` 生命周期及带操作者/原因的追加审计事件。
- [x] **P3-11 模型比较和人工晋升**：新增版本化评估证据、显式 `ComparisonPolicy`、内容寻址比较报告和指标/校准门禁；缺失的信号量、净期望、回撤、换手、特征稳定性或最差折指标会阻断，而不会填充默认值。`promote` 必须引用哈希匹配且通过的人工比较报告；已有 active 模型必须先人工退役，回滚仍保留独立入口。
  - 验收：只有人工确认后才能晋升；保留回滚入口，不自动替换当前模型。
- [ ] **P3-12 影子推理**：已增加独立 `shadow.py`、`score_selection_shadow.py` 和不可变评分/报告存储；当前只支持 shadow 生命周期的常数基线，尚未接入新导入批次和 Web。
  - 验收：影子评分失败不阻止原始批次和成熟任务；结果可与人工决策和实际客观结果分开比较。
- [ ] **P3-13 API/前端展示**：增加模型版本、评分、校准估计、样本量、适用日期和失败原因展示；不提供自动交易按钮。
  - 验收：未批准模型、过期特征或质量门失败时不展示权威评分；页面明确显示“历史估计，不代表收益保证”。

### P4：可靠研究和推荐工作台

- [ ] **P4-01 历史补数与股票池**：已实现 Tushare `stock_basic` 的不可变参考快照、`sync_tushare_universe.py` 时间点股票池和 `sync_tushare_calendar.py` 交易日历；完整历史成分、退市主数据生命周期和更丰富证券元数据仍未实现。
- [x] **P4-02 策略生命周期**：`StrategyVersionStore` 和 `register_strategy.py`/`transition_strategy.py` 实现草稿、验证、影子、激活和退役；版本内容不可覆盖，生命周期变更追加审计事件。
- [x] **P4-03 回测报告**：`backtest/report.py` 和 `report_backtest.py` 生成内容寻址的理论回测报告，按策略、成本政策、显式市场状态和入场日期分组；报告明确只包含理论回测证据，不混入推荐或实际成交。
- [ ] **P4-04 每日推荐工作台**：在公式候选基础上展示质量状态、历史统计和已批准模型评分，由用户手动确认，不自动下单。
- [ ] **P4-05 股票搜索和持仓台账**：已实现按时间点股票池搜索的 `/v1/universe/securities`，以及手工订单、成交、持仓推导、复盘记录及 `/v1/manual-orders`、`/v1/manual-fills`、`/v1/positions`、`/v1/portfolio-reviews` 基础接口；完整股票主数据、用户覆写和更完整复盘工作台仍未实现，实际交易数据已与模型结果分离。
- [ ] **P4-06 模型运营**：提供冠军/挑战者比较、影子运行、手工晋升/回滚、校准和漂移报告；所有操作保留审计记录。

### 明确不做

- 不解析或执行通达信公式；
- 不接入券商和自动交易；
- 不引入分钟/Tick 数据；
- 不在参数、数据质量和调度幂等冻结前引入大规模部署设施；
- 不把浏览器 F7 光标浏览、普通文字选择或 UI 指针问题误判为页面可编辑功能。

## 后续里程碑

- 阶段 1A：通达信半手动研究闭环；导入批次、候选队列、人工标签、固定持有期结果、统计和按时间切分的数据集；不实现通达信公式解析器。当前已完成导入、候选 API/前端、冻结标签、政策可追溯结果、显式成熟 CLI、成熟任务账本与调度 CLI、JSONL 出口、不可覆盖的数据集时间切分、Web 数据管理、手工候选录入和条件胜率/历史入场价分析；剩余真实样例兼容、真实数据源退出以及用户策略参数冻结。
- 阶段 1：完整历史存储、策略工作台和可靠回测报告；
- 阶段 2：每日推荐、股票搜索和手工持仓台账；
- 阶段 3：LightGBM 评分、走步验证和模型注册；
- 阶段 4：AI 解释、公告摘要和桌面封装。

不因某一检查点能够编译而提前进入后续阶段；必须先满足当前检查点和阶段退出标准。
