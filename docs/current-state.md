# 当前状态

## 项目

- 名称：`StratGauge`
- 定位：是一个把个人选股和买卖策略自动化、用历史数据与机器学习评估胜率和风险，并由用户手动决策交易的 A 股 AI 策略助手

## 当前目标

- 阶段 0 的实现检查点 1—6 已完成，代码退出验收通过；Tushare `daily` 主源验证和与 BaoStock 的固定区间交叉校验已通过；股票池现限定为沪深主板，已生成 3193 条主板 `stock_basic` 快照；交易日历已由 BaoStock + AKShare 交叉校验并缓存，阶段 0 运行验收不再阻塞。Tushare `trade_cal` 原生端点仅剩供应商溯源补验。
- 新增无额外数据平台费用的半手动闭环：通达信导出选股结果，系统人工标注，随后用日线跟踪并自动生成结果、统计和机器学习样本。

## 已知状态

- 产品、架构、策略、数据、回测、机器学习、数据模型和 API 规格已合并为 `docs/系统规格说明.md`，并统一使用中文。
- 已建立 `backend/` Python 工程、Pydantic 领域模型、Tushare/BaoStock/AKShare/歪枣网统一适配器契约、Tushare `stock_basic`/`trade_cal` 参考数据快照、DuckDB 清单索引、内容寻址 JSON/Parquet 快照、质量门、安全策略 AST、下一交易日回测和股票分析 API；另有带 API 健康状态的最小 React/Vite 前端外壳。默认测试不访问网络；真实验证脚本位于 `backend/scripts/verify_live_adapters.py`。
- 已实现通达信半手动选股首个可验证切片：`tdx_import.py` 支持 UTF-8/GB18030 的 CSV、TSV、分号分隔文件、通达信 `.EBK` 市场前缀格式和通达信行情表 `.txt`；批次记录公式、原始文件名和文件哈希；原始导出和解析结果不可变保存；人工标签和客观结果追加保存；`selection_outcomes.py` 支持明确固定持有期、政策指纹和名义金额的客观结果计算；`selection_stats.py` 按候选/追踪政策取最新结果并分离完成、未入场和未解决样本；FastAPI 候选队列、统计、一次性成熟和成熟任务接口，以及 React 导入、标注、统计、成熟任务状态和数据管理控制已接入。
- 歪枣网适配器支持按证券代码获取不复权日线，使用 `WAIZAO_TOKEN` 环境变量；`code=all` 当前 Token 无权限。成交量已从手转换为股，原始响应仍保留供应商字段。成交量和成交额交叉比较允许 0.001% 相对容差以覆盖供应商取整。

## 待处理

