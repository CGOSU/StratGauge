# 当前状态

## 项目

- 名称：`StratGauge`
- 定位：是一个把个人选股和买卖策略自动化、用历史数据与机器学习评估胜率和风险，并由用户手动决策交易的 A 股 AI 策略助手

## 当前目标

- 阶段 0 的实现检查点 1—6 已完成，代码退出验收通过；Tushare `daily` 主源验证和与 BaoStock 的固定区间交叉校验已通过；股票池现限定为沪深主板，已生成 3193 条主板 `stock_basic` 快照；交易日历已由 BaoStock + AKShare 交叉校验并缓存，阶段 0 运行验收不再阻塞。Tushare `trade_cal` 原生端点仅剩供应商溯源补验。
- 新增无额外数据平台费用的半手动闭环：通达信导出选股结果，系统人工标注，随后用日线跟踪并自动生成结果、统计和机器学习样本。
- 已确认前端视觉优化需求：恢复相邻操作按钮之间的可见间距，并审查相似操作区在桌面/窄屏下的换行、对齐和横向溢出体验；本轮仅调整前端呈现，不改变 API、数据、任务调度、交易语义、键盘访问或按钮可用性。
- 已实现观察池重复证券排序修正：同一页内的相同证券按首次出现顺序稳定连续分组，组内保留原有记录顺序，不按代码重排，也不改变筛选、分页、交易语义和既有颜色标记。
- 已实现单证券持仓表现：持仓页不展示组合级金额净盈亏走势。用户点击当前持仓后，在既有股票详情二级抽屉的“表现”标签中查看该证券独立的历史净收益率走势、百分比摘要、估值日明细和行情证据；只使用该证券已确认成交、`ManualFill` 已记录的佣金/印花税及本地 approved `market_daily` 快照，按 `as_of` 过滤。缺少可见行情时显示明确证据状态和原因，百分比保持为空，不补零、旧价或猜测值。

## 已知状态

- 产品、架构、策略、数据、回测、机器学习、数据模型和 API 规格已合并为 `docs/系统规格说明.md`，并统一使用中文。
- 已建立 `backend/` Python 工程、Pydantic 领域模型、Tushare/BaoStock/AKShare/歪枣网统一适配器契约、Tushare `stock_basic`/`trade_cal` 参考数据快照、DuckDB 清单索引、内容寻址 JSON/Parquet 快照、质量门、安全策略 AST、下一交易日回测和股票分析 API；另有带 API 健康状态的最小 React/Vite 前端外壳。默认测试不访问网络；真实验证脚本位于 `backend/scripts/verify_live_adapters.py`。
- 已实现通达信半手动选股首个可验证切片：`tdx_import.py` 支持 UTF-8/GB18030 的 CSV、TSV、分号分隔文件、通达信 `.EBK` 市场前缀格式和通达信行情表 `.txt`；批次记录公式、原始文件名和文件哈希；原始导出和解析结果不可变保存；人工标签和客观结果追加保存；`selection_outcomes.py` 支持明确固定持有期、政策指纹和名义金额的客观结果计算；`selection_stats.py` 按候选/追踪政策取最新结果并分离完成、未入场和未解决样本；FastAPI 候选队列、统计、一次性成熟和成熟任务接口，以及 React 导入、标注、统计、成熟任务状态和数据管理控制已接入。
- 歪枣网适配器支持按证券代码获取不复权日线，使用 `WAIZAO_TOKEN` 环境变量；`code=all` 当前 Token 无权限。成交量已从手转换为股，原始响应仍保留供应商字段。成交量和成交额交叉比较允许 0.001% 相对容差以覆盖供应商取整。
- 工作台观察池现返回并展示当前连续 `watch`/`prepare_buy` 周期首条 `reviewed_at` 到最新批准收盘价的累计涨跌幅；仅消费 approved `market_daily` 快照，起点和最新行情均受 `available_at <= as_of` 约束。缺少或时间不可见的证据显示 `unavailable`/`incomplete` 原因，不补零。
- 股票详情抽屉现提供规范化 `CODE.EXCHANGE` 到雪球 `EXCHANGE+CODE` 的日线 K 线外链；例如 `000788.SZ` 映射为 `https://xueqiu.com/S/SZ000788`。该链接只读、在新标签页打开，不发起应用内行情请求。
- 工作台从信号行打开股票详情时会保留被点击的 `candidate_id`；同一证券出现在多个批次时，人工评审和交易写入对应的批次候选，不再固定操作详情中的第一条信号。
- 观察池同一页出现重复证券时，重复行左侧显示相同颜色的粗边框标记；只对当前观察池页面内重复的标准化证券代码标记，不改变筛选、排序或交易语义。
- 当前持仓详情抽屉的“表现”标签按需调用 `GET /v1/workbench/stocks/{symbol}/performance`；无活动持仓不提供该入口。面板用原生 SVG 绘制累计净收益率，并以语义摘要、估值日表格和 approved 行情快照/缺失原因作为并列证据。
- 手工台账、手工持仓跟踪和手工研究候选保持三条边界：新手工确认买入自动进入 `manual-position-tracking-v1` 跟踪案例，历史持仓必须显式登记且不倒填；手工跟踪 evidence 通过独立 `manual-position-tracking-dataset-v1` 导出，手工研究候选仍按 `SelectionBatch.source` 保留 `manual` 来源。
- 手工 tracking evidence 导出只读取跟踪开始后的案例关联订单/成交/复盘和 evaluated_at 前已批准的本地行情，报告记录排除、覆盖和资格缺口；它不进入既有四用途训练、注册、shadow、推荐或交易链。报告 `ready` 仅表示证据覆盖完整，不表示已获训练或模型发布授权。

