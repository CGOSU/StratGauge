# StratGauge（策衡）

> 衡量优势，以证据交易。

StratGauge 是一款个人 A 股策略助手：把现有的手工选股、买入和卖出流程转化为可复现系统，用于扫描股票、评估候选、估计历史和模型表现，并记录手动执行的交易。

**一句话简介：StratGauge 是一个把个人选股和买卖策略自动化、用历史数据与机器学习评估胜率和风险，并由用户手动决策交易的 A 股 AI 策略助手。**

系统不会自动执行交易。

## 当前状态

本仓库目前包含使用 Codex 开始实现所需的产品与工程规格。第一阶段应先建立可信的数据和回测基础，而不是 AI 聊天界面。

## 开始阅读

1. 阅读 `AGENTS.md`；
2. 阅读 `PLANS.md`；
3. 阅读 `docs/系统规格说明.md`；
4. 使用 `CODEX_START_PROMPT.md` 开始第一次 Codex 规划会话。

## 计划技术栈

- Python 3.12、`uv`、Polars、DuckDB 和 Parquet；
- FastAPI 和 Pydantic；
- LightGBM、Optuna 和 MLflow；
- React、Vite、TypeScript 和 ECharts；
- Tushare Pro 主数据适配器，BaoStock 备用适配器，AKShare 兼容适配器；
- 量化核心稳定后再考虑 Tauri/Axum 封装。

## 核心原则

- 人工确认和手动执行；
- 时间点数据正确性；
- 推荐结果可复现；
- 保守的 A 股回测；
- 策略和模型版本化；
- 走步验证和受控的模型晋升；
- 量化计算与 LLM 解释明确分离。

## 本地开发

后端要求 Python 3.12–3.14 和 `uv`：

```bash
cd backend
uv sync
PYTHONPATH=src uv run python -m unittest discover -s tests -v
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
PYTHONPATH=src uv run uvicorn stratgauge.api.app:app --reload
```

显式进行真实数据源验证时（需要网络，不属于默认测试）：

```bash
cd backend
uv sync --extra live-data
# Tushare Pro 是新增流程默认主源，需要先设置 TUSHARE_TOKEN
PYTHONPATH=src uv run python scripts/verify_live_adapters.py
# 兼容验证：临时使用歪枣网（需要本地设置 WAIZAO_TOKEN）
PYTHONPATH=src uv run python scripts/verify_live_adapters.py --primary waizao
```

通达信半手动选股导入（默认保存到被 `.gitignore` 忽略的 `data/selection`）：