- 行情同步诊断目前没有独立的浏览器视觉回归/Storybook 测试、同步阶段进度轮询/取消操作；未成交/部分成交仍无独立前端录入入口。`git push` 仍因 `master` 未配置远程仓库而不可执行。
- Tushare `daily` 已用 `000001.SZ` 的 2024-01-02—2024-01-05 区间通过质量门，并与 BaoStock 同区间交叉校验通过；`stock_basic` 已生成 `tushare-reference-278819ba7c8d5c92fe37` 和 `universe-d8b8d8f30a3f50c43fd9`，共 3193 条记录，市场均为主板且交易所仅为 SSE/SZSE。2026-08-10 已真实完成全市场日线和复权因子同步，均为 3193/3193、100% 覆盖并通过批准；日线快照为 `snapshot-844cde11ae0c5b85f937`，因子快照为 `snapshot-b42e9d0261edad6fa089`。BaoStock + AKShare 已交叉验证 2024-01-01—2026-08-11，共 631 个交易日，缓存为 `backend/data/calendars/trade-cal-sse.txt`，manifest 为 `calendar-5dc7698e9739a85ac100`；Tushare `trade_cal` 原生端点的供应商溯源请求仍返回 40203，但不影响该备用缓存使用。
- 已用真实脱敏 `0810.EBK` 和 `081020260810.txt` 样例确认格式：`.EBK` 为 CRLF 七位市场前缀代码；`.txt` 为 GBK 制表符行情表，包含 30 列和 `#数据来源:通达信` 尾行；两份样例的 4 个代码均可解析为 `002167.SZ`、`600593.SH`、`603183.SH`、`603980.SH`，样例文件已加入忽略规则，不进入提交。
- 当前已有 CLI `mature_tdx_selection.py` 和 Web 一次性成熟操作，新增本地成熟任务账本、事件审计、幂等执行、可恢复超时、有限重试和 `run_selection_maturity_tasks.py` 显式调度入口；JSONL 数据集带 schema 版本，并可由 `split_tdx_dataset.py` 按选股日期生成不可覆盖的 train/validation/test 清单和源文件哈希 manifest；第一版研究参数已冻结为 10 个交易日固定退出、无止盈止损、扣费后净收益胜负、100 股整手和默认 10000 元名义金额，后续用户偏好必须创建新政策版本。
- 数据管理第一版已完成：公式和导入批次支持 Web 新建/查看、筛选、编辑为新版本、停用/恢复、批次详情、原始文件下载和 JSONL 数据集下载；统计支持公式版本、来源、选股日期范围、最新人工决策、结果政策和生命周期筛选；研究工作台支持手工逐条添加候选，批次按通达信/手工来源筛选；生命周期采用追加式事件，归档批次只读，普通研究工作台默认隐藏归档数据。公式编辑不会原地覆盖历史记录。
- 机器学习已完成数据准入、历史补齐、特征契约、基线训练、LightGBM 候选训练、带客观结果证据的走步指标、Isotonic 概率校准、基础影子推理和只读模型证据展示；新增四用途数据集构建器、训练标签契约、时间切分/未来标签 purged 分区和不可变准备 CLI，但模型训练尚未自动接入新批次：`features/contract.py`/`engine.py` 提供带版本和溯源的收益、动量、波动率、流动性、均线及基准相对强弱特征；`train_selection_baseline.py` 和可选依赖 CLI `train_selection_lightgbm.py` 以 `selection-feature-dataset-v1`、单一追踪政策和相同时间切分训练模型；LightGBM 输出未校准概率、指标、模型文本和不可覆盖 manifest；`walk_forward.py`/`validate_walk_forward.py` 可选读取 `selection-dataset-v2`，按时间清除训练标签并报告结果覆盖率、执行率、平均净收益、最大回撤、换手和市场状态平均收益；`calibrate_selection_probabilities.py` 只用 validation 拟合 PAVA 校准器，test 隔离评估并记录校准时间、适用日期和源哈希；`ModelRegistryStore` 和 `shadow.py` 已支持 baseline/LightGBM 注册、运行时工件校验、可选校准分数和独立评分报告；新增按候选查询模型版本、分数、样本量、适用日期和失败原因的只读 API/前端面板，所有影子结果仍标记不用于决策；当前尚未实现模型批次自动接入。
- 研究基础已增加 Tushare 股票列表/交易日历同步、BaoStock + AKShare 备用交易日历交叉校验、交易日历增量缓存、时间点股票池快照、证券主数据快照、`/v1/universe/securities` 与 `/v1/universe/master` 搜索、内容寻址理论回测报告、策略生命周期审计，以及手工订单/成交/持仓/复盘 API；趋势交易 A1 买入规则、A2 胜负/成本规则、B1/B2 全市场增量行情及多日编排、C1 交易日历缓存、C2/C2.1 复权因子同步与历史批量补齐、显式前复权特征输入、C3 已批准行情自动成熟队列以及条件胜率和买入价分析查询已完成；分析查询严格按公式、追踪政策和批次导出时间点筛选历史证据。

## 最近一次更新