## 待处理

- 2026-09-01 已新增显式 `prepare_selection_ml_market_evidence.py`：按批次/选股日期范围扩展交易日历，分阶段补齐全市场 `market_daily`、同源复权因子和独立 `000300.SH` `index_daily`，输出结构化报告；仅离线 fake-provider 验证，未执行真实 provider 或写入 `backend/data`。
- 该补齐服务会拒绝 universe snapshot `as_of` 晚于任一目标交易日，质量拒绝和 provider 失败保留在阶段报告中；补抓行情的 `available_at` 不代表历史可见性，特征快照、join、训练、注册和 shadow 仍需后续人工显式执行。
- 2026-08-22 ML 四用途重构已完成代码迁移：中心训练契约、不可变 bundle 写入原语、训练前特征观察 application service、五个训练 application service 和六个研究证据/生命周期 application service 已落地；11 个 ML CLI 仍由人工显式触发，未引入后台训练、自动注册、自动晋升或交易。旧候选成功率链与新用途链保持隔离。
- 2026-08-22 已补齐四用途训练标签到时间点特征快照的显式 CLI，并用临时目录、真实文件和 fake LightGBM 完成四用途离线全链路演练；演练覆盖 prepare、特征快照、join、baseline/LightGBM、validation-only calibration、注册、人工 shadow transition、shadow、walk-forward 和 compare。真实 provider、真实训练和当前生产数据 readiness 仍未执行或达标。
- 第一轮 clean-code 整理已完成行为保持型收敛：后端 `uv run mypy` 现在覆盖 `start.py`、`src`、`tests` 和 `scripts`；ML 规范 JSON/SHA-256 与原子文本写入已分别抽为共享私有工具；前端请求客户端、日历输入和主板行情同步面板已拆出，无消费者的 API Context/hook、`decisionDescription` 和成熟任务前端遗留 CSS 已删除。后端公开 API/CLI、数据源适配器和历史兼容路径未因“看起来旧”而删除。
- 尚未进行大规模职责拆分：`frontend/src/App.tsx` 的工作台/数据管理/持仓页面、`backend/src/stratgauge/api/selections.py` 的综合服务以及 ML 训练大模块仍保留，后续应按领域边界继续小步提取。主板行情同步视觉回归用例已经存在（4 个场景）；本轮已确认排队状态操作区的间距回归通过，但完整套件仍有 2 个既有 completed/partial 结果截图差异未处理；同步阶段进度轮询/取消操作和未成交/部分成交独立前端入口仍未实现。`git push` 仍因 `master` 未配置远程仓库而不可执行。
- 已实现 Tushare `daily`、股票池、交易日历和复权因子适配，以及仅面向 `000300.SH` 的独立 `index_daily` 请求与同步 CLI；本轮只用离线替身验证接口、ingest/快照写入和 readiness 过滤，未执行真实行情请求。现有本地交易日历只覆盖至 2026-08-11；Tushare `trade_cal` 原生端点的供应商溯源请求仍受既有限额约束，BaoStock + AKShare 的备用交易日历缓存需要在使用更晚行情前显式扩展。
- 已用真实脱敏 `0810.EBK` 和 `081020260810.txt` 样例确认格式：`.EBK` 为 CRLF 七位市场前缀代码；`.txt` 为 GBK 制表符行情表，包含 30 列和 `#数据来源:通达信` 尾行；两份样例的 4 个代码均可解析为 `002167.SZ`、`600593.SH`、`603183.SH`、`603980.SH`，样例文件已加入忽略规则，不进入提交。
- 当前已有 CLI `mature_tdx_selection.py` 和 Web 一次性成熟操作，新增本地成熟任务账本、事件审计、幂等执行、可恢复超时、有限重试和 `run_selection_maturity_tasks.py` 显式调度入口；JSONL 数据集带 schema 版本，并可由 `split_tdx_dataset.py` 按选股日期生成不可覆盖的 train/validation/test 清单和源文件哈希 manifest；第一版研究参数已冻结为 10 个交易日固定退出、无止盈损、扣费后净收益胜负、100 股整手和默认 10000 元名义金额，后续用户偏好必须创建新政策版本。主板行情同步另有 `LocalJobStore` 耐久任务 API/显式 runner，只有 queued 可取消，FastAPI 不启动后台线程；任务级 Tushare transport 在任务结束后关闭。
- 数据管理第一版已完成：公式和导入批次支持 Web 新建/查看、筛选、编辑为新版本、停用/恢复、批次详情、原始文件下载和 JSONL 数据集下载；统计支持公式版本、来源、选股日期范围、最新人工决策、结果政策和生命周期筛选；研究工作台支持手工逐条添加候选，批次按通达信/手工来源筛选；生命周期采用追加式事件，归档批次只读，普通研究工作台默认隐藏归档数据。公式编辑不会原地覆盖历史记录。高基数批次、观察池、工作台信号和观察列表已有兼容分页入口，JSONL 下载按记录流式输出，旧完整列表和下载入口保留；本轮分页优化尚未建立运行中生产服务的延迟基线。
- 机器学习相关代码已实现数据准入、历史补齐、特征契约、离线基线/LightGBM 训练入口、带客观结果证据的走步指标、Isotonic 概率校准、基础影子推理和只读模型证据展示；新增四用途数据集构建器、训练标签契约、时间切分/未来标签 purged 分区、时间点特征快照构建和不可变准备 CLI；离线演练已覆盖临时目录中的特征生成、训练、注册和 shadow，但真实数据链仍需授权，模型也尚未自动接入新批次：`features/contract.py`/`engine.py` 提供带版本和溯源的收益、动量、波动率、流动性、均线及基准相对强弱特征；`train_selection_baseline.py` 和可选依赖 CLI `train_selection_lightgbm.py` 以 `selection-feature-dataset-v1`、单一追踪政策和相同时间切分训练模型；LightGBM 输出未校准概率、指标、模型文本和不可覆盖 manifest；`walk_forward.py`/`validate_walk_forward.py` 可选读取 `selection-dataset-v2`，按时间清除训练标签并报告结果覆盖率、执行率、平均净收益、最大回撤、换手和市场状态平均收益；`calibrate_selection_probabilities.py` 只用 validation 拟合 PAVA 校准器，test 隔离评估并记录校准时间、适用日期和源哈希；`ModelRegistryStore` 和 `shadow.py` 已支持 baseline/LightGBM 注册、运行时工件校验、可选校准分数和独立评分报告；新增按候选查询模型版本、分数、样本量、适用日期和失败原因的只读 API/前端面板，所有影子结果仍标记不用于决策。
- 2026-08-23 本地数据根实际盘点为 12 个导入批次、33 个候选和 4 个客观结果；4 个结果均为 `unresolved`，且采用旧 5-session policy。冻结的 10 个交易日政策尚无完成 `win/loss/flat` 样本；训练标签、特征快照、独立 `000300.SH` `index_daily` 基准和 ML 训练输出仍未形成可用证据。
- 2026-08-23 `backend/data/selection-market` 含 10 个已批准 `market_daily` 日期（2026-08-10 至 2026-08-21）和 7 个已批准、绑定来源日线的 `adjustment_factor` 快照，但没有 `index_daily` 快照；交易日历文件结束于 2026-08-11，不能为更晚快照自动推断成熟或基准同步交易日。
- 2026-08-20 导入数据页在 ML 前置检查之后新增独立只读成熟结果列表；通过 `GET /v1/selection-maturity/page` 跨全部历史批次展示候选与每个 tracking policy 的最新结果，包含归档批次和已排除候选，支持 pending/win/loss/flat/no_entry/unresolved 六类筛选、全量状态汇总和分页。列表不创建或执行成熟任务，不改变客观结果、训练标签或 shadow 评分边界。
- 研究基础已增加 Tushare 股票列表/交易日历同步、BaoStock + AKShare 备用交易日历交叉校验、交易日历增量缓存、时间点股票池快照、证券主数据快照、`/v1/universe/securities` 与 `/v1/universe/master` 搜索、内容寻址理论回测报告、策略生命周期审计，以及手工订单/成交/持仓/复盘 API；趋势交易 A1 买入规则、A2 胜负/成本规则、B1/B2 全市场增量行情及多日编排、C1 交易日历缓存、C2/C2.1 复权因子同步与历史批量补齐、显式前复权特征输入、C3 已批准行情自动成熟队列以及条件胜率和买入价分析查询已完成；分析查询严格按公式、追踪政策和批次导出时间点筛选历史证据。