```bash
cd backend
PYTHONPATH=src uv run python scripts/import_tdx_selection.py \
  --file path/to/selection.txt \
  --selection-date 2026-01-05 \
  --exported-at 2026-01-05T15:05:00+08:00 \
  --formula-name "趋势选股"
PYTHONPATH=src uv run python scripts/label_tdx_selection.py \
  --root data/selection \
  --batch-id <batch_id> \
  --candidate-id <candidate_id> \
  --decision watch \
  --labeled-at 2026-01-05T15:10:00+08:00
# 显式用已有日线源成熟一个批次；不会自动交易
PYTHONPATH=src uv run python scripts/mature_tdx_selection.py \
  --batch-id <batch_id> \
  --provider tushare \
  --evaluated-at 2026-01-10T17:00:00+08:00 \
  --horizon-sessions 5 \
  --position-notional 10000
# 执行 Web/API 已创建的排队成熟任务；可由 Windows Task Scheduler 调用，不注册后台定时器
PYTHONPATH=src uv run python scripts/run_selection_maturity_tasks.py \
  --root data/selection \
  --market-root data/selection-market \
  --limit 1
# 导出可追溯 JSONL 数据集；目标文件已存在时拒绝覆盖
PYTHONPATH=src uv run python scripts/export_tdx_dataset.py \
  --batch-id <batch_id> \
  --output data/selection-datasets/batch.jsonl
# 按选股日期生成不可覆盖的 train/validation/test 清单和切分 manifest
PYTHONPATH=src uv run python scripts/split_tdx_dataset.py \
  --input data/selection-datasets/batch.jsonl \
  --output-dir data/selection-datasets/batch-split \
  --train-end 2026-06-30 \
  --validation-end 2026-09-30
# 机器学习训练前先做准入审计；未通过时退出码为 2，不启动训练
PYTHONPATH=src uv run python scripts/audit_selection_ml_readiness.py \
  --input data/selection-datasets/batch.jsonl \
  --output data/selection-datasets/batch-readiness.json \
  --strategy-parameters-frozen \
  --tracking-policy-id <tracking_policy_id> \
  --min-completed-samples 100
# 补齐选股日前的历史；默认从 Tushare trade_cal 获取显式交易日
PYTHONPATH=src uv run python scripts/backfill_selection_history.py \
  --root data/selection \
  --market-root data/selection-market \
  --batch-id <batch_id> \
  --provider tushare \
  --lookback-sessions 60 \
  --benchmark 000300.SH \
  --industry 399006.SZ \
  --output data/selection-history/<batch_id>.json
# 训练离线常数概率基线；输入必须是 selection-feature-dataset-v1
PYTHONPATH=src uv run python scripts/train_selection_baseline.py \
  --input data/selection-features/features.jsonl \
  --output-dir data/models/baseline-v1 \
  --train-end 2026-06-30 \
  --validation-end 2026-09-30 \
  --feature-set-version-id <feature_set_version_id> \
  --tracking-policy-id <tracking_policy_id> \
  --label-field label \
  --random-seed 0
# 保存显式时间点股票池；来源快照必须已批准
PYTHONPATH=src uv run python scripts/create_universe_snapshot.py \
  --root data \
  --market-root data/selection-market \
  --as-of 2026-01-05T16:00:00+08:00 \
  --source manual-universe \
  --source-snapshot-id <approved_snapshot_id> \
  --symbol 000001.SZ
# 注册策略版本并追加生命周期变更
PYTHONPATH=src uv run python scripts/register_strategy.py --input strategy.json
PYTHONPATH=src uv run python scripts/transition_strategy.py \
  --strategy-version-id <strategy_version_id> \
  --to validated \
  --changed-at 2026-01-05T16:00:00+08:00 \
  --reason "验证通过"
```

成熟任务要求显式提供单笔名义金额；这是最低佣金换算为收益率所必需的参数，系统不会猜测。

趋势交易的第一版买入执行政策为 `entry-limit-chase-v1`：收盘后信号在后续 3 个交易日有效；低开或区间内开盘按开盘价成交；高开时要求日线最低价触及买入区间上限，且追价不超过 1%；停牌和不可交易日不成交。日线触达是明确的回测近似，策略和成交记录会保存 `entry_policy_id`。

## 操作手册

### 1. 启动服务

后端和前端需要分别启动。开发环境可以使用两个终端：

```bash
# 终端一
cd backend
PYTHONPATH=src uv run uvicorn stratgauge.api.app:app --reload

# 终端二
cd frontend
corepack pnpm dev
```

打开 Vite 输出的本地地址。默认数据写入 `backend/data/selection` 和
`backend/data/selection-market`；这些运行数据已被 Git 忽略，不应提交到仓库。

### 2. 创建公式目录项

进入“数据管理” → “公式目录” → “新建公式”，填写公式名称、版本和说明。
StratGauge 不解析或执行通达信公式，只保存公式目录和版本留痕。历史批次引用的公式不会被原地修改；调整公式时应新建版本。

### 3. 每日收盘后导入选股结果

如果当天运行了通达信选股公式并需要纳入研究，建议在当日收盘后导出：

1. `选股日期` 填公式产生信号的交易日；
2. `导出时间` 填实际导出时间，使用北京时间，例如 `2026-01-05T15:05:00+08:00`；
3. 最好在当日收盘后、次日 `00:00` 前完成导出和人工标注；
4. 没有运行选股或没有候选时，不需要为了“每天一份文件”而虚构导出记录。

`选股日期` 是行情跟踪的基准日，决定最早从下一交易日起计算表现；`导出时间` 是审计证据，证明文件何时离开通达信。两者不能混用，也不要填写计划时间。

前端导入会保存原始文件、解析后的候选和内容哈希。相同证据不会覆盖旧批次。CLI 示例：

```bash
cd backend
PYTHONPATH=src uv run python scripts/import_tdx_selection.py \
  --file path/to/selection.txt \
  --selection-date 2026-01-05 \
  --exported-at 2026-01-05T15:05:00+08:00 \
  --formula-name "趋势选股" \
  --formula-version v1
```

