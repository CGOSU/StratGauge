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
- 完成 API/前端：新增 `/v1/selection-batches` 导入和列表接口、批次详情接口及人工标签接口；新增 React 候选队列、批次选择、备注和四类人工标签操作，所有写操作仍不连接券商。
- 验证：后端测试 65 项通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
- 结果持久化：选股结果按内容寻址写入 `tdx/outcomes`，候选详情 API 和前端可显示最新结果；新增按批次评估全部候选的纯函数入口，严格遵守 `available_at` 和评估时点。
- 验证：后端测试 67 项通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
- 自动成熟入口：新增 `mature_tdx_selection.py`，显式选择 AKShare/BaoStock/歪枣网、评估时间和固定持有期；逐候选请求，质量门失败不写入结果；成功后保存行情快照和客观结果；不启动后台定时器、不自动交易。
- 验证：后端测试 69 项通过（1 项环境相关跳过）；Ruff、MyPy 通过。
- 数据集出口：新增 `selection_dataset.py` 和 `export_tdx_dataset.py`，将批次、公式版本、最新人工标签、客观结果和来源快照 ID 导出为不可覆盖 JSONL；无成熟结果的候选保留为待定。
- 验证：后端测试 72 项通过（1 项环境相关跳过）；Ruff、MyPy 通过。
- 审查修复：结果新增追踪政策指纹、费用方案和名义金额；最低佣金无名义金额时拒绝计算；统计和 JSONL 只取每候选/每政策最新结果；人工标签限制在导出后至下一自然日 00:00；新增批次统计 API；前端展示待成熟数量和胜负分布；修复 React StrictMode 健康检查和过期请求 loading 状态。
- 验证：后端测试 77 项通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
- 样例兼容：检查真实脱敏 `0810.EBK` 和 `081020260810.txt`；新增 GBK/GB18030 制表符行情表解析、通达信表头识别、尾行跳过和市场命名空间映射；两份样例的 4 个代码均解析为 `002167.SZ`、`600593.SH`、`603183.SH`、`603980.SH`。样例文件加入 `.gitignore`，未进入提交。
- Web 体验：新增公式目录和“新建公式”选择流程；导入批次只提交 `formula_id`；前端改为浅色 shadcn/ui 基础组件，并按标题、正文、辅助信息和状态徽章区分文字层级；一次性成熟操作仍可选择行情源、评估时间、持有交易日和单笔名义金额。
- 验证：后端测试 83 项通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
- 遗留问题：尚未接入真正的后台定时调度；用户止盈止损、持有期、胜负阈值和名义金额尚未冻结；本轮改动尚未提交。

### 2026-08-10 Web 数据管理

- 完成内容：新增公式和导入批次的数据管理 API；保存原始文件名；支持全部/启用中/已停用筛选、批次详情、标签/结果计数、原始文件下载、归档和恢复；生命周期操作采用追加式事件，不执行硬删除；归档批次禁止标注和成熟，归档公式禁止新导入。
- 前端：新增“研究工作台 / 数据管理”导航；管理页使用浅色 shadcn/ui 组件展示公式、批次、候选状态、生命周期历史和原始文件操作。
- 验证：后端 85 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
- 遗留问题：仍未接入后台定时调度和真实 AKShare 主源验证。

### 2026-08-10 Web 数据集导出

- 完成内容：复用现有数据集构建器，新增 `GET /v1/data-management/batches/{batch_id}/dataset`；Web 数据管理页可下载包含待定候选、最新人工标签、最新客观结果和来源快照 ID 的 JSONL。
- 设计：Web 只生成内存响应，不写入服务器导出文件；CLI 的长期 JSONL 导出和拒绝覆盖规则保持不变。
- 验证：后端 85 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 公式编辑体验

- 现象：数据管理页只有公式停用/恢复，没有编辑入口；普通文本区域的鼠标指针容易被误认为可编辑。
- 修复：增加“编辑为新版本”对话框，预填公式名称、版本和说明；保存时复用公式创建接口生成新目录项，不覆盖旧公式；页面明确提示必须修改名称或版本号。
- 交互：全局普通页面使用默认指针，输入控件使用文本指针，按钮、链接和选择控件使用指针；未禁用文本复制。浏览器若开启 F7 光标浏览模式，仍需按 F7 关闭，这是浏览器功能而非页面可编辑状态。
- 验证：前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 手工候选录入

- 完成内容：新增 `POST /v1/selection-batches/manual`；手工批次使用 canonical JSON 保存原始输入证据，按请求内容生成批次 ID，重复提交幂等；普通六位代码必须显式选择 SH/SZ/BJ，重复证券拒绝。
- 前端：研究工作台新增手动添加候选对话框，支持公式、日期、记录时间、逐条候选、名称、备注、预览、移除和取消；数据管理增加通达信/手工来源筛选。
- 验证：后端 94 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 阶段 0 主源验证重试

- 执行：`PYTHONPATH=src uv run python scripts/verify_live_adapters.py --symbol 000001.SZ --start-date 2024-01-02 --end-date 2024-01-05`。
- 结果：AKShare 仍返回 `AKShare fetch failed for 000001.SZ`，退出码 2；未将 BaoStock 或歪枣网结果冒充 AKShare 主源通过，阶段 0 退出条件继续阻塞。

### 2026-08-10 成熟任务可靠调度

- 完成内容：新增本地成熟任务模型、任务/事件 JSON 账本、确定性任务指纹、创建幂等、排队/执行中/完成/部分完成/失败状态、陈旧运行恢复、有限重试和同任务候选结果去重；保留原同步成熟接口。
- API/CLI：新增 `POST/GET /v1/selection-batches/{batch_id}/maturity-tasks`、`POST /v1/maturity-tasks/{task_id}/run` 和 `run_selection_maturity_tasks.py`；CLI 只执行排队任务，适合由 Windows Task Scheduler 显式调用，不注册服务内后台定时器。
- 前端：成熟区改为创建幂等任务并立即执行，显示任务状态、尝试次数、写入/拒绝数量、事件和重试提示，支持手动刷新及排队任务再次执行。
- 验证：后端 100 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。