## 最近一次更新

- 2026-09-03 观察池重复证券稳定连续分组：在既有整体排序和筛选结果之后，按证券首次出现顺序将相同证券连续显示，组内保留原记录顺序；既有颜色标记、分页、点击和交易语义不变。前端 typecheck、lint、build 通过，定向 Playwright 4 项通过；未执行 commit/push。

- 2026-09-03 观察池重复证券标记：同一页内相同证券的表格行使用相同颜色的 4px 左侧边框，唯一证券不添加标记；新增定向 Playwright 断言验证分组颜色、边框宽度和唯一行无标记。前端 typecheck、build 通过，lint 0 error、19 条既有 warning；未改变 API、数据或交易写入入口，未执行 commit/push。

- 2026-09-03 完成单证券持仓历史净收益率表现：移除组合级金额表现 API/UI，新增只读 `GET /v1/workbench/stocks/{symbol}/performance`；持仓点击后在既有 `StockDetailDrawer` 的“表现”标签按需加载，展示百分比走势、摘要、估值日证据和缺失行情原因，无活动持仓不显示入口。前端 typecheck、lint、build 通过；定向 Playwright 4 项通过；lint 保留 19 条既有 warning；后端完整测试 438 项通过、1 项跳过，Ruff 和 MyPy 通过；`git diff --check` 通过；未写入 `backend/data`、未访问真实 provider、未执行 commit/push。