### 4. 人工标注

在研究工作台选择批次，为每只候选记录：

- `buy_candidate`：保留为重点候选；
- `watch`：观察；
- `pass`：放弃；
- `invalid`：无效数据或不应跟踪。

人工标签是用户当时的决策记录，不是客观输赢标签。标签只追加保存，不覆盖历史。标注必须发生在导出时间之后，并在下一自然日 `00:00` 前完成；超过冻结时间应保留为未标注，而不是补写历史判断。

### 5. 创建并执行成熟任务

成熟任务用于在未来数据已经可用后计算客观结果。研究工作台选择行情源、评估时间、持有交易日、单笔名义金额、最大重试次数和超时后，点击“创建并执行一次”。

推荐规则：

- 评估时间必须晚于选股日期；
- 只使用日线、收盘后可获得的数据；
- 不为止盈、止损等尚未确认的策略参数自行添加规则；
- `Tushare Pro` 是新增流程主源，`BaoStock` 是备用和交叉校验源，`AKShare` 保留兼容入口，歪枣网只是临时恢复源；
- 网络、限流和超时错误可以有限重试，质量门、参数和响应格式错误不会盲目重试。

任务 ID 由批次、UTC 评估时间、追踪政策和行情源确定。同一配置重复提交会复用任务；同一任务和候选最多保存一个客观结果。任务事件会记录排队、执行、重试、失败和结果写入过程。

需要操作系统调度时，在 Windows Task Scheduler 中调用：

```bash
cd backend
PYTHONPATH=src uv run python scripts/run_selection_maturity_tasks.py \
  --root data/selection \
  --market-root data/selection-market \
  --limit 10
```

该命令只执行排队任务，执行结束即退出；服务内没有后台定时器、线程、Redis/Celery，也不会连接券商或下单。对已经完成收盘行情同步的全市场快照，可先用显式交易日历自动排队成熟任务：

```bash
cd backend
PYTHONPATH=src uv run python scripts/queue_selection_maturity_tasks.py \
  --root data/selection \
  --market-root data/selection-market \
  --calendar-file data/calendars/a-share-sessions.txt \
  --provider tushare \
  --horizon-sessions 10 \
  --position-notional 10000
```

队列只使用 `approved` 的全市场快照；任务保存固定股票池和输入快照 ID，运行时重放本地快照，不重新请求供应商。扫描必须使用显式交易日历，不能用自然日补齐缺口。

### 6. 查看统计和管理证据

“数据管理”支持按状态、来源、公式版本、选股日期、最新人工决策和结果政策筛选。统计区显示匹配批次、候选样本、待定数、结果样本、胜率和平均净收益。

统计规则如下：

- 结果按“候选 + 追踪政策”取最新记录，避免重复成熟导致重复计数；
- 没有对应成熟结果的候选计入待定；
- `no_entry` 和 `unresolved` 保留为独立状态，不强行算作输或赢；
- 胜率和平均净收益是历史描述性统计，不是未来收益保证。

研究工作台候选卡片的“查看历史条件分析”会调用：

```text
GET /v1/selection-batches/{batch_id}/candidates/{candidate_id}/analysis
```

接口按当前批次导出时间点筛选同公式历史结果；如历史样本包含多个追踪政策，需要追加 `tracking_policy_id` 查询参数。完成样本少于 100 个时显示“证据不足”而不返回胜率；历史入场价仅显示实际入场价分布，不构成买入价建议。

### 7. 导出数据集和时间切分

数据管理可以下载带 `schema_version=selection-dataset-v2` 的 JSONL；每条成熟结果同时记录 `outcome_policy_id`。CLI 导出不会覆盖已有文件：

全市场日线同步必须显式提供交易日历和已批准的沪深主板股票池快照；单个交易日可执行：

```bash
cd backend
PYTHONPATH=src uv run python scripts/sync_tushare_daily.py \
  --session-date 2026-08-10 \
  --calendar-file data/calendars/a-share-sessions.txt \
  --universe-snapshot-id <approved_universe_snapshot_id>
```

