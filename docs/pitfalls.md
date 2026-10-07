# 开发踩坑记录

本文件记录容易复发且不直观的问题。一次性报错、普通开发流水和未经验证的猜测不写入此处。

## 记录格式

每条记录包含：

- 日期：发现或最后验证日期；
- 现象：可观察到的错误或异常行为；
- 根因：经过确认的直接原因；
- 修复：已验证有效的处理方法；
- 验证：复现、测试或检查命令及结果。

## 已知问题

### 2026-09-14：Windows 保留 TCP 端口导致统一启动器后端 EACCES

- 现象：运行 `uv run --project backend python start.py` 时，Uvicorn 在默认 `127.0.0.1:8000` 启动失败并返回 `WinError 10013`；前端可能已先进入 ready 状态。
- 根因：Windows 的 TCP 排除端口范围包含 `7991-8090`，其中包括 8000；没有进程监听 8000，因此不是普通端口占用。具体创建该保留段的系统组件未由本次检查确认。
- 修复：启动器先以独占监听方式探测 8000，不可用时回退到 8091；将实际端口写入 `STRATGAUGE_BACKEND_PORT`，让后端启动参数和 Vite API 代理保持一致。显式设置该变量可覆盖自动选择。
- 验证：Windows 实际启动中后端 `/healthz` 和前端 `http://localhost:5173/` 均返回 200，退出后 8091/5173 无监听残留。

### 2026-09-13：跨策略同日重复信号不能按 candidate 独立显示评审状态

- 现象：不同策略在同一选股日期命中同一股票时，只给其中一条候选追加人工评审，会导致策略信号列表的另一条仍显示“未判断”，而观察池按已评审候选显示为已判断。
- 根因：候选 ID 由批次和证券组成，不同策略天然不同；若工作台只按 candidate ID 读取 annotation，就把同一交易日同一证券的重复命中错误拆成多个评审对象。
- 修复：工作台读取投影按 `(标准化证券代码, selection_date)` 聚合可见 annotation，使用评审组最新状态和观察起点；组内按 `(recorded_at, annotation_id)` 最大值裁决跨候选冲突。`annotations/latest_annotation` 保持候选自身历史，`review_annotations/latest_review_annotation` 单独承载评审组历史和最新值，并以 `review_group_key`、`review_source_candidate_id` 明确组标识及最新来源；观察池复评摘要按评审组去重，避免把其他候选 annotation 放入当前候选字段。持久化仍写入被点击候选，候选级交易/排除/客观结果不合并，不同选股日期不合并。
- 验证：`tests/application/test_workbench.py` 新增同日跨策略回归；应用层 14 项、API 5 项通过，相关 Ruff check 和 MyPy 检查通过。

### 2026-09-04：证券交易日必须按上海时区解释 `as_of`

- 现象：直接使用带时区时间的 `as_of.date()` 作为 `DailyBar.session_date` 上界时，接近 UTC 日界的查询可能漏掉上海当天的行情，或把上海下一交易日错误地纳入历史视图。
- 根因：`DailyBar.session_date` 表示上海证券交易日，而 `as_of` 的日历日期取决于其自身时区。例如 `2026-01-12T18:00:00Z` 已经是上海时间 2026-01-13 凌晨，不能与上海交易日直接按 UTC 日期比较。
- 修复：工作台行情读取先将 `as_of` 转为 `Asia/Shanghai` 再取日期；观察表现的可见行情同时继续校验 `available_at <= as_of`。测试夹具也按上海日期设置未来行情边界。
- 验证：工作台观察表现回归覆盖 `as_of` 之后的行情不参与；后端完整测试 439 项通过、1 项跳过，Ruff 和 MyPy 通过。


### 2026-09-03：单证券表现缺行情时不能用零值维持曲线

- 现象：表现 API 某个估值日缺少仍持仓证券的可见 approved `market_daily` 时，前端若用 `0`、前一收盘价或空点补齐，会把证据缺口误显示为收益率变化。
- 根因：单证券历史净收益率依赖该证券、该估值日且满足 `available_at <= as_of` 的行情；缺失行情不是零收益，且不同证券不能合并为组合曲线。
- 修复：后端返回 `incomplete`/`unavailable`、明确原因和空百分比；前端表现标签保留状态与估值日证据，SVG 只连接连续可用点，表格显示缺失原因。表现请求仅从当前活动持仓的详情抽屉按需发起。
- 验证：`frontend/tests/visual/workbench-manual-position.spec.ts` 覆盖无持仓时无“表现”入口，以及缺失行情状态、原因、百分比和估值明细；定向 Playwright 4 项通过。