- 2026-09-01 完成手工持仓 tracking ML evidence：新增独立、不可覆盖的 `manual-position-tracking-dataset-v1` JSONL 导出和 `manual-position-tracking-report-v1` 覆盖/资格报告；保留手工/通达信候选来源，排除跟踪开始前事实及候选/策略/推荐关联事实。后端完整测试 434 项通过、1 项跳过；Ruff check、format check 和 MyPy 通过；CLI `--help` 通过；未访问真实 provider、未写入 `backend/data`、未执行 commit/push。下一步仍需由操作者按既有四用途链人工启动训练，手工 evidence 不会自动进入该链。

- 2026-09-01 新增 ML 市场证据补齐编排：`prepare_selection_ml_market_evidence.py` 支持显式批次范围、交易日历扩展、全市场日线/复权因子/独立 `000300.SH` `index_daily` 同步及结构化安全阻断。离线 fake-provider 测试覆盖批准复用、质量/供应商失败、股票池时间点阻断和不触发后续研究；真实 provider、特征快照、训练和模型链未执行。

- 2026-09-01 将旧版 `ManualSelectionLabel` 的冻结截止从选股日期次日 00:00 调整为次日 02:00（严格早于），截止时间沿用 `exported_at` 时区；API、测试和相关文档已同步。