非交易日安全跳过；已批准的同一交易日/股票池组合不会重复请求，覆盖率不足的日线或复权因子快照不会发布。日线批准后，命令会继续请求同一交易日的 Tushare `adj_factor`，并把复权因子快照绑定到该日线快照；输出中的 `adjustment_factor_results` 单独列出因子结果。

若历史日线已经批准、但因子尚未补齐，可只运行因子补齐 CLI。它不会重新请求日线；缺少已批准日线的日期会报告为阻断并返回非零退出码：

```bash
cd backend
PYTHONPATH=src uv run python scripts/backfill_tushare_adjustment_factors.py \
  --start-date 2026-01-02 \
  --end-date 2026-01-10 \
  --calendar-file data/calendars/a-share-sessions.txt \
  --universe-snapshot-id <approved_universe_snapshot_id>
```

```bash
cd backend
PYTHONPATH=src uv run python scripts/export_tdx_dataset.py \
  --batch-id <batch_id> \
  --output data/selection-datasets/batch.jsonl
```

数据集保留未标注、未入场和未解决样本。训练前使用显式日期边界切分，不随机打乱：

```bash
PYTHONPATH=src uv run python scripts/split_tdx_dataset.py \
  --input data/selection-datasets/batch.jsonl \
  --output-dir data/selection-datasets/batch-split \
  --train-end 2026-06-30 \
  --validation-end 2026-09-30
```

命令会生成 `train.jsonl`、`validation.jsonl`、`test.jsonl` 和 `manifest.json`。manifest 保存源文件 SHA-256、schema 版本、切分边界和各分区行数；已有输出会拒绝覆盖。

### 8. 补齐历史行情

特征工程前，用显式交易日历补齐每个候选选股日前的最长回看窗口。未提供 `--sessions-file` 时，脚本从 Tushare `trade_cal` 获取交易日，不使用自然日填充：

```bash
PYTHONPATH=src uv run python scripts/backfill_selection_history.py \
  --batch-id <batch_id> \
  --provider tushare \
  --lookback-sessions 60 \
  --benchmark 000300.SH \
  --industry 399006.SZ \
  --output data/selection-history/<batch_id>.json
```

交易日文件必须是按升序排列的 ISO 日期，一行一个日期；不能用自然日猜测周末或节假日。候选由批次自动生成，基准和行业标的必须显式使用 `CODE.EXCHANGE`。报告列出缺失证券、缺失交易日、窗口不足、质量失败和可用快照 ID；只有通过质量门的 `approved` 快照会进入 `approved_snapshot_ids`，本命令不计算特征。

### 9. 特征和离线基线

`features/engine.py` 只接受严格早于选股日、且 `available_at <= as_of` 的批准行情。需要复权价格时，调用方必须显式传入覆盖全部输入交易日的已批准 `AdjustmentFactor`，并提供因子快照 ID；引擎按最新因子缩放历史价格，不修改原始未复权日线，也不改变成交量和成交额。特征快照和特征版本均不可覆盖。训练入口只接受 `selection-feature-dataset-v1`：

```bash
PYTHONPATH=src uv run python scripts/train_selection_baseline.py \
  --input data/selection-features/features.jsonl \
  --output-dir data/models/baseline-v1 \
  --train-end 2026-06-30 \
  --validation-end 2026-09-30 \
  --feature-set-version-id <feature_set_version_id> \
  --tracking-policy-id <tracking_policy_id> \
  --label-field label
```

当前基线是常数概率模型，只用于流程对照，不代表预测收益或交易建议。事后抓取的历史行情若 `available_at` 晚于历史 `as_of`，会被特征引擎拒绝。

LightGBM 是可选依赖，不影响默认离线测试；安装后可用相同时间边界训练固定配置的未校准候选模型：

```bash
uv sync --extra ml
PYTHONPATH=src uv run python scripts/train_selection_lightgbm.py \
  --input data/selection-features/features.jsonl \
  --output-dir data/models/lightgbm-v1 \
  --train-end 2026-06-30 \
  --validation-end 2026-09-30 \
  --feature-set-version-id <feature_set_version_id> \
  --tracking-policy-id <tracking_policy_id>
```

LightGBM 输出模型文本、分区指标、预测明细和不可覆盖 manifest；`calibrated=false`，当前不接入 Web、影子评分或自动晋升。

显式配置走步折和最终测试起点后，运行基础走步验证：