### 2026-09-01：手工 tracking evidence 完整不等于训练就绪

- 现象：手工跟踪案例已有订单、成交、复盘和批准行情，就把导出的 `ready=true` 当作现有四用途训练链的准入，或把历史纳入前订单/成交倒填到跟踪开始时间之前。
- 根因：手工台账、跟踪案例和手工研究候选是不同事实；`manual-position-tracking-dataset-v1` 只证明案例关联 evidence 的覆盖，不定义监督标签、特征契约或模型发布准入。历史登记前事实只能作为审计来源，不能伪装成跟踪期观察。
- 修复：导出时按 `tracking_started_at`、事实事件时间和 `evaluated_at` 过滤，只读取 approved 本地 `market_daily`；候选/策略/推荐关联事实和歧义复盘排除并保留原因。将手工 evidence 与 selection ML 四用途、注册、shadow、推荐和交易链隔离；未来若要训练必须另行冻结标签/特征契约，并由操作者显式启动。
- 验证：`tests/backtest/test_manual_position_tracking_dataset.py` 和 `tests/application/test_manual_position_tracking_dataset_cli.py` 覆盖时间边界、缺失行情、资格报告、不可覆盖输出和非零退出码；本轮 434 项后端测试通过、1 项跳过，未执行真实 provider 或训练。

### 2026-09-01：市场证据补齐成功不等于特征可用

- 现象：`prepare_selection_ml_market_evidence.py` 已为历史 session 抓到并批准行情，便直接把这些行情交给训练特征，或把 CLI 退出码 `0` 当成模型训练就绪。
- 根因：补齐快照的 `available_at` 是本次实际抓取时间，通常晚于历史选股/评审时间；市场证据准备只负责日历、行情、因子和独立基准，不验证每个标签观察时点的历史可见性。
- 修复：先阅读结构化报告中的各阶段状态、质量门、缺失和时间点阻断；仅在标签成熟且 `available_at <= as_of` 等证据检查通过后，显式调用 `build_selection_training_feature_snapshots.py`。该 CLI 的 `feature_visibility_guaranteed` 不作承诺，`ready=false` 不得继续训练。
- 验证：离线 fake-provider 测试覆盖阶段批准复用、质量拒绝、provider 失败和 universe `as_of` 阻断；本轮未执行真实 provider 或特征/训练流程。

### 2026-09-01：02:00 冻结截止不是通用的未来数据截点

- 现象：把标签冻结时间误认为所有行情/特征都可见的时间，或用日期而不是发布时间过滤夜间数据，可能把开盘前后信息混入历史判断。
- 根因：02:00 只是旧版人工标签的业务锁定点；未来数据防泄漏仍必须按每条证据的 `available_at`/发布时间和实际决策时间过滤。
- 修复：旧版标签严格接受导出时间至选股日期次日 02:00:00（不含），使用 `exported_at` 时区；特征和回测继续使用各自的时间点约束。
- 验证：`tests/data_pipeline/test_selection_store.py` 覆盖 01:59:59 接受和 02:00:00 拒绝；`tests/api/test_selections.py` 覆盖 API 在 02:00:00 返回结构化拒绝。

### 2026-08-30：同一证券多个候选时详情抽屉不能丢失候选上下文

- 现象：`002673.SZ`（西部证券）同时存在两个选股批次；一个候选已评审、另一个仍未评审。工作台按证券打开详情却固定取 `signals[0]`，点击未评审行可能重复写入已评审候选，列表继续显示未判断。
- 根因：`SelectionAnnotation` 绑定包含批次的 `candidate_id`，而前端打开详情只传证券代码，没有传递被点击的候选。
- 修复：信号行打开详情时同时保存 `candidate_id`，详情抽屉按该 ID 选择评审/候选交易对象；持仓和交易入口没有候选上下文时仍回退第一条信号。
- 验证：`frontend/tests/visual/workbench-annotation-target.spec.ts` 验证详情返回顺序与被点击行相反时仍显示未判断，并把 POST 写入 `candidate-unreviewed`。

### 2026-08-23：批准行情快照不能替代交易日历覆盖

