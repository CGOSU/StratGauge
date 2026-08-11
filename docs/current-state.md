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

- Tushare `daily` 已用 `000001.SZ` 的 2024-01-02—2024-01-05 区间通过质量门，并与 BaoStock 同区间交叉校验通过；`stock_basic` 已生成 `tushare-reference-278819ba7c8d5c92fe37` 和 `universe-d8b8d8f30a3f50c43fd9`，共 3193 条记录，市场均为主板且交易所仅为 SSE/SZSE。2026-08-10 已真实完成全市场日线和复权因子同步，均为 3193/3193、100% 覆盖并通过批准；日线快照为 `snapshot-844cde11ae0c5b85f937`，因子快照为 `snapshot-b42e9d0261edad6fa089`。BaoStock + AKShare 已交叉验证 2024-01-01—2026-08-11，共 631 个交易日，缓存为 `backend/data/calendars/trade-cal-sse.txt`，manifest 为 `calendar-5dc7698e9739a85ac100`；Tushare `trade_cal` 原生端点的供应商溯源请求仍返回 40203，但不影响该备用缓存使用。
- 已用真实脱敏 `0810.EBK` 和 `081020260810.txt` 样例确认格式：`.EBK` 为 CRLF 七位市场前缀代码；`.txt` 为 GBK 制表符行情表，包含 30 列和 `#数据来源:通达信` 尾行；两份样例的 4 个代码均可解析为 `002167.SZ`、`600593.SH`、`603183.SH`、`603980.SH`，样例文件已加入忽略规则，不进入提交。
- 当前已有 CLI `mature_tdx_selection.py` 和 Web 一次性成熟操作，新增本地成熟任务账本、事件审计、幂等执行、可恢复超时、有限重试和 `run_selection_maturity_tasks.py` 显式调度入口；JSONL 数据集带 schema 版本，并可由 `split_tdx_dataset.py` 按选股日期生成不可覆盖的 train/validation/test 清单和源文件哈希 manifest；第一版研究参数已冻结为 10 个交易日固定退出、无止盈止损、扣费后净收益胜负、100 股整手和默认 10000 元名义金额，后续用户偏好必须创建新政策版本。
- 数据管理第一版已完成：公式和导入批次支持 Web 新建/查看、筛选、编辑为新版本、停用/恢复、批次详情、原始文件下载和 JSONL 数据集下载；统计支持公式版本、来源、选股日期范围、最新人工决策、结果政策和生命周期筛选；研究工作台支持手工逐条添加候选，批次按通达信/手工来源筛选；生命周期采用追加式事件，归档批次只读，普通研究工作台默认隐藏归档数据。公式编辑不会原地覆盖历史记录。
- 机器学习已完成数据准入、历史补齐、特征契约、基线训练、LightGBM 候选训练、带客观结果证据的走步指标、Isotonic 概率校准、基础影子推理和只读模型证据展示：`features/contract.py`/`engine.py` 提供带版本和溯源的收益、动量、波动率、流动性、均线及基准相对强弱特征；`train_selection_baseline.py` 和可选依赖 CLI `train_selection_lightgbm.py` 以 `selection-feature-dataset-v1`、单一追踪政策和相同时间切分训练模型；LightGBM 输出未校准概率、指标、模型文本和不可覆盖 manifest；`walk_forward.py`/`validate_walk_forward.py` 可选读取 `selection-dataset-v2`，按时间清除训练标签并报告结果覆盖率、执行率、平均净收益、最大回撤、换手和市场状态平均收益；`calibrate_selection_probabilities.py` 只用 validation 拟合 PAVA 校准器，test 隔离评估并记录校准时间、适用日期和源哈希；`ModelRegistryStore` 和 `shadow.py` 已支持 baseline/LightGBM 注册、运行时工件校验、可选校准分数和独立评分报告；新增按候选查询模型版本、分数、样本量、适用日期和失败原因的只读 API/前端面板，所有影子结果仍标记不用于决策；当前尚未实现模型批次自动接入。
- 研究基础已增加 Tushare 股票列表/交易日历同步、BaoStock + AKShare 备用交易日历交叉校验、交易日历增量缓存、时间点股票池快照、证券主数据快照、`/v1/universe/securities` 与 `/v1/universe/master` 搜索、内容寻址理论回测报告、策略生命周期审计，以及手工订单/成交/持仓/复盘 API；趋势交易 A1 买入规则、A2 胜负/成本规则、B1/B2 全市场增量行情及多日编排、C1 交易日历缓存、C2/C2.1 复权因子同步与历史批量补齐、显式前复权特征输入、C3 已批准行情自动成熟队列以及条件胜率和买入价分析查询已完成；分析查询严格按公式、追踪政策和批次导出时间点筛选历史证据。

## 最近一次更新

- 完成安全策略、可复现回测黄金夹具、API 健康检查、前端代理和显式 live-data 验证脚本；新增通达信导入、EBK/行情表市场映射、公式目录、人工标签冻结校验、固定持有期结果、结果政策溯源、批次成熟 CLI/Web 操作、成熟任务账本与显式调度 CLI、批准全市场快照自动成熟队列、手工候选批次、带 schema 版本的 JSONL 数据集导出、按日期切分数据集 CLI、管理统计 API/前端、Tushare 日线/股票列表/交易日历/复权因子适配与同步、BaoStock + AKShare 备用交易日历交叉校验、交易日历增量缓存、单交易日全市场增量同步、多交易日增量编排、历史行情补齐、历史批量复权因子补齐、显式前复权特征输入、特征契约、离线基线训练、可选 LightGBM 候选训练、带客观结果证据的走步验证、Isotonic 概率校准、baseline/LightGBM 模型注册与影子推理、只读模型证据 API/前端、历史上市/退市日期过滤、推荐证据工作台、模型漂移报告、证券主数据历史快照、持仓订单/成交/复盘录入台账、时间点股票池、策略生命周期、理论回测报告、股票池搜索和手工持仓 API、条件胜率与历史入场价分析 API/前端；新增 `backfill_tushare_adjustment_factors.py`，只消费已批准全市场日线快照；Tushare 现通过官方 HTTPS Data API，股票池同步限定沪深主板，避免 SDK 默认旧 HTTP 端点的空响应；趋势交易 A2 已固定默认 10 日收益结果政策和沪深主板整手费用规则；前端已按新增 design-system Skill 收敛浅色语义 token、以 A 股涨势红为主色、响应式容器、卡片层级、焦点状态、移动端控件尺寸，以及 alert、badge、dialog 和日期/下拉控件等基础组件；后端测试 216 项通过、1 项环境相关跳过，Ruff/MyPy 通过，前端 typecheck/build/lint 通过。
