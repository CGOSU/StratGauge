# 会话记录

本文件按时间追加每次工作的完成内容、实际验证和遗留问题，不记录敏感信息或未经验证的结果。

## 记录格式

每次记录应包含日期、完成内容、实际执行的验证及遗留问题。

## 会话

### 2026-08-09

- 完成内容：将 8 份英文规格文档和路线图翻译、合并为 `docs/系统规格说明.md`；将 `AGENTS2.md` 的项目说明和开发规范合并进 `README.md`；删除已合并的英文规格文件和 `AGENTS2.md`。
- 验证：检查文档目录和旧文件引用，确认仅统一规格文件保留相关内容。
- 遗留问题：`docs/clean-code.md` 是仓库规则文件，未并入产品规格文档。

### 2026-08-09 实现检查点 1、2

- 完成内容：创建 `backend/` Python 工程；添加不可变 Pydantic 领域模型、标准代码规范化、AKShare/BaoStock 统一适配器接口和 provider 行标准化。
- 验证：`PYTHONPATH=src uv run python -m unittest discover -s tests -v`（15 项通过）；`uv run ruff format --check .`、`uv run ruff check .` 和 `uv run mypy src tests` 均通过。
- 遗留问题：策略 AST、回测、API 和前端尚未实现；真实适配器集成验证尚未执行。

### 2026-08-09 实现检查点 3

- 完成内容：添加基于内容寻址的原始 JSON、标准化 Parquet 和清单存储；添加幂等写入、状态更新、质量报告、缺失交易日/证券、重复日线和跨源差异检查；添加发布阻断式 `ingest` 流程；DuckDB 用于快照和质量索引。
- 验证：`PYTHONPATH=src uv run python -m unittest discover -s tests`（23 项通过）；`uv run ruff format .`、`uv run ruff check .` 和 `uv run mypy src tests` 均通过。
- 遗留问题：策略 AST、回测、API 和前端尚未实现；真实适配器集成验证尚未执行。

### 2026-08-09 实现检查点 4—6

- 完成内容：添加安全表达式 AST 和示例策略；实现下一交易日回测、保守同日止损/目标处理、T+1、滑点、费用和最小佣金；添加股票分析 API；创建 React/Vite/TypeScript 前端最小外壳。
- 验证：后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -v`（41 项通过）；`uv run ruff format .`、`uv run ruff check .`、`uv run mypy src tests` 均通过；前端 `corepack pnpm typecheck`、`corepack pnpm build`、`corepack pnpm lint` 均通过。
- 遗留问题：真实 AKShare/BaoStock 集成验证尚未执行；用户真实策略参数尚未提供。

### 2026-08-09 阶段 0 退出验收补充

- 完成内容：添加阶段 0 JSON 黄金夹具和回测特征集溯源；修正停牌退出不可成交；增加 `/healthz`、前端 API 状态和 Vite 开发代理；加入可选 `live-data` 依赖组及显式双源验证脚本；可选 provider 缺失测试改为兼容已安装依赖的环境。
- 验证：默认环境 `uv sync --no-extra live-data` 后，后端 44 项测试通过；Ruff、MyPy（含 `scripts`）通过；前端 typecheck、build、lint 通过。BaoStock 2024-01-02—2024-01-05 成功抓取 4 行并通过质量门；AKShare 因 `push2his.eastmoney.com` 代理连接失败，双源验证退出码 2。
- 遗留问题：阶段 0 仍缺 AKShare 主源网络验证和主备交叉校验；用户真实策略参数尚未提供。

### 2026-08-09 评估 efinance

- 完成内容：检查 `Micro-sheep/efinance` 当前主分支、PyPI 0.5.9、股票历史 K 线实现和许可证；确认其直接调用 Eastmoney 历史 K 线接口，不改变当前数据源独立性。
- 验证：使用临时环境执行 `efinance 0.5.9` 的 000001.SZ 日线请求，因同一 `push2his.eastmoney.com` 代理连接失败；未修改项目依赖。
- 结论：暂不接入；若后续需要，仅作为 Eastmoney 客户端适配器，并强制日线、不复权和显式代码校验。

### 2026-08-09 阶段 0 AKShare 复测

- 验证：`cd backend && uv sync --extra live-data && PYTHONPATH=src uv run python scripts/verify_live_adapters.py`；依赖同步成功，脚本退出码 2。
- 结果：AKShare 仍无法访问 `push2his.eastmoney.com`，底层为代理 `127.0.0.1:7890` 返回 `RemoteDisconnected`；随后直接调用 AKShare 也得到同样的 `ProxyError`。
- 状态：不能宣称 AKShare 与 BaoStock 双源交叉校验完成；阶段 0 退出仍被网络验证阻塞。

### 2026-08-09 评估歪枣网

- 完成内容：阅读飞书公开文档，确认其提供 A 股日线、周线、月线、分钟/时线、指数、基金等接口，采用 Token 鉴权和积分/订阅模式。
- 结果：公开文档未说明底层数据来源、修订规则、质量指标、SLA、停牌语义或商业授权边界；高频数据不符合本项目范围。
- 结论：暂不接入；记录为待凭证和样例响应验证的候选日线恢复源，不作为独立交叉校验源。

### 2026-08-09 歪枣网 SDK 检查

- 验证：检测到 `WAIZAO_TOKEN` 环境变量已设置，但未输出其内容；临时检查 `waizao` SDK，确认日线接口为 `getDayKLine`。
- 结果：SDK 使用 `http://api.waizaowang.com/doc/getDayKLine`，将 Token 放入 URL 查询参数；HTTPS 同路径请求当前连接失败。
- 状态：未发送真实 Token；等待供应商提供 HTTPS 或安全鉴权方式后再进行数据验证。