- 现象：本地存在 2026-08-21 的批准 `market_daily` 快照，但 `trade-cal-sse.txt` 只覆盖到 2026-08-11；直接用快照目录推断后续交易日会让成熟队列或 `index_daily` 同步失去显式日期边界。
- 根因：行情快照和交易日历是两类独立证据；`queue_selection_maturity_tasks.py` 与 `sync_tushare_index_daily.py` 读取调用方提供的交易日历，不从行情目录或自然日猜测会话。
- 修复边界：在扩展交易日历并完成来源交叉校验前，不排队更晚成熟任务、不把更晚行情用于基准证据；扩展动作必须保留新的日历 manifest 和来源快照。
- 验证：`backend/data/selection-market/manifests` 盘点得到 10 个已批准 `market_daily` 日期至 2026-08-21；`backend/data/calendars/trade-cal-sse.txt` 末尾为 2026-08-11；当前没有 `request_kind=index_daily` manifest。

### 2026-08-22：四用途特征快照不能按目录或批次日期猜来源

- 现象：直接把训练标签的选股日期当作特征可见时间，或扫描 feature/market 目录选择“看起来匹配”的快照，会让 annotation/buy/sell 丢失 `observation_at` 边界，也可能混入事后抓取数据。
- 根因：四用途标签的观察日、评审时点和行情 snapshot 的 `available_at` 是不同证据；candidate_success 还需要从不可变选择批次取得 `exported_at`。
- 修复：先由 `build_selection_training_feature_snapshots.py` 显式传入已批准 snapshot ID 和 `--as-of`，使用 `observation_at`（candidate_success 缺省时使用批次 `exported_at`），再把报告映射中的 FeatureSnapshot ID 显式交给 join；不存在或不可见行情逐样本失败，不填充特征。
- 验证：`tests.application.test_ml_feature_observations` 覆盖时间点/血缘/逐样本失败；`tests.application.test_ml_offline_rehearsal` 离线演练验证四用途映射、SHA-256 和完整文件链路均通过。

### 2026-08-22：shadow 输入哈希必须覆盖实际 canonical JSONL 字节

- 现象：shadow CLI 的 `--source-input-sha256` 使用了原始对象、另一份格式化 JSON 或缺少换行的文件哈希，评分会被拒绝或输入血缘无法复现。
- 根因：shadow 报告绑定的是调用方提交的无标签 JSONL 内容，不是训练 split 或一个抽象的候选列表。
- 修复：先固定 canonical JSON 序列化、UTF-8 编码和 LF 换行，再对最终提交给 `--input` 的完整字节计算 SHA-256；输入不得携带结果标签或监督字段，并且必须先人工 transition 到 `shadow`。
- 验证：`tests.ml.test_training_feature_shadow` 和 `tests.application.test_ml_offline_rehearsal` 覆盖输入哈希、标签拒绝、transition 前拒绝及 `used_for_decision=false`。

### 2026-08-09：可选 provider 测试不能假定依赖缺失

- 现象：安装 `live-data` 后，原本验证“未安装时抛错”的测试失败，并可能误触发真实网络请求。
- 根因：测试把可选依赖缺失当成固定环境事实。
- 修复：测试先检查 `importlib.util.find_spec`；provider 已安装时跳过缺失依赖分支。默认 `uv sync --no-extra live-data` 仍覆盖未安装行为。
- 验证：默认环境后端 44 项测试通过；安装可选依赖时该测试安全跳过。

### 2026-08-09：最低佣金不能按单股价格直接扣除

- 现象：固定持有期结果使用默认最低佣金时，收益率可能被夸大扣减。
- 根因：最低佣金是按一笔交易金额计价，领域结果只有价格而没有名义金额；按每股价格直接相减会把货币金额和每股收益混算。
- 修复：结果政策要求显式 `position_notional`；缺失时拒绝有最低佣金的完整结果，已知名义金额后按交易数量换算费用。
- 验证：`test_minimum_commission_uses_explicit_position_notional` 和 `test_minimum_commission_without_notional_is_rejected_for_completed_outcome`。

### 2026-08-09：React StrictMode 下的一次性请求守卫不能配合 AbortController

- 现象：开发模式首次 effect 清理后，健康状态可能永久停留在“检测中”。
- 根因：StrictMode 会执行“挂载—清理—重新挂载”；第一次 effect 将 ref 标记为已启动并中止请求，第二次 effect 被守卫跳过。
- 修复：健康检查不使用一次性 ref 守卫，依赖 effect 自带的 AbortController；批次详情请求在清理后不再覆盖新请求的 loading 状态。
- 验证：前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-09：AKShare 默认历史接口受代理影响