```bash
PYTHONPATH=src uv run python scripts/validate_walk_forward.py \
  --input data/selection-features/features.jsonl \
  --folds data/selection-features/walk-forward-folds.json \
  --final-test-start 2026-10-01 \
  --outcomes data/selection-datasets/batch.jsonl \
  --output data/models/walk-forward-v2/report.json
```

折 JSON 必须由研究配置显式提供；最终测试期只计数、不参与折验证。`--outcomes` 可选，提供后按候选/选股日关联 `selection-dataset-v2` 的客观结果：未解决样本不进入执行率分母，缺少净收益或名义金额时对应收益/换手指标保持为空；不提供时仍只生成概率指标和标签清除审计。

模型注册会复制模型、评估报告和训练 manifest，并校验 SHA-256；生命周期只能人工追加，不能自动晋升：

```bash
PYTHONPATH=src uv run python scripts/register_selection_model.py \
  --root data \
  --model data/models/baseline-v1/model.json \
  --evaluation data/models/baseline-v1/metrics.json \
  --manifest data/models/baseline-v1/manifest.json \
  --registered-at 2026-10-02T18:00:00+08:00 \
  --registered-by researcher
PYTHONPATH=src uv run python scripts/transition_selection_model.py \
  --root data \
  --model-version-id <model_version_id> \
  --to shadow \
  --changed-at 2026-10-02T18:05:00+08:00 \
  --reason "人工开始影子阶段" \
  --operator researcher
```

晋升前必须使用显式比较政策生成不可变报告；缺失的净收益、回撤、换手、信号量、特征稳定性或校准指标会阻断，不会填充默认值。先准备策略政策 JSON，例如：

```json
{
  "comparison_policy_id": "policy-gate-v1",
  "tracking_policy_id": "<tracking_policy_id>",
  "require_incumbent": false,
  "require_calibrated": false,
  "rules": [
    {
      "metric_name": "validation_log_loss",
      "direction": "minimize",
      "maximum_value": "1.0",
      "max_regression": "0.0"
    }
  ]
}
```

```bash
PYTHONPATH=src uv run python scripts/compare_selection_models.py \
  --root data \
  --challenger-model-version-id <model_version_id> \
  --policy data/models/comparison-policy.json \
  --compared-at 2026-10-03T17:55:00+08:00 \
  --compared-by researcher
```

只有人工确认并提供比较报告 ID/哈希、注册评估报告 SHA-256 后才能 promote；已有 active 冠军时必须先人工退役。不得把未校准分数称为胜率：

```bash
PYTHONPATH=src uv run python scripts/transition_selection_model.py \
  --root data \
  --model-version-id <model_version_id> \
  --to active \
  --evaluation-sha256 <evaluation_sha256> \
  --comparison-report-id <comparison_report_id> \
  --comparison-report-sha256 <comparison_report_sha256> \
  --changed-at 2026-10-03T18:00:00+08:00 \
  --reason "人工确认" \
  --operator researcher
```

影子评分独立保存，不改变人工标签、客观结果或成熟任务；输入只能是未带结果标签的候选/特征快照 JSONL：

```bash
PYTHONPATH=src uv run python scripts/score_selection_shadow.py \
  --root data \
  --model-version-id <shadow_model_version_id> \
  --input data/selection-features/shadow-input.jsonl \
  --scored-at 2026-10-03T18:05:00+08:00 \
  --output data/models/shadow-reports/run.json
```

输出字段是 `uncalibrated_model_score`，并明确 `calibrated=false`、`used_for_decision=false`，不能称为胜率。

### 10. Tushare 参考数据、时间点股票池和策略生命周期

首次同步股票主数据和交易日历：

```bash
# 需要 TUSHARE_TOKEN；只同步沪深主板（market=主板，SSE/SZSE），保存不可变 stock_basic 参考响应和时间点股票池
PYTHONPATH=src uv run python scripts/sync_tushare_universe.py \
  --root data \
  --as-of 2026-01-05T16:00:00+08:00
# 增量维护交易日缓存；不会用自然日填充，已覆盖区间不会重复调用 trade_cal
PYTHONPATH=src uv run python scripts/sync_tushare_calendar.py \
  --start-date 2025-01-01 \
  --end-date 2026-01-05 \
  --root data \
  --output data/calendars/a-share-sessions.txt
```