### 2026-08-09 临时接入歪枣网日线

- 完成内容：新增 `WaizaoAdapter`，从 `WAIZAO_TOKEN` 环境变量读取凭证；固定使用 A 股、不复权日线，通过 POST 请求体发送参数；新增验证脚本 `--primary waizao` 选项。
- 额外验证：`code=all` 单日全市场请求被服务端拒绝，提示仅订阅服务用户开放；因此保留按证券代码请求，不实现未授权的批量模式。
- 实际验证：000001.SZ 2024-01-02—2024-01-05 返回 4 行；主源自身质量门通过。成交量从供应商手数转换为股后，价格与 BaoStock 一致，但成交量/成交额仍因精度差异导致严格交叉校验失败。
- 代码验证：Ruff format、Ruff check、MyPy 通过；后端 45 项单元测试通过（1 项环境相关跳过）。
- 后续修正：成交量/成交额比较增加 0.001% 相对容差，价格继续使用绝对 0.01 容差；歪枣网适配器升至 `0.2`，避免单位转换后的新标准化结果复用旧快照身份。
- 复测：`000001.SZ` 2024-01-02—2024-01-05 的歪枣网 + BaoStock 验证脚本返回 `cross_source_approved=true`；主源和备用源均 4 行且质量门通过。
- 遗留问题：歪枣网仅 HTTP；Token 应在临时使用结束后撤销/更换；AKShare 主源验证仍未完成。

### 2026-08-09 规划通达信半手动闭环

- 需求：用户决定暂缓继续导入外部行情平台，改为零额外平台费用的半手动方案：通达信执行选股公式并导出结果，StratGauge 负责导入、人工标注、后续行情跟踪、自动胜负结果、统计和机器学习样本。
- 方案：不实现通达信公式解析器或插件；保存原始导出文件和批次元数据，保留全部候选；人工标注与客观结果分离；默认收盘后导入，最早下一交易日执行。
- 风险边界：该流程是被动观察/研究闭环，不是全市场历史回测；样本受通达信公式和人工选择影响，机器学习必须保留时间点、公式版本、人工标注时点和未成交/未解决状态。
- 完成实现：新增 `domain/selections.py`、`data_pipeline/tdx_import.py` 和 `selection_store.py`；支持 UTF-8/GB18030、CSV/TSV/分号分隔、显式交换所校验、公式/文件哈希、原始文件与解析 JSON 的不可变保存；新增 `import_tdx_selection.py` 和 `label_tdx_selection.py` 命令；人工标签采用追加式保存。
- 结果切片：新增 `backtest/selection_outcomes.py`，按“下一可交易日开盘进入、固定持有交易日后收盘退出”计算净收益，输出胜/负/平、未入场和未解决；严格按 `available_at` 和评估时间过滤未来行情，不擅自加入止盈止损。新增 `backtest/selection_stats.py`，完成样本与未解决样本分开统计。
- 验证：Ruff、MyPy 通过；后端 61 项测试通过（1 项环境相关跳过）；CLI 端到端导入并标注烟测通过。
- 遗留问题：尚未接入候选队列 API/前端和自动成熟任务；需要真实脱敏导出样例确认市场列格式；用户止盈止损、持有期和胜负阈值尚未冻结；Git 已初始化但未提交。