- 现象：`verify_live_adapters.py` 的 AKShare 请求访问 `push2his.eastmoney.com` 时因代理远端断开失败；BaoStock 同一有限区间可成功返回 4 行。
- 根因：当前宿主网络/代理无法连通 AKShare 所用的 Eastmoney 接口，非质量门静默降级。
- 修复：保留带上下文的 `AdapterError`，验证脚本返回结构化错误和非零退出码；不以 BaoStock 结果替代 AKShare 主源完成双源验证。
- 验证：`PYTHONPATH=src uv run python scripts/verify_live_adapters.py --symbol 000001.SZ --start-date 2024-01-02 --end-date 2024-01-05` 返回退出码 2；BaoStock 单独抓取并入库后质量状态为 `approved`。

### 2026-08-10：管理列表详情面板位于页面下方

- 现象：数据管理批次的“查看详情”按钮实际改变了选中批次，但详情面板位于列表下方，用户当前视口看不到变化，容易误认为点击无效。
- 根因：选择状态更新没有同步滚动或明确的视口反馈。
- 修复：点击后保留原生按钮语义，设置选中批次并滚动到带 `management-detail-panel` 标识的详情面板。
- 验证：前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10：历史回看窗口不能用自然日填充

- 现象：用 `selection_date - timedelta(days=n)` 直接请求或补齐历史，会把周末和 A 股节假日误报为缺失交易日，甚至产生伪造特征行。
- 根因：现有适配器只负责证券日线，不提供统一交易日历；自然日不是 A 股交易日。
- 修复：`backfill_selection_history.py` 强制接收按升序排列的显式交易日文件，并只把质量门批准的快照 ID 输出给后续特征阶段；缺失日保留在报告中，不填充。
- 验证：`tests/data_pipeline/test_history_backfill.py` 覆盖严格早于选股日、缺失交易日、空证券响应和未批准快照读取。

### 2026-08-10：事后历史补数不能伪装成历史可见数据

- 现象：历史行情虽然交易日早于选股日，但抓取时间晚于选股日，直接拿来计算历史特征会引入修订信息。
- 根因：`normalize_daily_rows` 会把抓取时间写入 `available_at`；交易日早不等于当时已经公开可用。
- 修复：特征引擎同时检查交易日严格早于选股日和 `available_at <= as_of`；不满足则拒绝生成特征快照。
- 验证：`tests/features/test_engine.py` 覆盖未来交易日和晚于 `as_of` 的输入。

### 2026-08-10：常数基线不是可发布模型

- 现象：训练样本只有单一类别或特征质量不足时，不能形成有意义的分类基线。
- 根因：基线的目的只是提供流程和指标对照，不具备排序能力，也没有校准和样本外保证。
- 修复：单一标签类别在训练前直接拒绝；双类别时输出明确的 `constant_probability` 类型、并列排名、覆盖率和 Brier/Log Loss；未实现模型注册、晋升或 Web 权威评分。
- 验证：`tests/ml/test_selection_training.py` 覆盖单类别训练、时间切分和不可覆盖输出。

### 2026-08-10：回测报告不能自动猜测市场状态

- 现象：报告需要按市场状态分组，但回测结果本身不包含牛熊或行业状态标签。
- 根因：根据收益曲线或日期自动推断状态会把报告规则和研究假设混在一起，并可能产生事后分类。
- 修复：`build_backtest_report` 只接受调用方显式的日期到状态映射；未提供的交易标记为 `unspecified`。
- 验证：`tests/backtest/test_report.py` 覆盖显式状态、缺失状态和空状态拒绝。

### 2026-08-10：股票搜索没有时间点快照时必须失败

- 现象：请求历史时间点的股票搜索若没有快照，直接返回当前股票池会泄漏未来成分。
- 根因：股票主数据和历史成分尚未完整入库，当前只能依赖显式 `UniverseSnapshot`。
- 修复：`/v1/universe/securities` 按 `as_of` 解析已知快照；不存在时返回 `UNIVERSE_SNAPSHOT_NOT_FOUND`，不回退到当前或自动猜测。
- 验证：`tests/api/test_universe.py` 覆盖已知时间点搜索和未来快照拒绝。

### 2026-08-10：Tushare 日线单位不是领域单位

- 现象：直接把 Tushare `daily` 的 `vol`/`amount` 写入标准行情，会使成交量少 100 倍、成交额少 1000 倍。
- 根因：Tushare 文档定义成交量为手、成交额为千元，而领域标准层使用股和元。
- 修复：`TushareAdapter` 在 provider 边界把 `vol` 乘 100、`amount` 乘 1000；原始行仍保留供应商原值。
- 验证：`tests/data_sources/test_contract.py` 覆盖单位转换和 `pre_close` 映射。

### 2026-08-10：Tushare 参考数据不能绕过不可变证据