Tushare `daily` 返回的成交量是手、成交额是千元，系统分别转换为股和元。Token 只从 `TUSHARE_TOKEN` 读取，不进入日志或快照。

股票池必须显式提供证券和已批准来源快照：

```bash
PYTHONPATH=src uv run python scripts/create_universe_snapshot.py \
  --as-of 2026-01-05T16:00:00+08:00 \
  --source manual-universe \
  --source-snapshot-id <approved_snapshot_id> \
  --symbol 000001.SZ
```

策略版本先用 `register_strategy.py` 注册，再通过 `transition_strategy.py` 追加 `draft → validated → shadow → active → retired` 事件；不修改历史策略文件。

### 11. 理论回测报告

从 `BacktestResult` JSON 生成不可覆盖的理论报告；市场状态只能通过显式 JSON 映射提供，缺失状态标记为 `unspecified`，不会自动推断：

```bash
PYTHONPATH=src uv run python scripts/report_backtest.py \
  --input data/backtests/run.json \
  --output data/backtests/reports/run-report.json \
  --market-states data/backtests/market-states.json
```

报告按策略版本、成本政策、市场状态和入场日期分组，并包含收益、胜率、复合收益和最大回撤。它只表示理论回测，不包含推荐结果或实际成交。

### 12. 时间点股票搜索

股票搜索只读取 `as_of` 时已经存在的最新股票池快照，不使用未来成分或自动猜测交易所：

```text
GET /v1/universe/securities?as_of=2026-01-06T09:00:00+08:00&query=平安&limit=20
```

若请求时间点没有可用快照，接口返回结构化错误；当前不提供全市场主数据补齐。

### 13. 手工订单和持仓

API 提供：

- `POST /v1/manual-orders`
- `POST /v1/manual-fills`
- `GET /v1/positions`
- `POST /v1/portfolio-reviews`

持仓由成交记录重放推导，卖出不得超过当前持仓；系统不连接券商、不自动下单。

## 运行原理

StratGauge 的核心不是“自动买卖”，而是把一次收盘后选股变成一条可复核证据链：

```text
通达信公式执行
  → 收盘后导出文件
  → 原始证据与候选批次入库
  → 人工决策追加标签
  → 显式创建成熟任务
  → 日线行情适配器抓取并通过质量门
  → 固定持有期计算客观结果
  → 去重后的统计与 JSONL 样本
  → 按选股日期生成训练/验证/测试清单
  → 理论回测报告与时间点股票池搜索
```

每个环节的边界：

1. **证据不可变**：原始导出、规范化解析、标签和结果分别保存，并通过批次 ID、候选 ID、政策 ID 和行情快照连接；不通过原地覆盖修正历史。
2. **时间点正确**：信号只代表选股日期收盘后已知的信息，行情结果只能读取评估时间之前可获得的日线，训练集按选股日期向前切分，禁止未来信息泄漏。
3. **标签与结果分离**：人工选择是决策特征；胜负、未入场和未解决由日线跟踪规则产生，两者不能互相替代。
4. **质量门优先**：行情缺失、日期异常、OHLCV 不一致或供应商响应格式错误时，不生成看似完整的结果；失败原因进入任务状态或事件。
5. **幂等和可恢复**：任务指纹、内容哈希和结果去重保证重复点击不会制造重复证据；排队任务可由 Web 手动执行，也可由 Windows Task Scheduler 定期调用 CLI。
6. **人工交易边界**：系统只做记录、计算和研究展示。用户仍需自行确认买卖，不存在券商连接、自动下单、分钟数据或 Tick 数据。

导入器也支持通达信 `.EBK` 和行情表 `.txt` 选股结果：`.EBK` 使用七位市场编码；行情表使用 GBK/GB18030 的制表符列、`代码`/`名称(4)`/`涨幅%` 表头和通达信来源尾行。行情表的代码市场命名空间按通达信格式映射 `0/3` 深市、`6` 沪市、`4/8` 北市；普通 CSV 仍必须显式提供交易所，不会泛化猜测。

前端包含“研究工作台”和“数据管理”两个入口：研究工作台负责通达信导入、手动添加候选、标注和成熟；数据管理负责新建/查看公式和导入批次、按状态/来源筛选、按公式版本/日期/人工决策/结果政策查看统计、下载原始文件和 JSONL 数据集及追加归档/恢复事件，不执行硬删除。