- 2026-08-31 将前端“今日信号”更正为“策略信号”：保留 `/v1/workbench/daily-signals` 的历史/当前可用批次查询语义，在页面显示当前加载范围内的最新行情收盘日，并在每行显示实际 `market.session_date`；运行中本地数据核对为 87 条信号、最新选股日和行情日均为 2026-08-28，2026-08-31 没有选股信号。

- 2026-08-27 完成观察池表现和雪球日线入口：后端按当前页候选批量读取 approved `market_daily` 证据并计算连续观察周期表现；前端展示起始时间/价格/涨跌幅、缺失原因和窄屏表格内滚动，详情抽屉新增安全雪球外链。后端 410 项测试通过、1 项跳过；Ruff check、format check、MyPy 通过；前端 typecheck/build 通过，lint 0 error 但保留 20 条既有 warning；观察池 Playwright 1 项通过。完整前端视觉回归另有 3 个未修改主板行情同步截图基线差异。未执行 commit/push。

- 2026-08-22 完成四用途人工操作路径收口：新增使用文档中的 prepare → 时间点特征快照 → join → baseline/LightGBM → calibration → register → transition → shadow → walk-forward → compare 操作图、输入/输出血缘、证据目录和无标签 shadow 输入哈希要求；README、系统规格、current-state、decisions、session-log 和 pitfalls 已同步当前事实。未执行 commit/push。

- 2026-08-22 完成四用途离线全链路演练：使用临时目录、真实文件和 fake LightGBM 覆盖四用途 prepare、特征快照、join、baseline/LightGBM，以及候选成功率二分类和人工评审三分类的校准、注册、人工 lifecycle、shadow、walk-forward 和 comparison；断言 final-test 隔离、不可变工件和 `used_for_decision=false`。后端完整测试 408 项通过、1 项跳过，Ruff check、format check 和 MyPy 通过；未执行真实 provider、commit/push。

- 2026-08-22 完成四用途 ML application workflow 迁移：训练、校准、注册、生命周期、shadow、走步和比较 CLI 现在通过 `backend/src/stratgauge/application/ml/` 的显式 request/result 服务执行；保留 CLI 参数、退出语义、工件路径、SHA-256、manifest、审计证据、`final-test` 隔离和 `used_for_decision=false`。新增 application 边界回归；六个 evidence CLI `--help` 通过。后端完整测试 400 项通过、1 项跳过，Ruff check、format check 和 MyPy 通过；未执行 commit/push。

- 2026-08-22 完成本轮 Web 列表读取优化：后端工作台、成熟结果和数据管理分页先筛选/分页再 hydration，批量读取 DuckDB 投影并保留事实文件扫描回退；前端按导入路由加载导入数据，工作台搜索防抖并取消过期请求，统计/成熟列表按需加载，旧手工台账展开后挂载。公开路由、字段、排序筛选、全范围成熟 `status_counts` 和旧完整列表入口保持不变。未引入新基础设施，未执行 commit/push。

- 2026-08-22 前端验证：typecheck、build、lint 均通过；成熟结果视觉回归 3 项通过，持仓/交易视觉回归 2 项通过。完整视觉回归 6 项通过、3 项主板行情同步截图比较失败；未修改既有 `frontend/src/styles.css`、详情抽屉或快照以掩盖该工作区既有差异。

- 2026-08-22 新增独立顺序 GET 接口性能巡检：`backend/scripts/benchmark_get_endpoints.py` 从运行中服务的 `/openapi.json` 发现 GET 路由，严格校验版本化样例清单，逐接口预热后顺序请求并输出状态、响应字节数、中位/最大耗时；配置错误返回 2，HTTP、网络或性能阈值失败返回 1。`backend/scripts/benchmark_get_cases.json` 已覆盖当前 43 个 GET 路由，其中 28 个空数据根可安全运行，15 个动态、时间点或流式接口写明跳过原因。

- 2026-08-22 为巡检增加隔离 `create_app()` 契约回归：不访问网络或真实数据根，验证样例与 OpenAPI 双向一致，并确认全部可运行样例返回 200。后端完整测试 384 项通过、1 项跳过；Ruff、格式检查和 MyPy 通过。没有把性能阈值接入 CI，也没有在本轮连接运行中的真实服务记录延迟数值。