- 现象：`stock_basic` 直接生成当前股票池会把后来的股票列表误用于历史时间点。
- 根因：股票列表和交易日历同样是会变化的参考数据，不能只保存当前内存结果。
- 修复：`TushareReferenceStore` 保存内容寻址原始响应和 manifest；`sync_tushare_universe.py` 使用抓取时点创建 `UniverseSnapshot`，搜索按 `as_of` 解析。
- 验证：`tests/data_sources/test_tushare.py` 覆盖参考快照授权时间点股票池。

### 2026-08-10：走步验证必须显式隔离最终测试期

- 现象：只在报告中声明 `final_test_used=false`，但没有最终测试起点时，折配置可能意外覆盖测试样本。
- 根因：走步折边界和最终测试边界是两个独立契约；训练/验证函数不能从样本日期猜测哪个分区是最终测试。
- 修复：`validate_walk_forward` 接受调用方提供的 `final_test_start`，拒绝折窗口跨越该日期，并把最终测试行只计数、不参与折评估；CLI 要求显式折 JSON。
- 验证：`tests/ml/test_walk_forward.py` 覆盖标签清除、多政策、重复行、单类别和最终测试行隔离。

### 2026-08-10：模型注册不能只保存一个路径

- 现象：只在注册表记录模型输出路径时，原输出被删除或修改后无法证明注册时使用的模型和评估证据。
- 根因：路径不是内容身份，也不能保证训练 manifest 与模型文件匹配。
- 修复：`ModelRegistryStore` 注册时复制模型、评估报告和训练 manifest，记录并读取时校验 SHA-256；生命周期事件追加保存，激活引用评估报告哈希。
- 验证：`tests/ml/test_registry.py` 覆盖不可变注册、工件篡改检测、人工激活、单政策 active 唯一性和回滚。

### 2026-08-10：影子评分不能复用带标签训练行

- 现象：把 `SelectionFeatureRow` 直接作为新批次输入，会把客观结果标签和 `label_evaluated_at` 一并带入影子推理，破坏评分与结果的边界。
- 根因：训练行是成熟后的监督学习证据；新候选只应提供当时可用的 `FeatureSnapshot`，不应要求先有结果标签。
- 修复：`read_shadow_inputs` 拒绝顶层结果标签；影子输入只包含候选 ID 和特征快照，输出单独保存 `uncalibrated_model_score`、输入快照和评分时间。
- 验证：`tests/ml/test_shadow.py` 覆盖标签拒绝、时间不可用、shadow 生命周期、候选失败隔离和同次评分幂等。

### 2026-08-10：比较门禁不能把缺失运营指标当作零

- 现象：基线评估只有验证集 Log Loss/Brier 时，如果晋升策略隐含要求回撤、换手或校准，直接用零或空默认值会产生虚假的通过结果。
- 根因：不同模型评估报告覆盖的指标范围不同；缺少成交/净收益序列不是“指标为零”。
- 修复：`ComparisonPolicy` 的每个指标规则必须显式声明；`ModelComparisonReport` 对缺失候选或冠军指标写入失败项，`promote` 只接受通过且哈希匹配的报告。
- 验证：`tests/ml/test_comparison.py` 覆盖缺失指标阻断、相对退化、校准要求和比较报告篡改；比较时间晚于晋升时间也会被拒绝。

### 2026-08-10：Tushare SDK 旧端点会把网关错误伪装为空数据

- 现象：Tushare `daily`、`stock_basic` 和 `trade_cal` 通过 SDK 返回空 DataFrame，质量门只报告缺少证券。
- 根因：当前 SDK 默认使用旧 HTTP 端点；宿主代理返回 HTTP 502，而 SDK 对非成功 HTTP 响应直接返回空 DataFrame。
- 修复：适配器改用官方 HTTPS Data API，显式解析 HTTP 状态、API 错误码、字段和行长度；HTTP 502 等传输错误标记为可重试。
- 验证：Tushare `daily` 4 行质量门通过；Tushare/BaoStock 固定区间交叉校验通过；新增 API 解析、HTTP 失败和 Token 缺失测试。

### 2026-08-10：Tushare `trade_cal` 有 Token 级频率限制

- 现象：`stock_basic` 成功后，`trade_cal` 请求返回频率超限，交易日文件不会生成。
- 根因：当前 Token 对该接口返回每月 5 次的频率限制；重复验证或开发期手动探测会消耗配额。
- 修复：同步脚本捕获 provider 错误并返回结构化 JSON，不创建半成品输出；下一个配额窗口再用固定区间重试。
- 验证：`sync_tushare_calendar.py` 在错误 40203（5 次/月）时返回退出码 2，目标文件未写入。