- 2026-08-14 合并持仓/交易记录中旧手工台账的订单和成交录入：单个表单通过原子确认交易接口同时写入订单与成交，自动生成订单 ID 和成交 ID；卖出增加必填卖出原因，复盘和只记录未成交订单的后端 API 保持兼容。
- 2026-08-14 完成主板收盘行情同步诊断 API/UI：API 为每个日线和复权因子阶段返回中文状态、覆盖率、预期/实际证券数、质量问题、处理建议、快照 ID 和 `quality_run_id`，质量拒绝仍保留为 HTTP 200 的不可用快照并通过 `research_ready=false` 标记；供应商、存储、前置条件和未知异常返回稳定 `code` 及不泄露凭据的中文原因/建议。导入数据页同步面板区分完整成功与含未批准数据的完成状态，并明确未批准数据不会进入研究；桌面双列和窄屏单列布局支持长证据 ID 换行。后端 API 测试 47 项通过，前端 typecheck/lint/build 通过。未跟踪 `.agents/` 未纳入本轮改动。
- 2026-08-13 完成桌面研究工作台 UI-01/UI-02：前端新增固定左导航、顶部运行状态栏、中央工作区和右侧 Stock Detail Drawer；工作台、今日信号、观察池、持仓、交易记录、策略和分析页面接入 `/v1/workbench/*` 只读投影。详情抽屉提供行情/判断/信号/交易/分析五个 Tab，快速判断仅追加 `SelectionAnnotation`，支持评分、观点、操作意图、固定标签和备注；保留现有手工台账录入入口。前端 typecheck、build、lint 和 `git diff --check` 通过。统一 Drawer 下的用户确认 BUY/SELL、默认工作台连续处理入口和观察池重新判断变化摘要已接入；新增左侧“导入数据”路由，恢复通达信导入表单和批次列表；导入数据页的候选区现为只读，旧标签仅展示兼容历史，新的 Annotation 判断和交易记录统一从工作台股票详情进入；前端已移除批次成熟操作区，客观结果和历史成熟数据仍保留为只读证据；导入数据页新增“同步收盘行情”，后端只接受缓存交易日并校验沪深主板股票池，写入 Tushare 日线和同源复权因子批准快照；后续主要进行全量回归与视觉验收。
- 2026-08-13 完成前端视觉收敛：统一浅色金融研究台 token、中文系统字体、排版层级、卡片/表格/Drawer 和 A 股红涨绿跌、独立错误、蓝色信息、琥珀待处理语义；补充导航无障碍属性、数字样式 hook 和 confirmed KPI 状态。typecheck、lint、build、`git diff --check` 及 CSS 变量/权重扫描通过；Chrome headless 在 390/1024/1440 视口确认无页面级横向溢出。无后端 API 时的 JSON 错误提示属于既有数据服务不可用状态。随后完成布局滚动修复：应用壳固定为 `100dvh`，主内容独立纵向滚动，顶部状态栏固定 60px（移动端 72px），Drawer layer 从状态栏底部开始。
- 2026-08-11 新增仓库根目录统一启动入口：`uv run --project backend python start.py`，Windows 可双击 `start.cmd`。启动器并发运行后端 Uvicorn（固定使用 `--app-dir src`）和前端 `corepack pnpm dev`，带服务日志前缀，并在服务失败、Ctrl+C 或窗口终止时清理进程树；Windows 真实健康检查确认后端 `/healthz` 和前端首页返回 200，退出后 8000/5173 端口均释放。启动器不自动安装依赖或打开浏览器。后端启动器测试 5 项通过，Ruff/MyPy 通过。
+- 2026-08-11 完成可复用人工评审标签切片：固定四分类标签支持在 Web 评审对话框中按分类展示、创建并立即选中；数据管理支持改名、归档和恢复；Annotation 保存不可变历史快照，归档/改名不影响历史显示。可复用标签保持在 `selection-annotation-dataset-v1`、特征、训练、注册、shadow、推荐和交易边界之外。前端 typecheck/build/lint 通过；后端完整测试 306 项通过、1 项跳过；后端源 MyPy 和 Ruff check 通过。完整 Ruff format 检查仍发现既有 9 个未格式化文件，未在本切片中改动。
- 2026-08-11 完成 ML-02I/ML-02J：新增独立四用途走步验证和用途感知模型比较。走步按显式 folds 执行折内 train-only 训练，按 `label_available_at` 清除，校验 evaluated_at、特征可用时间、注册/校准时间和完整血缘；`purged` 永不消费，final test 只保留边界和计数，不参与训练、校准、指标或比较。比较按用途、标签契约、类别顺序、特征列和各项哈希/时间血缘阻断不一致，按 validation 行数聚合指标，并只依据 CLI 显式的绝对阈值、相对退化上限和校准要求生成人工门禁。两类报告分别保存到 `models/feature-registry/walk-forward-reports/` 与 `comparisons/`，不可覆盖且仅允许字节相同幂等重试；不自动晋升、不修改 challenger/shadow/retired 生命周期，不接入 API、Web、推荐或交易。相关实现、针对性测试和完整后端验证已通过。
- 2026-08-11 完成人工评审重构：新增追加式 `SelectionAnnotation`，综合评分/观点/当前决策为必填，位置/形态/量价/板块标签使用 `selection-annotation-taxonomy-v1` 固定词表；新增独立候选排除事件、观察池状态投影、候选关联的原子订单与成交接口；旧 `ManualSelectionLabel` 保留为只读兼容数据。
- 2026-08-11 完成训练标签契约和时间切分：新增 `training_datasets.py` 与 `prepare_selection_training_dataset.py`，四类数据集分离为独立目标；未成熟/未来不可见标签进入 `purged` 或跳过统计，保留源行哈希和稳定样本 ID。
- 2026-08-11 完成四类标签常数基线：新增 `constant_label_distribution` 和 `train_selection_label_baseline.py`；人工决策支持三分类，其余用途独立二分类；只使用 train 类别分布，分别评估 validation/test，`purged` 永不消费；尚未接入模型注册。
- 2026-08-11 完成用途特征与标签连接：新增 `training_feature_join.py` 和 `join_selection_training_features.py`；只消费调用方显式提供的特征快照，按证券/观察日/评审时点选择唯一安全最新快照，未来或歧义快照进入失败报告，`purged` 标签不连接；输出同时保留原始标签分区、标签 split manifest、准入报告和连接 manifest，供后续血缘校验。
- 2026-08-11 完成 SG-ML-02D 用途特征基线：新增 `training_feature_baseline.py` 和 `train_selection_feature_baseline.py`；严格校验用途、标签契约、特征集、特征列、样本 ID、源哈希、分区哈希、标签切分证据和连接失败；完整连接后使用 train 标签分布运行独立 `constant_label_distribution`，validation/test 评估，`purged` 永不消费；尚未自动注册、校准、晋升或接入交易。
- 2026-08-11 完成 SG-ML-02E 四用途 LightGBM 候选接入：新增 `training_feature_lightgbm.py` 和 `train_selection_feature_lightgbm.py`；复用已验证的用途特征 split，二分类用途使用 binary，人工决策使用 multiclass；仅 train 拟合，validation/test 评估，模型身份绑定用途/标签契约/特征连接 manifest/标签血缘，默认环境仍不加载可选依赖；尚未自动校准、注册、晋升、影子评分或接入交易。
- 2026-08-11 完成 SG-ML-02F 四用途概率校准：新增 `isotonic.py`、`training_feature_lightgbm_calibration.py` 和 `calibrate_selection_feature_probabilities.py`；二分类正类取补集，三分类 one-vs-rest PAVA 后归一化；只用 validation 拟合、test 隔离评估，读取时校验模型/指标/预测/manifest 哈希、用途/标签契约/特征列/分区和 `purged` 证据，校准时间不得早于标签可用时间；旧二分类校准已复用共享 PAVA 并保留兼容入口；尚未自动注册、晋升、影子评分或接入交易。
- 2026-08-11 完成 SG-ML-02G 四用途模型不可变注册证据：新增 `training_feature_registry.py` 和 `register_selection_feature_model.py`；独立于旧 `ModelRegistryStore`，重新校验完整 LightGBM 训练目录，可选绑定同源用途校准目录；复制模型、运行工件、指标、预测和 manifest 全部证据，记录用途/标签契约/类别/特征列/完整血缘哈希；注册固定为 `challenger`，不可覆盖，不自动迁移生命周期、比较、影子评分或交易。
- 2026-08-11 完成 SG-ML-02H 四用途模型人工 shadow 契约：新增用途模型追加式 `challenger → shadow → retired` 生命周期和 `transition_selection_feature_model.py`；新增 `training_feature_shadow.py`、`score_selection_feature_shadow.py` 及独立 shadow 证据存储。输入显式绑定用途/标签契约且拒绝结果标签；二/三分类 raw 概率和注册同源校准概率均校验特征集、列、注册/授权/特征/校准时间边界；候选级失败保留在报告，结果固定 `used_for_decision=false`，未接入比较、晋升、API、推荐或交易。
- 2026-08-11 完成多用途机器学习数据集底座：保留候选成功率数据集，新增逐评审人工判断数据集、T+1/T+3/T+5/T+10 未来表现及最大涨幅/回撤/目标达成字段、买入择时数据集和持仓 session 级卖出择时数据集；所有择时数据集使用显式交易日历和评估截止时间，未成熟标签保持为空；新增 `export_selection_ml_datasets.py`。
- 2026-08-11 全面更新技术与使用文档：新增 `docs/使用文档.md`，逐项说明 34 个后端 CLI 的 input、HTTP API 请求参数和前端表单字段；新增行情远程拉取/本地缓存决策表，明确日历缺口、全市场批准快照、单票 provider 模式、因子补齐和 approved-snapshots 重放的不同边界；重写 `docs/系统规格说明.md` 以匹配当前代码实现，并在 README 增加文档入口。
- 2026-08-11 完成前端体验收口：研究、数据管理和持仓工作区的 API 错误提示统一为可关闭的吸顶提示；所有日期/日期时间字段统一使用 `CalendarInput`，提供日历按钮、清空操作、键盘焦点反馈和统计日期范围约束；删除旧的重复错误 JSX 和日期专用样式。前端类型检查和生产构建通过，桌面与 390px 移动视口实测无横向溢出。
- 2026-08-11 优化通达信文件上传控件：原生文件 input 保留为可访问的实际输入，视觉上改为带上传图标、格式说明、文件名/大小、重新选择和清除操作的上传卡片；已选状态使用独立的选中语义色，移动端长文件名可换行且无横向溢出。
- 2026-08-11 完成前端设计一致性和 Python 死代码复核：持仓台账的原生下拉框统一使用 `native-select` token 并补齐可见标签、保存中状态和 `aria-busy`；`AlertDescription` 改为语义化段落；确认 `selection_training.py` 中未被仓库引用的两个特征数据集辅助函数已删除，其余候选均保留为协议、模型或 CLI 入口。
- 2026-08-11 修复通达信行情表兼容回归：识别 `名称(n)` 版本化表头，GB18030 编码的 `名称(5)` 实际样例可解析 5 个候选；普通表格仍要求明确交易所或代码后缀，不启用代码前缀猜测。
- 2026-08-11 优化前台批次和标记信息层级：批次列表、批次详情以选股日期为主视觉，选股策略降为辅助信息；标记按钮使用短标签“候选/观察/放弃/无效”，说明和实际成交边界保留在辅助文案中；Badge 基础样式显式使用 `inline-flex`，避免徽章按块级元素拉伸。
- 完成安全策略、可复现回测黄金夹具、API 健康检查、前端代理和显式 live-data 验证脚本；新增通达信导入、EBK/行情表市场映射、公式目录、人工标签冻结校验、固定持有期结果、结果政策溯源、批次成熟 CLI/Web 操作、成熟任务账本与显式调度 CLI、批准全市场快照自动成熟队列、手工候选批次、带 schema 版本的 JSONL 数据集导出、按日期切分数据集 CLI、管理统计 API/前端、Tushare 日线/股票列表/交易日历/复权因子适配与同步、BaoStock + AKShare 备用交易日历交叉校验、交易日历增量缓存、单交易日全市场增量同步、多交易日增量编排、历史行情补齐、历史批量复权因子补齐、显式前复权特征输入、特征契约、离线基线训练、可选 LightGBM 候选训练、带客观结果证据的走步验证、Isotonic 概率校准、baseline/LightGBM 模型注册与影子推理、只读模型证据 API/前端、历史上市/退市日期过滤、推荐证据工作台、模型漂移报告、证券主数据历史快照、持仓订单/成交/复盘录入台账、时间点股票池、策略生命周期、理论回测报告、股票池搜索和手工持仓 API、条件胜率与历史入场价分析 API/前端；新增 `backfill_tushare_adjustment_factors.py`，只消费已批准全市场日线快照；Tushare 现通过官方 HTTPS Data API，股票池同步限定沪深主板，避免 SDK 默认旧 HTTP 端点的空响应；趋势交易 A2 已固定默认 10 日收益结果政策和沪深主板整手费用规则；前端已按新增 design-system Skill 收敛浅色语义 token、以 A 股涨势红为主色、响应式容器、卡片层级、焦点状态、移动端控件尺寸，以及 alert、badge、dialog 和日期/下拉控件等基础组件；后端测试 216 项通过、1 项环境相关跳过，Ruff/MyPy 通过，前端 typecheck/build/lint 通过。
