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
- AKShare 主数据适配器、BaoStock 备用适配器；
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
PYTHONPATH=src uv run python scripts/verify_live_adapters.py
# 临时使用歪枣网（需要本地设置 WAIZAO_TOKEN）
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
  --provider baostock \
  --evaluated-at 2026-01-10T17:00:00+08:00 \
  --horizon-sessions 5 \
  --position-notional 10000
# 导出可追溯 JSONL 数据集；目标文件已存在时拒绝覆盖
PYTHONPATH=src uv run python scripts/export_tdx_dataset.py \
  --batch-id <batch_id> \
  --output data/selection-datasets/batch.jsonl
```

成熟任务要求显式提供单笔名义金额；这是最低佣金换算为收益率所必需的参数，系统不会猜测。

导入器也支持通达信 `.EBK` 和行情表 `.txt` 选股结果：`.EBK` 使用七位市场编码；行情表使用 GBK/GB18030 的制表符列、`代码`/`名称(4)`/`涨幅%` 表头和通达信来源尾行。行情表的代码市场命名空间按通达信格式映射 `0/3` 深市、`6` 沪市、`4/8` 北市；普通 CSV 仍必须显式提供交易所，不会泛化猜测。

前端包含“研究工作台”和“数据管理”两个入口：研究工作台负责通达信导入、手动添加候选、标注和成熟；数据管理负责查看公式和导入批次、按状态/来源筛选、下载原始文件和 JSONL 数据集及追加归档/恢复事件，不执行硬删除。

前端：

```bash
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm typecheck
corepack pnpm build
corepack pnpm lint
```

AKShare、BaoStock 和临时歪枣网适配器默认延迟使用；默认单元测试使用冻结数据，不访问网络。`live-data` 是可选依赖组，真实抓取必须显式执行集成验证；验证脚本会保存快照、质量报告并执行主备数据差异检查。歪枣网当前仅按证券代码请求日线，`code=all` 需要更高订阅权限；其接口仅提供 HTTP，Token 不得写入代码、日志或提交。通达信导入支持 UTF-8/GB18030 的 CSV、TSV、分号分隔文件、市场前缀 `.EBK` 和行情表 `.txt`；普通 CSV 代码仍要求交换所显式存在或代码带 `.SH/.SZ/.BJ` 后缀。DuckDB 保存快照、质量和状态索引，标准行情仍保存在 Parquet。

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
- AKShare 为主数据适配器，BaoStock 为备用和交叉校验适配器，歪枣网仅作临时日线恢复源，Tushare Pro 为可选项；
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