### 2026-08-10：日线最高/最低触达不是逐笔成交证明

- 现象：只有日线 OHLC 时，`low <= 买入价 <= high` 不能还原排队、成交量和盘中顺序。
- 根因：日线没有盘口和逐笔成交信息，触达只能作为明确的回测近似。
- 修复：`entry-limit-chase-v1` 显式记录日线触达即成交的近似、开盘价成交和 1% 追价上限；后续若接入更细粒度数据，应新增政策而不是覆盖旧结果。
- 验证：执行政策 ID 写入回测结果和成交记录，测试覆盖低开成交与高开超过追价上限。

### 2026-08-10：最低佣金计算必须先按主板整手确定数量

- 现象：直接用名义金额除以买入价计算分数股，会低估或高估最低佣金和印花税。
- 根因：沪深主板交易数量必须按 100 股整手成交，预算通常不能精确换算为整数股。
- 修复：`a-share-main-board-cost-v1` 按 100 股整手向下取整；不足一手时拒绝完成结果，费用基于实际入场/退出金额计算。
- 验证：新增非整数手成本测试，后端全量测试通过。

### 2026-08-10：全市场日线缺失证券不能全部当作质量错误

- 现象：Tushare `daily(trade_date=...)` 不返回停牌证券，逐证券使用“缺失即错误”会拒绝正常交易日快照。
- 根因：全市场日线接口只返回有行情记录的证券，停牌状态不在本次响应中完整表达。
- 修复：缺失证券记录为聚合警告，并增加显式覆盖率质量门；当前阈值低于 90% 才拒绝发布。
- 验证：测试覆盖覆盖率通过和覆盖率不足两种结果；拒绝快照不会被幂等逻辑跳过。

### 2026-08-10：成熟队列不能用候选逐票补数代替全市场快照重放

- 现象：如果成熟任务每次按候选逐票请求供应商，同一批次会重复抓取，且重跑时可能混入不同抓取时点的数据。
- 根因：任务没有固定全市场输入快照，调度只记录“已完成”而没有记录可复现的数据范围。
- 修复：队列只从已批准全市场快照创建任务，保存股票池和逐日快照 ID；执行时本地重放并拒绝供应商调用、未来快照或混用股票池。
- 验证：新增连续交易日门槛、队列幂等和快照模式不调用供应商测试。

### 2026-08-10：复权价格不能覆盖执行用未复权日线

- 现象：直接把前复权 OHLC 写回 `DailyBar`，会让回测成交价、费用和实际行情混用，除权除息日制造虚假成交或收益。
- 根因：复权因子是研究价格变换，不是供应商重新提供的可成交市场价格。
- 修复：因子独立保存并绑定同日批准日线快照；只通过 `forward_adjust_daily_bars` 生成研究特征输入，保留因子快照 ID，原始 `DailyBar` 永不改写。
- 验证：新增同日快照绑定、覆盖率质量门、因子幂等、因子血缘和前复权特征测试。

### 2026-08-10：交易日缓存不能只依赖文本文件

- 现象：只保存 `a-share-sessions.txt` 无法判断文件覆盖到哪一天，也无法发现人工修改或半成品追加。
- 根因：交易日列表缺少请求范围、来源快照和内容身份，调用方可能把局部日历当成完整历史。
- 修复：使用配套 manifest 记录覆盖区间、来源参考快照 ID 和内容哈希；已覆盖区间不重复请求，校验失败直接阻止消费。
- 验证：新增缺口查询、完全覆盖幂等和篡改拒绝测试。

### 2026-08-10：股票池板块范围不能由代码前缀推断

- 现象：只需要沪深主板时，直接使用全量 `stock_basic` 会混入北交所、创业板和科创板。
- 根因：六位代码前缀不是稳定、完整的板块契约；板块应使用供应商明确返回的 `market`，交易所使用 `exchange`。
- 修复：`TushareReferenceClient.stock_basic` 请求并校验 `market=主板`，再限制 `exchange` 为 SSE/SZSE；清理此前生成的宽范围本地股票池。
- 验证：单元测试覆盖主板保留以及创业板、科创板、北交所过滤；配额窗口后需重新执行真实 `stock_basic` 同步。

### 条件历史统计不能使用当前时点之后的结果