- 2026-08-20 完成本轮数据边界收口：导入数据页提供跨历史批次的只读成熟结果列表；新增仅面向 `000300.SH` 的独立 `index_daily` 基准证据同步 CLI，并将 readiness 限定为已批准、日期匹配的独立快照；将 walk-forward 客观结果索引和收益/回撤/换手计算提取到 `walk_forward_outcomes.py`，保持公共 API、报告身份、哈希和错误语义不变。未执行真实 Tushare 请求、成熟、特征生成、训练、模型注册或 shadow；未执行 commit/push。

- 本轮实际验证：后端完整测试 371 项通过、1 项跳过；Ruff check、Ruff format check、MyPy 通过；walk-forward 定向测试 7 项通过；git diff --check 通过（仅 Windows 换行转换提示）。

- 2026-08-20 完成导入数据页只读成熟结果列表：新增跨全部历史批次（含归档和已排除候选）的六状态筛选、全量状态汇总、最新 tracking policy outcome 证据和分页；pending 明确表示没有客观结果，no_entry/unresolved 不进入训练标签。新增前端类型、列表卡片和窄屏无溢出视觉覆盖；同步补充既有视觉测试的只读 API mock 与受布局影响的快照。前端 typecheck 通过，lint 为 0 error/24 warnings，build 通过，全部 9 项 Playwright 视觉回归通过；未执行 commit/push。
- 2026-08-16 继续前端卫生清理：删除成熟任务操作区移除后遗留的专用 CSS 与响应式规则，保留导入/对话框共享布局；前端 typecheck、lint、build 和 7 项 Playwright 视觉回归通过。随后调整导入数据页布局：导入/批次/候选成为主流程，机器学习前置检查与行情同步下移为运行状态区；桌面双列均衡，中窄屏单列且无横向溢出，8 项 Playwright 视觉回归通过。修正机器学习前置检查卡片缺少内边距的问题，视觉回归仍通过。未修改当前未提交的后端路由拆分改动。
- 2026-08-16 前端引入真实 ESLint 并清理死代码：新增 `frontend/eslint.config.js`（ESLint 10 + typescript-eslint + react-hooks），`lint` 脚本不再与 `typecheck` 重复；`typescript` 降为 `~6.0.3`（TS 7 被 typescript-eslint 硬性拦截，6.x 为同语言版本的 JS API 并行线）。按 lint 结果删除 2026-08-13 起无调用点的成熟任务前端死代码约 470 行（`App.tsx` 4557 → 4078 行）及未使用导入/函数；`set-state-in-effect` 降为有注释的 warn 基线。仓库卫生：`.gitignore` 补 `frontend/test-results/` 与 `.agents/`，删除根目录遗留空目录 `nonexistent-task-test/`，修复本文件一处 `+- ` diff 残留；`AGENTS.md` 常用命令已补齐。前端 lint/typecheck/build 与 `git diff --check` 通过；后端未改动。
- 2026-08-15 完成 IO 优化回归与文档收口：SnapshotStore catalog 快速路径在命中时不读取 manifest 文件；SelectionReadIndex 批量读取不触发全量扫描；分页契约统一为 `page/page_size/total/has_next` 且上限 200；JSONL 下载使用 `StreamingResponse`；主板同步任务 API/UI 使用 LocalJobStore、显式 runner 和 queued-only 取消，任务状态跨应用重启保持；Tushare transport 在单任务生命周期内复用并关闭。离线后端测试 359 项通过、1 项跳过，Ruff/MyPy 通过；前端 typecheck/lint/build 通过。真实 Tushare 请求未执行，未提交或推送。
- 2026-08-14 完成第一轮 clean-code 行为保持型整理：收敛 18 个 ML 模块的规范 JSON/字节哈希重复实现，抽取跨 8 个存储/应用模块的 UTF-8/LF 原子文本写入，修复 Ruff/MyPy 基线债务；前端统一 API 客户端错误与 URL 解析，抽出 `CalendarInput` 和主板行情同步面板，删除无消费者 provider/hook 及无调用 helper。后端 339 项测试通过、1 项跳过，Ruff/MyPy 通过；前端 typecheck/lint/build 通过。未提交本轮整理，未纳入 `.agents/`。
- 2026-08-14 合并持仓/交易记录中旧手工台账的订单和成交录入：单个表单通过原子确认交易接口同时写入订单与成交，自动生成订单 ID 和成交 ID；卖出增加必填卖出原因，复盘和只记录未成交订单的后端 API 保持兼容。
- 2026-08-14 完成主板收盘行情同步诊断 API/UI：API 为每个日线和复权因子阶段返回中文状态、覆盖率、预期/实际证券数、质量问题、处理建议、快照 ID 和 `quality_run_id`，质量拒绝仍保留为 HTTP 200 的不可用快照并通过 `research_ready=false` 标记；供应商、存储、前置条件和未知异常返回稳定 `code` 及不泄露凭据的中文原因/建议。导入数据页同步面板区分完整成功与含未批准数据的完成状态，并明确未批准数据不会进入研究；桌面双列和窄屏单列布局支持长证据 ID 换行。后端 API 测试 47 项通过，前端 typecheck/lint/build 通过。未跟踪 `.agents/` 未纳入本轮改动。
- 2026-08-13 完成桌面研究工作台 UI-01/UI-02：前端新增固定左导航、顶部运行状态栏、中央工作区和右侧 Stock Detail Drawer；工作台、今日信号、观察池、持仓、交易记录、策略和分析页面接入 `/v1/workbench/*` 只读投影。详情抽屉提供行情/判断/信号/交易/分析五个 Tab，快速判断仅追加 `SelectionAnnotation`，支持评分、观点、操作意图、固定标签和备注；保留现有手工台账录入入口。前端 typecheck、build、lint 和 `git diff --check` 通过。统一 Drawer 下的用户确认 BUY/SELL、默认工作台连续处理入口和观察池重新判断变化摘要已接入；新增左侧“导入数据”路由，恢复通达信导入表单和批次列表；导入数据页的候选区现为只读，旧标签仅展示兼容历史，新的 Annotation 判断和交易记录统一从工作台股票详情进入；前端已移除批次成熟操作区，客观结果和历史成熟数据仍保留为只读证据；导入数据页新增“同步收盘行情”，后端只接受缓存交易日并校验沪深主板股票池，写入 Tushare 日线和同源复权因子批准快照；后续主要进行全量回归与视觉验收。
- 2026-08-13 完成前端视觉收敛：统一浅色金融研究台 token、中文系统字体、排版层级、卡片/表格/Drawer 和 A 股红涨绿跌、独立错误、蓝色信息、琥珀待处理语义；补充导航无障碍属性、数字样式 hook 和 confirmed KPI 状态。typecheck、lint、build、`git diff --check` 及 CSS 变量/权重扫描通过；Chrome headless 在 390/1024/1440 视口确认无页面级横向溢出。无后端 API 时的 JSON 错误提示属于既有数据服务不可用状态。随后完成布局滚动修复：应用壳固定为 `100dvh`，主内容独立纵向滚动，顶部状态栏固定 60px（移动端 72px），Drawer layer 从状态栏底部开始。
- 2026-08-11 新增仓库根目录统一启动入口：`uv run --project backend python start.py`，Windows 可双击 `start.cmd`。启动器并发运行后端 Uvicorn（固定使用 `--app-dir src`）和前端 `corepack pnpm dev`，带服务日志前缀，并在服务失败、Ctrl+C 或窗口终止时清理进程树；Windows 真实健康检查确认后端 `/healthz` 和前端首页返回 200，退出后 8000/5173 端口均释放。启动器不自动安装依赖或打开浏览器。后端启动器测试 5 项通过，Ruff/MyPy 通过。
- 2026-08-11 完成可复用人工评审标签切片：固定四分类标签支持在 Web 评审对话框中按分类展示、创建并立即选中；数据管理支持改名、归档和恢复；Annotation 保存不可变历史快照，归档/改名不影响历史显示。可复用标签保持在 `selection-annotation-dataset-v1`、特征、训练、注册、shadow、推荐和交易边界之外。前端 typecheck/build/lint 通过；后端完整测试 306 项通过、1 项跳过；后端源 MyPy 和 Ruff check 通过。完整 Ruff format 检查仍发现既有 9 个未格式化文件，未在本切片中改动。
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