前端：

```bash
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm typecheck
corepack pnpm build
corepack pnpm lint
```

Tushare Pro、AKShare、BaoStock 和临时歪枣网适配器默认延迟使用；默认单元测试使用冻结数据，不访问网络。`live-data` 是可选依赖组，真实抓取必须显式执行集成验证；验证脚本会保存快照、质量报告并执行主备数据差异检查。Tushare Token 只从 `TUSHARE_TOKEN` 环境变量读取；歪枣网当前仅按证券代码请求日线，`code=all` 需要更高订阅权限，其接口仅提供 HTTP。通达信导入支持 UTF-8/GB18030 的 CSV、TSV、分号分隔文件、市场前缀 `.EBK` 和行情表 `.txt`；普通 CSV 代码仍要求交换所显式存在或代码带 `.SH/.SZ/.BJ` 后缀。DuckDB 保存快照、质量和状态索引，标准行情仍保存在 Parquet。

## 文档

| 文档 | 用途 |
| --- | --- |
| `docs/系统规格说明.md` | 产品、架构、策略、数据、回测、机器学习、数据模型、API 和交付路线 |
| `docs/decisions.md` | 已确认的设计决策 |

## 项目开发规范

### 不可违反的约束

- 不连接券商，不自动创建、修改或取消订单；
- 用户始终手动确认并执行交易；
- v1 只使用日线数据，未经批准不引入分钟或 Tick 数据；
- Tushare Pro 为新增自动流程主数据适配器，BaoStock 为备用和交叉校验适配器，AKShare 为兼容适配器，歪枣网仅作临时日线恢复源；
- 不臆造价格、指标、概率、收益、解释或数据新鲜度；
- 每条推荐必须记录数据快照、特征集、策略和模型版本；
- 防止未来函数、生存者偏差和提前使用修订信息；
- 理论策略结果、推荐结果和用户实际交易必须分开保存；
- 新模型必须经过走步验证、影子评估和人工晋升；
- LLM 只能解释或起草策略规则，不得计算权威市场值或晋升生产模型；
- 数据质量失败或数据过期时，阻止发布推荐并显示原因；
- 不把历史或模型估计胜率描述为未来收益保证。

### 技术基线

- Python 3.12、`uv`、Polars、NumPy、DuckDB、分区 Parquet；
- FastAPI、Pydantic 和 OpenAPI；
- LightGBM、Optuna、MLflow；
- React、Vite、TypeScript、ECharts；
- 前端依赖使用 `pnpm`；
- Python 量化核心稳定且边界明确后，才引入 Rust/Axum 或 Tauri；
- 不凭记忆固定依赖版本，初始化时解析兼容版本并提交锁文件。

### 架构规则

- 市场数据适配器通过统一接口隔离，策略代码不得直接导入 AKShare 或 BaoStock；
- 特征计算必须确定且版本化；
- 策略表达式使用安全解析器或类型化 AST，禁止 `eval` 和 `exec`；
- 分离信号生成与成交模拟；
- 区分 `event_time`、`available_at`、`ingested_at` 和 `as_of`；
- 费用和市场规则使用有效日期配置；
- 批处理任务应幂等并能补齐缺失交易日；
- 先定义 API schema，再让前端依赖后端实现。

### 质量要求

- 为指标、策略表达式、标签、持仓记账和费用计算添加单元测试；
- 为停牌、未成交、涨跌停、公司行为、T+1 和同日止损/目标歧义添加黄金回测夹具；
- 添加数据适配器契约测试和数据质量测试；
- 每个未来函数或回测缺陷都要有回归测试；
- 金融时间序列使用按时间排序的训练/验证/测试划分，禁止跨日期随机打乱；
- 完成前运行格式化、代码检查、类型检查和相关测试；
- 最终检查差异，重点确认数据泄漏、静默降级、不可复现状态和过期数据行为。

### 完成定义

任务只有在以下条件全部满足时才算完成：需求已实现并记录；相关自动检查通过；结果可由保存的 ID 和版本复现；没有使用未来信息；失败和过期数据路径明确；没有越过自动交易边界；行为变化时同步更新 `PLANS.md` 和受影响规格。