- 现象：直接汇总文件账本中的全部客观结果，会把当前批次导出后才成熟的结果带入历史条件胜率。
- 根因：结果评估时间通常晚于选股批次，但文件存储本身不自动表达“当时是否可见”。
- 修复：分析查询同时限制历史选股日早于当前选股日、批次导出时间不晚于当前参考时间、结果 `evaluated_at` 不晚于参考时间，并要求同一公式和追踪政策。
- 验证：新增未来评估结果过滤测试；不足 100 个完成样本时胜率保持为空。

### 历史因子补齐不能隐式补抓日线

- 现象：因子缺失时顺手重新抓取对应日线，会让补齐任务混入新的抓取结果或新的股票池范围。
- 根因：复权因子必须绑定具体的已批准日线快照，单独的日期不足以表达输入血缘。
- 修复：批量入口先按数据源、交易日和股票池查找已批准 `market_daily` 快照；找不到时只记录阻断，不调用行情供应商；因子快照保存 `source_daily_snapshot_id`。
- 验证：测试覆盖缺少日线不触发因子请求、已批准因子重复运行幂等和质量拒绝报告。

### 可选 LightGBM 不能进入默认运行路径

- 现象：在默认环境直接导入 LightGBM，会让离线测试依赖本机编译库、NumPy 或供应商二进制兼容性。
- 根因：模型训练是后续研究能力，不是原始导入、成熟和统计闭环的运行时依赖。
- 修复：LightGBM 只放入 `ml` 可选依赖；训练函数延迟导入，缺少依赖时返回明确错误；默认测试用小型替身验证时间切分、输出和幂等，真实训练通过显式 extra 命令验证。
- 验证：默认 `unittest discover` 不加载 LightGBM；`uv run --extra ml` 单独完成真实小样本训练。

### 走步收益指标不能把缺失结果当作零收益

- 现象：把未解决、未成熟或缺少净收益的样本填成 0，会虚假降低回撤并提高执行覆盖率。
- 根因：特征数据集和客观结果数据集的生成时间不同，且未入场、未解决不是同一种交易结果。
- 修复：走步报告可选关联 `selection-dataset-v2`；执行率只使用完成/未入场，未解决不入分母；平均净收益、回撤和换手缺字段时保持为空。
- 验证：新增完成 + 未入场指标测试，并保留无结果时的空指标行为。

### 概率校准不能使用 test 标签拟合

- 现象：把 test 预测和标签一起用于拟合校准器，会让最终评估失去独立性。
- 根因：校准本身也是从标签学习参数，不能因为不是分类器训练就绕过时间边界。
- 修复：校准器只读取 validation 预测拟合；test 仅应用已冻结的校准块并计算隔离指标，输出记录两个分区和源哈希。
- 验证：新增 validation PAVA 合并、test 日期顺序和单类别/小样本拒绝测试。

### LightGBM 元数据不能替代运行时模型工件

- 现象：只注册 `model.json` 元数据，影子评分却无法恢复 LightGBM Booster；或把校准器挂到另一基础模型上。
- 根因：模型参数/血缘和可执行模型文本是不同证据，校准器也绑定具体基础模型、特征集和追踪政策。
- 修复：注册时分别保存元数据和 `model.txt`，各自校验哈希；影子加载前校验模型类型、运行时工件和校准器三者的 ID/特征集/政策一致。
- 验证：新增 LightGBM 影子评分与校准器一致性测试，始终保留 `used_for_decision=false`。

### Web 不能把影子分数渲染成权威胜率

- 现象：直接把未校准或影子模型分数标为“胜率”，会绕过校准、样本量和适用日期限制。
- 根因：影子推理与人工标签、客观成熟结果是独立证据，模型生命周期也可能不是 active。
- 修复：API 返回固定 `authoritative=false` 和失败列表；前端使用“原始/校准分数”文字，显示模型版本、样本量、适用日期和政策，不提供交易按钮。
- 验证：新增候选模型证据 API 测试和空影子评分状态测试。

### active 模型不能默认进入推荐评分

- 现象：把 shadow 模型或未批准模型分数直接放进推荐工作台，会绕过人工晋升和质量门。
- 修复：评分 CLI 默认 `shadow`，active 评分必须显式指定 `--lifecycle active`；工作台同时检查 active 生命周期、来源快照批准状态和历史结果证据。

### 证券主数据必须区分生效时间和知晓时间

- 现象：只按 `list_date`/`delist_date` 过滤，而不限制主数据快照的观察时间，会把今天获取的退市事实带回过去。
- 修复：`SecurityMasterSnapshot` 保存 `observed_at`；主数据查询同时要求 `effective_date` 和 `known_at`，并拒绝 `effective_date > known_at.date()`。
- 约束：用户名称覆写只作用于显示层，不修改主数据事实或股票池成员资格。

### 2026-08-11：备用交易日历不能只保存单一来源

- 现象：Tushare `trade_cal` 受 Token 月度频率限制时，直接把 BaoStock 或 AKShare 单独结果写入主缓存，会失去独立来源校验，也容易把备用来源误标成主源。
- 根因：交易日集合是后续行情同步、入场窗口和持有期计算的共同边界；单一备用源的节假日修订或接口异常无法由缓存自身发现。
- 修复：`sync_backup_calendar.py` 同时拉取 BaoStock 和 AKShare，先分别保存原始证据，再比较完整交易日集合；不一致时不发布缓存，manifest 明确记录来源提供商和快照 ID。
- 验证：2024-01-01—2026-08-11 两源均返回 631 个交易日且差异为 0；生成 `data/calendars/trade-cal-sse.txt`，重复执行返回 `skipped=true` 且不再请求网络。

### 2026-08-11：受限 Windows shell 下前端构建需要显式运行时

- 现象：直接执行 `corepack pnpm` 时找不到 `corepack`；使用 pnpm shim 时若 PATH 没有 Node.js，脚本报 `node is not recognized`；补齐 Node.js 后，Vite 在受限沙箱内启动子进程仍可能报 `spawn EPERM`。
- 根因：宿主 PowerShell profile 的 fnm shim 无法创建，且 Vite 构建/开发服务器需要启动子进程，受限 shell 不一定继承可用的 Node.js PATH 或子进程权限。
- 修复：先用 `load_workspace_dependencies` 定位捆绑 Node.js 和 pnpm，再将捆绑 Node.js 的 `bin` 目录加入当前命令 PATH；若仍为 `spawn EPERM`，只对构建或临时开发服务器申请受控的沙箱外权限，不修改项目代码绕过问题。
- 验证：显式捆绑运行时的前端 `typecheck` 通过；受控权限下 `pnpm build` 和本地页面桌面/移动端检查通过。

### 2026-08-11：受限沙箱下 Python 临时目录测试可能误报失败

- 现象：后端 `unittest` 使用 `TemporaryDirectory` 时，受限沙箱可能在临时目录的递归创建或清理阶段返回 `WinError 3/5`，表现为测试错误而非断言失败。
- 根因：测试需要在系统临时目录创建隔离文件，沙箱对该目录的 ACL/清理权限不完整；这与仓库业务代码无关。
- 修复：先区分 traceback 中的临时目录权限错误和真实测试失败；必要时对同一条测试命令申请受控权限重跑，不修改生产代码绕过测试环境限制。
- 验证：受控权限下 `python -m unittest discover -s tests -t . -q` 通过 216 项、跳过 1 项。

### 2026-08-11：通达信行情表名称版本号不能写死为 4

- 现象：通达信导出使用 `名称(5)` 等版本化名称表头时，若只识别 `名称(4)`，文件会被当作普通表格解析，并在代码行报“没有交易所”。
- 根因：通达信不同版本可能改变名称列的括号版本号；而普通表格的代码-only 行又必须继续拒绝，不能直接放宽为所有无交易所代码都按前缀推断。
- 修复：仅在第一列为代码、存在 `涨幅%` 且表头匹配 `名称(n)` 时进入通达信行情表分支，再按该格式的代码命名空间映射交易所；普通表格仍要求明确交易所或代码后缀。
- 验证：`名称(5)` GB18030 样例解析出 5 个候选；针对性导入测试通过。

### 2026-08-16：typescript-eslint 硬性拦截 TypeScript 7.x

- 现象：安装 typescript-eslint 后运行 ESLint 直接报错 `typescript-eslint does not support TS 7.0`，与是否配置无关；`pnpm peers check` 同时报 typescript peer 范围为 `>=4.8.4 <6.1.0`。
- 根因：TypeScript 7 是原生编译器线，不含 typescript-eslint 依赖的完整 JS API；TS 官方以 6.x 作为同语言版本的 JS API 并行发布线，供工具链使用。
- 修复：项目 `typescript` 固定在 `~6.0.3`，tsc/Vite/typescript-eslint 共用同一条工具链；若未来要回到 TS 7 原生编译器提速，需先确认 typescript-eslint 已声明支持（跟踪 issue typescript-eslint#10940），不能只改版本号。
- 验证：`corepack pnpm add -D typescript@~6.0.3` 后 `pnpm peers check` 无问题，`eslint .` 正常解析并产出结果。
