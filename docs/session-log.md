# 会话记录

### 2026-09-17 策略信号支持删除未判断候选

- 完成内容：新增独立 `SelectionCandidateDismissal` 追加事件和 `tdx/dismissals` 持久化；新增 `POST /v1/selection-batches/{batch_id}/dismissals` 命令，按同一选股日、同一标准化证券的跨策略共享评审校验未判断状态，重复请求幂等，并为归档批次、错误候选和已有人工判断返回结构化错误。工作台默认策略信号列表/分页/汇总隐藏 dismissal，策略与分析统计、股票历史详情和时间线保留 dismissal 证据。
- 完成内容：策略信号前端仅为 `unreviewed` 行显示删除入口；删除前确认只从默认列表移除且原始证据保留，取消不写入，成功后刷新分页/汇总并在末页回退；主行打开与删除为同级原生按钮，API 失败显示真实错误。
- 修改文件：`backend/src/stratgauge/domain/selections/`、`backend/src/stratgauge/data_pipeline/selection_store/`、`backend/src/stratgauge/api/selections/`、`backend/src/stratgauge/api/workbench.py`、`backend/src/stratgauge/application/workbench/`、相关后端测试；`frontend/src/components/workbench/workspace.tsx`、`frontend/src/types.ts`、`frontend/src/styles.css`、`frontend/tests/visual/workbench-dismissal.spec.ts` 及观察池夹具字段。
- 实际验证：后端 `PYTHONPATH=src uv run python -m unittest tests.data_pipeline.test_selection_store tests.api.test_selections tests.application.test_workbench -q`（67 项通过）；相关 19 个 Python 文件 Ruff check、format check 通过；相关 16 个源文件 MyPy 通过；前端 `corepack pnpm typecheck` 通过；`corepack pnpm lint` 0 error、19 条既有 warning；定向 Playwright `workbench-dismissal.spec.ts`、`workbench-annotation-target.spec.ts`、`watchlist.spec.ts` 共 4 项通过；任务涉及路径 `git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。
- 遗留问题：本轮误执行的完整前端视觉套件中仍有既有主板行情同步 completed/partial 截图基线差异，未扩大范围修复；未执行 commit/push、真实 provider 请求或真实业务数据写入。

### 2026-09-14 修复 Windows 保留端口导致统一启动失败

- 完成内容：统一启动器启动前探测后端 TCP 端口；默认 8000 不可绑定时自动切换到 8091，显式设置 `STRATGAUGE_BACKEND_PORT` 时使用指定端口；后端 Uvicorn 增加明确的 host/port 参数，前端 Vite 代理读取同一环境变量，避免后端回退后 API 仍指向 8000。
- 修改文件：`start.py`、`backend/tests/test_start_launcher.py`、`frontend/vite.config.ts`，并同步启动说明、当前状态和踩坑记录。
- 实际验证：`cd backend; uv run python -m unittest tests.test_start_launcher -v`（6 项通过）；`uv run ruff check ../start.py tests/test_start_launcher.py`、`uv run ruff format --check ../start.py tests/test_start_launcher.py`、`uv run mypy ../start.py tests/test_start_launcher.py` 均通过；`cd frontend; corepack pnpm typecheck` 通过，`corepack pnpm lint` 0 error、19 条既有 warning；Windows 实际启动中后端 `/healthz` 和前端 `http://localhost:5173/` 均返回 200，退出后 8091/5173 无监听残留。
- 遗留问题：未执行完整后端测试、前端 build 或 commit/push；端口保留段的具体系统组件未进一步拆分确认。

### 2026-09-13 统一同日同股跨策略人工评审状态

- 完成内容：修正工作台读取投影；不同策略在同一选股日期命中同一标准化证券时，共享该 `(symbol, selection_date)` 评审组的最新人工判断、状态和观察起点，避免策略信号列表出现“未判断”而观察池显示已判断。候选自身 `annotations/latest_annotation` 与评审组 `review_annotations/latest_review_annotation` 分开返回，并通过 `review_group_key`、`review_source_candidate_id` 明确评审组和来源；评审组仅取 `as_of` 可见记录并按 `(recorded_at, annotation_id)` 最大值裁决冲突；观察池复评摘要按评审组去重。原始策略信号行、候选级 annotation 写入、交易、排除和客观结果边界保持不变；不同选股日期仍独立。
- 修改文件：`backend/src/stratgauge/application/workbench/service.py`、`backend/src/stratgauge/application/workbench/views.py`、`backend/src/stratgauge/application/workbench/helpers.py`、`backend/tests/application/test_workbench.py`、`frontend/src/types.ts`、`frontend/src/components/workbench/workspace.tsx`、`frontend/src/components/workbench/detail-drawer.tsx`、`frontend/tests/visual/workbench-annotation-target.spec.ts`，并同步相关项目文档。
- 实际验证：`cd backend; PYTHONPATH=src uv run python -m unittest tests/application/test_workbench.py tests/api/test_workbench.py -q`（19 项通过，含既有 Starlette 弃用警告）；相关 Ruff check 通过；相关 `mypy`（4 个文件）通过；`cd frontend; corepack pnpm typecheck` 通过；`corepack pnpm lint` 0 error、19 条既有 warning；定向 Playwright `tests/visual/workbench-annotation-target.spec.ts` 1 项、`tests/visual/workbench-manual-position.spec.ts` 6 项通过；`git diff --check` 通过（仅 Windows 换行转换提示）。
- 遗留问题：未执行前端完整构建/完整视觉套件、完整测试套件、commit/push 或真实 provider 请求；策略信号原始行按设计仍会保留，需使用筛选或观察池查看处理状态。

### 2026-09-04 删除独立工作台入口并以策略信号为默认首页

- 完成内容：删除前端独立“工作台”导航和页面，将“策略信号”设为默认首页；移除工作台专属的前 12 条待办截断、连续处理按钮和样式。保留策略信号的汇总、分页、筛选、排序、候选上下文、股票详情、人工评审、交易入口，以及其他研究路由和共享 `/v1/workbench/*` API。直接操作说明改为从策略信号进入；“推荐工作台”独立概念保留。
- 文档：同步更新 `docs/current-state.md`、`docs/decisions.md`、`docs/使用文档.md` 和 `docs/系统规格说明.md` 的当前导航与职责说明。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`corepack pnpm lint` 退出码 0（0 error、19 条既有 React Hooks warning）；`corepack pnpm build` 通过；定向 `workbench-annotation-target.spec.ts` 1 项通过，验证默认首页为策略信号且无工作台导航；组合回归 10 项中 9 项通过，观察池外链用例因既有未提交 `StockDetailDrawer` 对夹具缺失 `position` 字段读取 `quantity` 失败，未修改该既有代码或夹具；`git diff --check` 通过。
- 遗留问题：组合回归中的观察池外链失败属于本次工作区已有缺陷，需后续单独修复；未执行 commit/push、真实 provider 请求或业务数据写入。

### 2026-09-04 取消卖出跟踪案例必选限制

- 完成内容：修正手工确认卖出流程；`tracking_case_id` 改为可选，卖出未选择案例时不再因没有、多个或无法推断案例而阻断；已有单个可推断案例仍可自动关联，明确选择的案例继续校验证券和剩余数量。卖出原因仍保持必填。前端移除“卖出必选”文案，允许无跟踪案例直接提交，并补充回归测试。
- 文档：同步更新 `docs/decisions.md`、`docs/current-state.md` 和 `docs/使用文档.md`，明确跟踪案例是独立 evidence 范围，不是实际卖出的前置条件。
- 实际验证：后端相关测试 17 项通过；后端完整测试 440 项通过、1 项跳过；Ruff check、format check 通过；MyPy 346 个源文件通过；前端 typecheck、build 通过；前端 lint 0 error、19 条既有 React Hooks warning；定向 Playwright `workbench-manual-position.spec.ts` 6 项通过；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。未执行 commit/push。
- 遗留问题：未关联的卖出不会纳入任何 tracking evidence；不改变现有持仓成交重放和卖出原因约束。

### 2026-09-04 修正放大图表初始提示和滚动条

- 完成内容：修正放大图表打开时因 Radix Dialog 自动聚焦/指针命中而提前显示数据点提示的问题；初始保持隐藏，只有实际移动到数据点或键盘聚焦才显示。放大图表取消内部滚动条，按视口自适应尺寸展示；观察池和持仓共用实现。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，覆盖初始无提示、无滚动条和两类图表放大/返回，临时配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未改变 API、数据或交易语义。

### 2026-09-04 修正折线图提示保持行为

- 完成内容：修正观察池和持仓共用折线图提示逻辑：初始不显示，命中数据点后保持显示，不因鼠标移出或键盘焦点离开而消失；命中下一个数据点时替换为新数据，关闭详情/次级图表后随组件清除。同步补充使用文档、当前状态和设计决策。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，临时配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未改变 API、数据或交易语义。

### 2026-09-04 放大观察池与持仓折线图

- 完成内容：概览折线图增加图内留白和更大的数据点命中范围，补充“放大查看图表”应用内次级图表页；观察池和持仓共用同一 SVG 图表实现，放大页不重复请求数据，支持关闭返回详情。新增浏览器回归覆盖两类图表的放大/返回和移动端页面宽度约束。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，临时配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未改变 API、数据或交易语义。

### 2026-09-04 观察池与持仓折线图数据点提示

- 完成内容：共用原生 SVG 折线图的数据点支持鼠标悬停和键盘聚焦；观察池与持仓均显示交易日/估值日、收盘价、当日涨跌幅或净收益率、累计涨跌幅或净收益率。持仓表现 API/类型新增可空 `close` 字段；缺失行情明确显示不可用，不补零或旧值。同步更新 `docs/使用文档.md`、`docs/current-state.md` 和 `docs/decisions.md`。
- 实际验证：`cd backend; $env:PYTHONPATH='src'; uv run python -m unittest tests.application.test_workbench tests.api.test_workbench -q`（18 项通过）；后端相关 Ruff check、format check、MyPy 通过；`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，临时配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未访问真实 provider 或写入 `backend/data`。

### 2026-09-04 观察池表现补充最高涨幅和最大跌幅

- 完成内容：观察池详情“观察表现”摘要新增观察期最高涨幅、最大跌幅及对应交易日，分别取可用累计涨跌幅的最大值和最小值；缺失或无效点不参与计算，最大跌幅不冒充峰值回撤。新增回归夹具覆盖正负极值，并保留折线、逐日明细和证据状态。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，使用临时 4173 端口，配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未新增后端/API 或真实行情验证需求。

### 2026-09-04 观察池详情补充累计涨跌幅折线图

- 完成内容：在既有观察池候选级逐交易日百分比接口和详情表格基础上，新增详情抽屉“观察表现”的原生 SVG 累计涨跌幅折线图；按 approved 日线累计比例绘制，缺失或无效点断开，不补零、不沿用前值、不跨缺口连接，保留原有状态、原因和逐日证据明细。同步更新 `docs/使用文档.md`、`docs/current-state.md` 和 `docs/decisions.md`。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`cd frontend; corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`cd frontend; corepack pnpm build` 通过；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，使用临时 4173 端口，配置已删除）；`git diff --check` 通过，仅有 Windows 换行转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；未新增后端/API 或真实行情验证需求。

### 2026-09-04 观察池候选逐交易日百分比表现与文档收口

- 完成内容：新增候选级 `GET /v1/workbench/candidates/{candidate_id}/observation-performance` 只读投影和前端详情抽屉“观察表现”页签；分页观察池只保留摘要。实现按 candidate ID 与当前连续 `watch`/`prepare_buy` 周期隔离，`discard` 重置；仅读取本地 approved `market_daily` 和追加式人工事实，按 `as_of` 及上海时区证券日期过滤，缺失证据返回 `unavailable`/`incomplete`，不补零、不展示金额。为新增公开 GET 路由补充基准样例清单和路由数量回归，并修正跨上海日期边界的测试夹具。
- 文档收口：更新 `docs/使用文档.md` 的 API、列表摘要、详情抽屉和逐日计算说明；更新 `docs/current-state.md`、`docs/decisions.md` 和本记录。新增的上海时区日期边界复发问题记录在 `docs/pitfalls.md`；`docs/系统规格说明.md` 的 6.4.3 已与实现一致，无需重复改写。
- 实际验证：`cd backend; uv run ruff format --check .`（345 个文件已格式化）；`cd backend; uv run ruff check .`（通过）；`cd backend; PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q`（439 项通过、1 项跳过）；`cd backend; uv run mypy`（346 个源文件无问题）；`cd frontend; corepack pnpm lint`（退出码 0、0 error、19 条既有 warning）；`cd frontend; corepack pnpm typecheck`（通过）；`cd frontend; corepack pnpm build`（通过）；`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium --config=playwright.temp-4173.config.ts`（5 项通过，临时配置已删除）。默认 5173 端口因本机返回 `EACCES` 未能启动，4173 可监听后完成同等测试；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异；真实 provider、训练和模型链仍需既有人工授权与数据准备。未写入 `backend/data`，未执行 commit/push。


### 2026-09-03 观察池重复证券稳定连续分组

- 完成内容：观察池在既有整体排序和筛选结果之后，按证券首次出现顺序进行稳定分组，使相同证券连续显示；组内保留原记录顺序。既有重复证券颜色左边框标记逻辑保持不变，未改变分页、点击、交易语义或证券代码排序规则。
- 实际验证：`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium`（4 项通过，包含交错重复证券被调整为连续分组及颜色标记保持）；`corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`corepack pnpm build` 通过；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异。

### 2026-09-03 观察池重复证券颜色标记

- 完成内容：观察池表格按当前页的标准化证券代码统计重复项；重复证券行增加相同颜色的 4px 左侧边框，唯一证券不增加标记。颜色标记仅作用于观察池，不改变行点击、筛选、排序或交易语义。
- 实际验证：`cd frontend; corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts`（3 项通过，含重复证券颜色、宽度和唯一行断言）；`corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`corepack pnpm build` 通过；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。未执行 commit/push。
- 遗留问题：无本次变更新增的已知问题；完整前端视觉回归仍有既有主板行情同步 completed/partial 截图基线差异。

### 2026-09-03 单证券持仓历史净收益率表现与文档收口

- 完成内容：移除组合级金额表现 API/UI/文档描述，改为只读 `GET /v1/workbench/stocks/{symbol}/performance`；用户点击当前持仓后，在既有 `StockDetailDrawer` 的“表现”标签按需查看单证券历史净收益率走势、百分比摘要、估值日明细和 approved 行情证据。严格按 `as_of` 过滤成交与 `available_at`，费用计入收益率基准；无活动持仓、已平仓或缺行情时显示明确状态/原因并保持百分比为空，不补零、旧价或猜测值。
- 文档收口：更新系统规格、使用文档、当前状态和设计决策，补充单证券 API、百分比字段、抽屉入口、费用、时间点可见性和缺失证据边界。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`corepack pnpm build` 通过；`corepack pnpm exec playwright test tests/visual/workbench-manual-position.spec.ts --project=chromium`（4 项通过）；后端完整测试 438 项通过、1 项跳过；后端 Ruff check、format check、MyPy 通过；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示。未访问真实 provider、未写入 `backend/data`、未执行 commit/push。
- 遗留问题：完整前端视觉回归仍有主板行情同步 completed/partial 的既有截图基线差异；真实行情/模型链仍按现有状态待人工授权和执行。
### 2026-09-01 前端操作区间距与窄屏布局

- 完成内容：仅调整前端呈现。为主板行情同步任务操作区、数据管理标签编辑区和分页控件补齐容器级 `gap`、换行、收窄和长文本断行规则；将通用 `DialogFooter` 从桌面断点的 `space-x-2` 改为跨方向统一 `gap`。未改变 API、数据、任务调度、交易语义、Tab/筛选器/分段控件、键盘访问、焦点可见性或按钮可用性。
- 回归覆盖：增加同步任务操作区和分页控件的 gap 断言，增加 390px 对话框 Footer 间距及无横向溢出断言；仅更新受本次操作区间距影响的排队状态截图，未更新 completed/partial 等无关截图。
- 实际验证：`cd frontend; corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、19 条既有 React Hooks warning）；`corepack pnpm build` 通过；`corepack pnpm exec playwright test tests/visual/main-board-market-sync.spec.ts -g "固定排队状态展示并保留 queued-only 操作"`（1 项通过）；`corepack pnpm exec playwright test tests/visual/ml-readiness.spec.ts`（4 项通过）；首次组合运行两份定向 spec 为 5 项通过、3 项失败，其中排队截图已按本次 gap 更新后通过，仍有 2 个既有 completed/partial 结果截图差异未处理；`git diff --check` 通过，仅有 Windows LF/CRLF 转换提示；未执行 commit/push。
- 遗留问题：完整前端视觉回归仍需后续单独核对并处理主板行情同步 completed/partial 的既有截图基线差异；本轮未修改无关基线，也未执行真实 provider、业务数据写入或外部状态操作。

### 2026-09-01 手工持仓 tracking ML evidence 与来源隔离

- 完成内容：新增独立 `manual-position-tracking-dataset-v1` JSONL 和 `manual-position-tracking-report-v1` 报告；只收集 tracking case 关联且在 `tracking_started_at` 之后的手工订单/成交/复盘，以及 evaluated_at 前已批准的本地 market_daily。历史登记前事实、候选/策略/推荐关联、未来事实和歧义复盘均保留排除/资格原因；源事实保存 SHA-256；dataset/report 目标不可覆盖。报告 `ready` 仅表示 evidence 覆盖完整，不表示训练或模型发布准入。现有 selection ML 四用途导出行保留 `SelectionBatch.source` 的 `tongdaxin`/`manual` 来源。未接入既有训练、注册、shadow、推荐或交易，也未创建 provider、访问外部行情或写入业务数据。
- 使用文档和系统规格补充三类事实边界、CLI 参数/输出、时间点过滤、退出码和人工训练边界；current-state、decisions 和 pitfalls 同步本轮新事实。
- 实际验证：`cd backend; uv run ruff check .` 通过；`cd backend; uv run ruff format --check .` 通过（343 个文件已格式化）；`cd backend; uv run mypy` 通过（344 个源文件无问题）；`cd backend; $env:PYTHONPATH='src'; uv run python -m unittest tests.backtest.test_manual_position_tracking_dataset tests.application.test_manual_position_tracking_dataset_cli tests.backtest.test_selection_dataset tests.backtest.test_selection_ml_datasets -q`（14 项通过）；`cd backend; $env:PYTHONPATH='src'; uv run python -m unittest discover -s tests -t . -q`（434 项通过、1 项跳过）；`cd backend; uv run python scripts/export_manual_position_tracking_dataset.py --help` 成功返回；`git diff --check` 通过，仅有 Windows 换行转换提示。
- 遗留问题：未执行真实 provider、未写入 `backend/data`、未执行 commit/push；手工 evidence 仍没有独立标签/特征/模型训练链，需未来另行设计并由操作者显式授权。

### 2026-09-01 ML 市场证据补齐编排

- 完成内容：新增 `backend/src/stratgauge/application/ml_market_evidence.py` 和 `backend/scripts/prepare_selection_ml_market_evidence.py`。命令按显式批次、归档批次或选股日期范围推导历史/成熟交易日窗口，扩展交易日历并依次补齐全市场 `market_daily`、同 provider 复权因子和独立 `000300.SH` `index_daily`；输出阶段状态、快照、缺失、失败、质量和时间点阻断报告，支持批准快照复用和不可覆盖报告写入。未接入成熟、特征、训练、注册、shadow、推荐或交易。
- 文档：补充使用命令、参数、输出阶段、provider/凭据边界、`available_at` 限制和后续人工特征/训练步骤；在设计决策中记录显式编排与时间点阻断。
- 实际验证：`cd backend; uv run python -m unittest tests.application.test_ml_market_evidence tests.application.test_ml_market_evidence_cli tests.backtest.test_selection_dataset tests.backtest.test_selection_ml_datasets -q`（14 项通过）；完整后端 `unittest discover`（420 项通过、1 项跳过）；`uv run ruff check .`、`uv run ruff format --check .` 和 `uv run mypy` 均通过；CLI `--help` 成功返回。`git diff --check` 仅有 Windows 换行转换提示。未执行真实 provider、未写入 `backend/data`、未执行 commit/push。
- 遗留问题：当前本地仍缺足量 `win/loss/flat` 成熟标签、可用特征快照和独立基准证据；补齐后仍需操作者按显式特征、join、训练和模型生命周期命令逐阶段运行。

### 2026-09-01 将旧版人工标签冻结截止调整为次日 02:00

- 完成内容：旧版 `ManualSelectionLabel` 现在严格接受导出时间至选股日期次日 02:00:00（不含）的标签，截止时间沿用 `exported_at` 时区；同步更新 API 错误提示、边界测试、系统规格、使用文档、当前状态、设计决策和踩坑说明。新的 `SelectionAnnotation` 评审时间语义未改变。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.data_pipeline.test_selection_store tests.api.test_selections -q`（49 项通过）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q`（411 项通过、1 项跳过）；`cd backend && uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy` 均通过；`git diff --check` 通过（仅有 Windows 换行转换提示）。
- 遗留问题：未执行 commit/push。

### 2026-08-31 更正策略信号页面语义

- 完成内容：将导航、信号页、摘要卡和导入页说明中的“今日信号”改为“策略信号”；保留后端历史/当前可用批次查询，页面增加最新行情收盘日提示，信号行增加实际行情 `session_date`，缺失时显示不可用。运行中本地服务核对为 87 条信号，最新选股日/行情日为 2026-08-28，2026-08-31 为 0 条。
- 实际验证：`cd frontend && corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、20 条既有 warning）；`corepack pnpm build` 通过；`corepack pnpm exec playwright test tests/visual/workbench-annotation-target.spec.ts` 通过（1 项）；`git diff --check` 通过。未执行 commit/push。
- 遗留问题：后端接口仍保留 `daily-signals` 兼容路径名称；本次未改变其筛选和分页语义。

### 2026-08-30 修复重复证券人工评审目标错位

- 完成内容：确认 `002673.SZ`（西部证券）同时存在两个选股批次，其中一个候选已有 `SelectionAnnotation`、另一个没有；修复工作台信号行打开详情时丢失 `candidate_id` 的问题，详情抽屉现在按被点击的批次候选执行人工评审和候选关联操作。新增重复证券回归用例，验证未判断行显示未判断且 POST 使用该行候选 ID。
- 实际验证：`cd frontend && corepack pnpm typecheck` 通过；`corepack pnpm lint` 通过（0 error、20 条既有 warning）；`corepack pnpm build` 通过；`corepack pnpm exec playwright test tests/visual/workbench-annotation-target.spec.ts` 通过（1 项）。`git diff --check` 通过。
- 遗留问题：同一证券在不同批次仍按独立候选保存，已评判一个批次不会自动替另一个批次完成评判；未执行 commit/push。

### 2026-08-23 readiness 阻断盘点

- 完成内容：只读盘点 `backend/data`、选股导入/结果文件和 `selection-market` manifest。当前有 12 个导入批次、33 个候选、4 个客观结果；4 个结果均为 `unresolved`，采用旧 5-session policy。`selection-market` 有 10 个已批准 `market_daily` 日期和 7 个已批准复权因子快照，但没有独立 `index_daily` 快照。
- 实际验证：交易日历 `data/calendars/trade-cal-sse.txt` 共 631 个交易日，末尾为 2026-08-11；批准的主板行情快照最晚到 2026-08-21。`corepack pnpm exec playwright test tests/visual/main-board-market-sync.spec.ts` 未能启动 Vite，进程绑定 `127.0.0.1:5173` 返回 `EACCES`，因此没有截图通过结果；前端 `corepack pnpm typecheck`、`corepack pnpm lint`、`corepack pnpm build` 均通过（lint 20 条既有 warning、0 error）；`git diff --check` 通过；未执行真实 Tushare 请求、成熟任务或训练。
- 遗留问题：必须先获得覆盖后续交易日的显式交易日历、独立 `000300.SH` 基准和满足冻结 10-session policy 的成熟结果；之后才能重跑 readiness 并进入四用途训练链。视觉回归需要解除本机端口绑定权限。

### 2026-08-22 四用途 ML 离线演练与人工操作文档

- 完成内容：补充 `docs/使用文档.md` 的四用途人工操作图、11 个 CLI 的阶段顺序、输入/输出血缘、时间点边界、证据目录和无标签 shadow canonical JSONL/SHA-256 要求；README 增加入口提示；系统规格补充显式时间点特征观察阶段；current-state、decisions 和 pitfalls 同步当前事实。文档明确所有阶段人工触发，保留旧新链隔离、final-test 隔离、`used_for_decision=false`、无外部编排和真实 provider/训练需授权边界。
- 实际验证：文档变更后执行 `git diff --check`（通过，仅有 Windows 换行转换提示）；此前本任务依据的离线演练 `cd backend && PYTHONPATH=src uv run python -m unittest tests.application.test_ml_offline_rehearsal -v`（1 项通过）、后端完整测试（408 项通过、1 项跳过）、`uv run ruff check .`、`uv run ruff format --check .`（334 个文件已格式化）和 `uv run mypy`（335 个源文件通过）均已有真实结果；未执行 commit/push。
- 遗留问题：真实 Tushare/成熟数据收集/真实 LightGBM 仍需授权和足量 readiness 数据；工作区仍包含未提交的代码与文档改动。

本文件按时间追加每次工作的完成内容、实际验证和遗留问题，不记录敏感信息或未经验证的结果。

### 2026-08-22 Web 列表读取优化收口

- 完成内容：后端工作台、成熟结果和数据管理列表改为先过滤/排序/分页、再批量读取当前页详情；保留事实文件和索引回退路径，并保持旧完整列表、公开响应和成熟全范围状态统计。前端导入数据按路由加载，工作台搜索防抖且取消过期请求，刷新期间保留旧列表；统计和成熟结果按需读取，旧手工台账展开后挂载。未修改既有详情抽屉、样式文件或 `.zcode/`，未执行 commit/push。
- 实际验证：后端相关回归此前执行为 376 项通过、1 项跳过；变更后的前端 `cd frontend && corepack pnpm typecheck`、`corepack pnpm build`、`corepack pnpm lint` 均通过。`cd frontend && corepack pnpm exec playwright test tests/visual/ml-readiness.spec.ts` 为 3 项通过；`portfolio-order-lifecycle.spec.ts` 为 2 项通过；完整 `corepack pnpm test:visual` 为 6 项通过、3 项主板行情同步截图比较失败。`git diff --check` 通过，仅有 Windows 换行转换提示。
- 遗留问题：完整视觉回归的 3 个失败未通过覆盖既有 `frontend/src/styles.css` 或更新无关快照处理；ML readiness 仍在进入导入页时加载，当前任务允许范围只延迟了导入数据 hook、成熟列表、统计和旧手工台账；尚未连接运行中服务建立真实延迟基线。

### 2026-08-22 顺序 GET 接口性能巡检

- 完成内容：新增 `backend/scripts/benchmark_get_endpoints.py` 和版本化 `backend/scripts/benchmark_get_cases.json`。CLI 通过运行中 FastAPI 的 `/openapi.json` 发现 43 个 GET 路由，严格校验样例双向覆盖；28 个可运行查询使用安全分页/时间参数，15 个动态 ID、时间点证据或流式下载接口写明 `skip_reason`。每个可运行样例预热后顺序请求，输出状态码、响应字节数、中位/最大耗时；不输出响应正文，不启动服务、不写入业务数据。配置错误为退出码 2，HTTP、网络和阈值失败为退出码 1。
- 测试边界：新增隔离 `create_app()` 回归测试，使用临时数据根、provider 哨兵和 TestClient 验证正式清单与 OpenAPI 一致且全部可运行样例返回 200；没有访问网络或真实数据根，也没有把机器相关性能阈值接入 CI。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.test_benchmark_get_endpoints tests.test_benchmark_get_cases -v`（8 项通过）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q`（384 项通过、1 项跳过）；`cd backend && python -m json.tool scripts/benchmark_get_cases.json > NUL`（通过）；相关 Ruff check/format check 和 `uv run mypy`（320 个源文件）通过。
- 遗留问题：尚未连接运行中的真实服务执行一次实际延迟巡检，因此本轮没有性能数值或 CI 基线；工作树仍包含其他协作者未提交改动，未执行 commit/push，未跟踪 `.zcode/` 保留且未纳入。

### 2026-08-20 数据导入、独立基准证据与走步结果边界收口

- 完成内容：完成只读成熟结果列表及 API 回归收口；新增只允许 `000300.SH` 的 `IndexDailyRequest`、Tushare `index_daily` 适配和 `sync_tushare_index_daily.py`，复用不可变 `ingest`/`SnapshotStore` 快照管道；`MLReadinessService` 只认可已批准、日期匹配的独立 `000300.SH` `index_daily` 快照；将 walk-forward 客观结果索引、收益/回撤/换手计算和日期/Decimal 解析提取到 `backend/src/stratgauge/ml/walk_forward_outcomes.py`，保留公共 API、报告身份、哈希输入、计算语义和异常文字。
- 研究边界：本轮没有执行真实 Tushare 请求，没有执行成熟任务、事后行情/特征生成、训练、模型注册或 shadow；没有恢复成熟任务 Web 执行入口，也没有在 API 请求中启动后台线程。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.ml.test_walk_forward -v`（7 项通过）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q`（371 项通过、1 项跳过）；`cd backend && uv run ruff check .`（通过）；`cd backend && uv run ruff format --check .`（316 个文件已格式化）；`cd backend && uv run mypy`（317 个源文件通过）；`git diff --check`（通过，仅 Windows 换行转换提示）。
- 遗留问题：当前本地 readiness 仍缺足量成熟标签、特征快照和独立基准证据；`master` 未配置远程仓库，未执行 commit/push；未跟踪 `.zcode/` 保留且未纳入。

## 记录格式

每次记录应包含日期、完成内容、实际执行的验证及遗留问题。

### 2026-08-16 清理成熟任务前端遗留样式

- 完成内容：确认成熟任务操作区已于前一轮移除，前端源码和测试没有相关 class 消费者；删除 `frontend/src/styles.css` 中成熟任务专用基础/状态/响应式规则，保留 `.import-form` 与 `.dialog-form` 的共享布局。未修改当前未提交的后端路由拆分及其他既有工作区改动。
- 实际验证：`cd frontend && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm build`（通过；lint 0 error、23 条既有基线 warning）；`cd frontend && corepack pnpm exec playwright test tests/visual`（7 项通过）；`git diff --check`（通过，仅 Windows 换行警告）。
- 遗留问题：大组件和综合服务仍按领域边界待后续小步拆分；本轮未执行 commit/push。

### 2026-08-16 调整导入数据页布局

- 完成内容：导入数据页改为“导入/批次/候选”主流程优先，机器学习前置检查和主板行情同步归入后置运行状态区；导入与批次宽屏均分双列，中窄屏单列；新增桌面列宽均衡和窄屏无横向溢出回归断言。同步更新受布局变化影响的视觉快照。
- 实际验证：`cd frontend && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm build`（通过；lint 0 error、23 条既有基线 warning）；`cd frontend && corepack pnpm exec playwright test tests/visual`（8 项通过）；`git diff --check`（通过，仅 Windows 换行警告）。
- 遗留问题：成熟任务 Web 操作区仍未恢复；本轮未修改当前未提交的后端路由拆分，未执行 commit/push。

### 2026-08-16 修正机器学习前置检查卡片内边距

- 完成内容：为 `frontend/src/styles.css` 的 `.ml-readiness-panel` 补充与其他面板一致的 `var(--space-5)` 内边距，修复内容贴近卡片边框的问题；更新受页面纵向布局影响的移动端行情同步视觉快照。
- 实际验证：`cd frontend && corepack pnpm typecheck`（通过）；`cd frontend && corepack pnpm exec playwright test tests/visual`（8 项通过）；`git diff --check`（通过，仅 Windows 换行警告）。
- 遗留问题：本轮未执行 commit/push。

### 2026-08-15 机器学习就绪提示

- 完成内容：新增只读 `MLReadinessService`、`GET /v1/ml/readiness` 和导入数据页机器学习前置检查卡片。就绪投影统计候选成功率用途的成熟结果、训练类别、结果政策、特征快照、独立基准、复权因子、用途模型注册及 shadow 状态；Web 只刷新本地事实，不启动 provider、训练、注册或 shadow。新增后端应用/API 测试和确定性 Playwright 回归，更新相关使用文档、系统规格、当前状态和设计决策。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.application.test_ml_readiness tests.api.test_ml_readiness -q`（4 项通过）；`cd backend && uv run ruff check src/stratgauge/application/ml_readiness.py src/stratgauge/api/ml_readiness.py src/stratgauge/api/app.py tests/application/test_ml_readiness.py tests/api/test_ml_readiness.py`（通过）；`cd backend && uv run mypy src/stratgauge/application/ml_readiness.py src/stratgauge/api/ml_readiness.py src/stratgauge/api/app.py tests/application/test_ml_readiness.py tests/api/test_ml_readiness.py`（通过）；`cd frontend && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm build`（通过）；`cd frontend && corepack pnpm exec playwright test tests/visual`（7 项通过）；当前数据根 readiness 实测为 4 个批次、14 个候选、0 个成熟训练标签、10 个待成熟和 4 个未解决。
- 遗留问题：当前仍没有足够成熟标签、特征快照或独立 `000300.SH` `index_daily` 证据，不能运行完整训练或 handoff；真实 Tushare 请求未执行；工作树包含既有未提交改动，未执行 commit/push，`.agents/` 和测试运行产物保持未跟踪。

### 2026-08-15 IO 优化工作流收口

- 完成内容：补充并验证 IO 回归断言。SnapshotStore 精确 catalog 命中不读取 manifest；SelectionReadIndex 批量 outcome 读取不触发全量目录扫描并按索引路径加载；JSONL 数据集下载直接返回 `StreamingResponse`/`application/x-ndjson`；主板同步任务在重新创建 App 后仍保留 `cancelled` 状态。同步更新使用文档、系统规格、当前状态和设计决策，明确分页契约、流式下载、LocalJobStore、显式 runner、queued-only 取消、事实源与 Tushare 任务 transport 生命周期。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.data_pipeline.test_store tests.data_pipeline.test_selection_index tests.api.test_selections tests.api.test_workbench tests.api.test_market_sync tests.application.test_market_sync_jobs -q`（50 项通过）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -q`（359 项通过，1 项跳过）；`cd backend && uv run ruff format --check src tests scripts`（227 个文件已格式化）；`cd backend && uv run ruff check src tests scripts`（通过）；`cd backend && uv run mypy`（228 个源文件通过）；`cd frontend && corepack pnpm typecheck`、`corepack pnpm lint`、`corepack pnpm build`（均通过）；`git diff --check`（通过，仅 LF/CRLF 警告）。
- 遗留问题：真实 Tushare 请求未执行；Tushare `trade_cal` 供应商溯源仍受既有 40203 配额限制；工作树包含本轮及此前未提交改动，未执行 commit/push，`.agents/` 保持未跟踪。

## 会话

### 2026-08-15 Windows Task Scheduler 主板同步运维验收

- 完成内容：补充 `run_main_board_market_sync_jobs.py` 的 Windows Task Scheduler 操作说明，固定 PowerShell、`backend` 起始目录、绝对 `--data-root`、`PYTHONPATH`、`uv.exe` 路径、`--limit 1` 和追加日志边界；明确调度器只调用一次显式 runner，不调度 `start.py`、Uvicorn 或服务内定时器。补充主板同步空队列测试，断言没有创建 Tushare adapter，也没有调用同步函数。
- 实际验证：在当前 Windows 宿主用 PowerShell Scheduler-shaped action 运行一次隔离临时数据根目录的空队列 runner，日志解析为 `jobs: []`，数据根目录中没有 `job-*.json`，日志不含 `TUSHARE_TOKEN` 文本，退出码为 `0`；未设置或使用 provider 凭证，未发起 Tushare 请求。`cd backend && PYTHONPATH=src uv run python -m unittest tests.application.test_market_sync_jobs -q`（6 项通过）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -q`（361 项通过、1 项跳过）；`cd backend && uv run ruff check src tests scripts`（通过）；`cd backend && uv run ruff format --check src tests scripts`（228 个文件已格式化）；`cd backend && uv run mypy`（229 个源文件通过）；`git diff --check`（通过，仅 Windows LF/CRLF 警告）。
- 遗留问题：本次只验收空队列和离线 runner 边界，没有执行非空队列真实行情同步；Tushare `trade_cal` 溯源仍受既有配额限制；工作树包含本轮及此前未提交改动，未执行 commit/push，`.agents/` 保持未跟踪。

### 2026-08-14 第一轮 clean-code 代码整理

- 完成内容：依据 `docs/clean-code.md` 做行为保持型整理。后端补齐 Ruff 格式基线和严格 MyPy 输入，修复测试中的可选值收窄、类型返回和变量复用问题；将 ML 模块重复的规范 JSON/字节 SHA-256 实现收敛到 `backend/src/stratgauge/ml/_hashing.py`；将 8 处重复的 UTF-8/LF 原子文本写入收敛到 `backend/src/stratgauge/_files.py`，保留调用方的目录创建、领域异常和字节写入边界。前端统一 `api/client.ts` 的 `ApiClientError`/`requestJson`，修复自定义 base URL 解析；抽出 `CalendarInput`、主板行情同步面板和同步 API 合约类型；删除全仓无消费者的 `api/provider.tsx`、`useApiResource.ts` 和 `decisionDescription`。
- 审计结论：后端 API、CLI、数据源适配器、历史数据模型和 `legacy-unknown` 兼容路径均有公开入口、测试引用或历史证据约束，本轮未删除；`App.tsx` 工作台/管理/持仓大组件、`SelectionService` 和 ML 大训练模块列为后续分批拆分项。
- 实际验证：`cd backend && uv run ruff format --check .` 通过（221 个文件已格式化）；`cd backend && uv run ruff check .` 通过；`cd backend && uv run mypy` 通过（222 个源文件）；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -q` 通过（339 项，1 项跳过；仅出现 Starlette/httpx 弃用警告）；`cd frontend && npm run typecheck && npm run lint && npm run build` 通过（Vite 转换 1875 个模块）；`git diff --check` 通过，仅有既有 LF/CRLF 工作树换行警告。
- 遗留问题：本轮整理尚未提交或推送；`.agents/` 未跟踪且未纳入。浏览器视觉回归、未成交/部分成交独立前端入口、同步进度轮询/取消操作以及大组件/综合服务的进一步拆分仍待后续小步实施。

### 2026-08-14 主板收盘行情同步诊断 API/UI

- 完成内容：主板收盘同步 API 新增逐 session 的日线/复权因子阶段诊断，返回状态、覆盖率、预期/实际证券数、质量问题、中文处理建议、快照 ID 和 `quality_run_id`，并用 `research_ready` 标记是否可进入研究。质量拒绝继续返回 HTTP 200 但明确为不可用快照；供应商、存储、前置条件和未知异常返回稳定 code、中文原因/建议且不泄露凭据。前端同步面板展示阶段证据和行动建议，区分完整成功与含未批准数据的完成状态，明确未批准数据不会进入研究；窄屏改为单列并允许长证据 ID 换行。
- 文档：更新 `docs/使用文档.md` 的 API/UI 诊断和排错说明；更新 `docs/current-state.md` 当前事实；在 `docs/decisions.md` 记录质量拒绝边界、中文错误契约及未实施的 Web UX 建议。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests/api -p 'test_*.py'` 47 项通过；`cd backend && uv run ruff check src/stratgauge/api/market_sync.py src/stratgauge/api/app.py tests/api/test_market_sync.py` 通过；`cd backend && uv run mypy src/stratgauge/api/market_sync.py src/stratgauge/api/app.py tests/api/test_market_sync.py` 通过；`cd frontend && npm run typecheck` 通过；`cd frontend && npm run lint` 通过；`cd frontend && npm run build` 通过（Vite 转换 1874 个模块）；`git diff --check` 通过，仅提示既有 `backend/src/stratgauge/api/market_sync.py` 的 LF/CRLF 工作树换行警告。
- 遗留问题：尚未增加同步诊断的浏览器视觉回归/Storybook 测试、未成交/部分成交的独立前端录入入口、同步阶段进度轮询或取消操作；`git push` 因 `master` 未配置远程仓库而阻塞；未跟踪 `.agents/` 和本轮改动均未自动提交或推送。

### 2026-08-14 合并手工订单与成交录入

- 完成内容：将持仓/交易记录中旧手工台账的“录入订单”和“录入成交”合并为一个“录入订单与成交”表单，改用 `/v1/manual-trades/confirm` 原子写入；自动生成订单 ID 与成交 ID，卖出增加卖出原因，复盘表单保持独立；桌面端录入区域改为两列布局。
- 文档：同步更新 `docs/使用文档.md`、`docs/current-state.md` 和 `docs/decisions.md`，说明统一录入字段、原子接口和未成交订单 API 的兼容边界。
- 实际验证：`cd frontend && npm run typecheck && npm run lint && npm run build` 通过；`cd backend && PYTHONPATH=src uv run python -m unittest tests.api.test_portfolio -v` 6 项通过；`git diff --check` 通过。
- 遗留问题：旧手工台账仍位于持仓/交易记录的折叠区域；未成交/部分成交仅能通过后端独立 API 记录，尚未提供单独的前端入口。

### 2026-08-11 统一启动器

- 完成内容：新增根目录 `start.py` 和 Windows `start.cmd`；统一并发启动 backend Uvicorn 与 frontend Vite，固定工作目录和命令，日志使用 `[backend]`/`[frontend]` 前缀；启动/运行失败传播非零状态，Ctrl+C/终止时清理 Uvicorn reload、pnpm、Vite 及其子进程。新增 `backend/tests/test_start_launcher.py` 5 项测试。
- 实际验证：`uv run ruff check ../start.py tests/test_start_launcher.py` 通过；`uv run mypy ../start.py tests/test_start_launcher.py` 通过；`uv run python -m unittest tests.test_start_launcher -v` 5 项通过；Windows 端到端健康检查中后端 `/healthz` 和前端 `http://[::1]:5173/` 均返回 200，退出后 8000 和 5173 端口均释放；`py_compile`、`git diff --check` 通过。
- 说明：Windows 真实终止返回系统相关非零码 `149`，但两个服务均已清理；前端 Vite 在本机绑定 IPv6 loopback，因此健康检查使用 `[::1]`，用户文档保留 Vite 输出的 `localhost` 地址。未执行 git add/commit/push。

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

### 2026-08-10 JSONL schema 版本

- 完成内容：每条 `SelectionDatasetRow` 增加 `schema_version=selection-dataset-v1`，保留待定、未入场和未解决样本，便于后续训练脚本拒绝未知列版本。
- 验证：后端 100 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过。

### 2026-08-10 数据集按日期切分

- 完成内容：新增 `split_tdx_dataset.py` 和 `selection_split.py`，按 `selection_date` 与显式 train/validation 边界生成不随机、不覆盖的三份 JSONL，并保存源文件 SHA-256、schema 版本和各分区行数 manifest。
- 验证：后端 103 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过。

### 2026-08-10 管理页统计筛选

- 完成内容：新增 `GET /v1/data-management/stats`，支持公式版本、来源、选股日期范围、最新人工决策、结果政策和生命周期筛选；数据管理页增加原生日期/选择控件和样本、待定、胜率、平均净收益概览。
- 验证：后端 105 项测试通过（1 项环境相关跳过）；Ruff、MyPy 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 公式创建入口归位

- 完成内容：将公式创建入口放到“数据管理”公式目录，研究工作台只负责选择已登记公式；创建成功后同步刷新两个工作区的公式列表。
- 验证：前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 README 操作手册

- 完成内容：在 README 增加启动、公式创建、收盘后导出、人工标注、成熟任务、Windows Task Scheduler、统计筛选、数据集导出/时间切分和运行原理说明。
- 验证：完成 Markdown 差异检查；本次仅修改文档，未重复运行代码测试。

### 2026-08-10 管理批次详情操作反馈

- 完成内容：保留公式和研究证据不可硬删除的约束，使用停用/归档代替删除；批次“查看详情”现在会选择批次并平滑滚动到详情面板，避免详情位于页面下方时看起来没有响应。
- 验证：前端 `corepack pnpm typecheck`、`build`、`lint` 通过。

### 2026-08-10 待完成功能任务分解

- 完成内容：将未完成工作细分为 P3 机器学习准入、训练样本审计、历史行情补齐、特征契约、防泄漏测试、基线/LightGBM、走步验证、校准、模型注册、影子推理和 Web 展示；新增 P4 可靠研究和推荐工作台任务。
- 验证：完成 `PLANS.md` 结构检查；本次仅修改计划和会话记录，未运行代码测试。

### 2026-08-10 实现机器学习数据准入审计

- 完成内容：新增 `selection_ml_audit.py` 和 `audit_selection_ml_readiness.py`；审计 `selection-dataset-v1` 的策略冻结声明、样本量、目标类别、追踪政策、重复候选、待定/未解决状态和结果快照溯源；未知或不完整训练证据返回非零退出码，不启动训练。
- 验证：新增审计测试 6 项通过；相关数据集/切分测试共 11 项通过；Ruff format/check、MyPy 通过。
- 遗留问题：特征工程、基线模型、LightGBM、走步验证、模型注册和 Web 预测尚未实现。

### 2026-08-10 历史行情补齐基础

- 完成内容：新增 `history_backfill.py` 和 `backfill_selection_history.py`；按显式升序交易日历截取选股日前最长回看窗口，候选自动来自批次，基准/行业标的要求显式 `CODE.EXCHANGE`；逐证券通过统一适配器和质量门抓取，并报告缺失证券、缺失交易日、历史窗口不足、质量失败和 provider 失败。新增 `SnapshotStore.load_bars`，默认拒绝读取未批准快照；报告和 rejected 快照不覆盖已有证据。
- 验证：`PYTHONPATH=src uv run python -m unittest tests.data_pipeline.test_history_backfill tests.data_pipeline.test_store -v`（9 项通过）；`uv run ruff check src/stratgauge/data_pipeline/history_backfill.py src/stratgauge/data_pipeline/store.py scripts/backfill_selection_history.py tests/data_pipeline/test_history_backfill.py tests/data_pipeline/test_store.py`、`uv run mypy src tests scripts` 通过。
- 遗留问题：特征契约、`available_at` 防泄漏测试、基线模型、LightGBM、走步验证、模型注册和 Web 预测尚未实现；真实交易日历和基准/行业映射仍需由研究配置显式提供。

### 2026-08-10 实现当前可独立完成的研究基础

- 完成内容：新增版本化确定性特征契约和计算引擎（收益、波动率、流动性、均线距离、基准相对强弱），严格拒绝选股日之后或 `available_at` 晚于 `as_of` 的数据；新增 `selection-feature-dataset-v1` 解析、按选股日期切分和离线常数概率基线训练 CLI；新增批准来源约束的时间点股票池快照；新增策略版本生命周期事件账本；新增手工订单、成交、持仓推导、复盘记录及基础 API。
- 验证：`PYTHONPATH=src uv run python -m unittest discover -s tests -v`（135 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过。
- 遗留问题：用户真实策略参数、完整股票主数据和历史成分、AKShare 网络、LightGBM、走步验证、概率校准、模型注册/晋升、用户覆写仍未实现。

### 2026-08-10 理论回测报告和时间点股票搜索

- 完成内容：新增 `backtest/report.py` 与 `report_backtest.py`，从不可变 `BacktestResult` 生成内容寻址的理论报告，按策略版本、成本政策、显式市场状态和入场日期分组；新增 `/v1/universe/securities`，只在请求 `as_of` 时已知的股票池快照中按代码/名称搜索。
- 约束：报告不混入推荐或实际成交；市场状态不自动推断；无时间点股票池时搜索返回结构化错误；当前不提供完整股票主数据补齐。
- 验证：`PYTHONPATH=src uv run python -m unittest discover -s tests`（139 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过。
- 遗留问题：P4-03 尚未接入 Web 报告查询；完整历史成分、用户覆写、真实策略参数和机器学习高级阶段仍未实现。

### 2026-08-10 切换 Tushare Pro 自动数据源

- 完成内容：新增 `TushareAdapter`，对接 Tushare `daily` 不复权日线；新增 `TushareReferenceClient`/`TushareReferenceStore`，对接 `stock_basic` 和 `trade_cal`；新增 `sync_tushare_universe.py`、`sync_tushare_calendar.py`；成熟、历史补数、任务调度、API 和真实验证脚本加入 Tushare，新增流程默认主源；前端行情源选项同步更新。
- 数据边界：`TUSHARE_TOKEN` 只从环境变量读取；成交量由手转换为股，成交额由千元转换为元；参考响应和股票池按内容保存，交易日历仍是显式 session 集合。
- 实际验证：未设置 `TUSHARE_TOKEN`，未执行真实 Tushare API 调用；只验证 HTTPS 端点可连接和注入响应契约。
- 验证：后端 `uv sync --extra live-data` 后执行 `PYTHONPATH=src uv run python -m unittest discover -s tests`（144 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过；`uv lock` 成功。
- 遗留问题：设置 Token 后仍需运行 Tushare/BaoStock 固定区间质量门和交叉校验；Tushare 积分/限流/修订时间需进一步确认。

### 2026-08-10 走步验证和模型注册基础

- 完成内容：增强 `walk_forward.py` 的最终测试起点隔离、折排序和输入哈希校验；新增 `validate_walk_forward.py`，读取版本化特征 JSONL 和显式折 JSON，输出不可覆盖报告；新增走步边界、清除期、多政策、重复行、单类别和最终测试隔离测试。
- 完成内容：新增 `ModelRegistryStore`、`register_selection_model.py`、`transition_selection_model.py` 和 `ModelLifecycle`；模型、评估报告、训练 manifest 内容复制并校验哈希；支持 challenger/shadow/active/retired、人工 promote、rollback 和追加操作者审计事件。
- 实际验证：走步 CLI 临时夹具成功输出 `selection-walk-forward-v1` 报告，最终测试行只计数未参与折评估；模型注册/transition CLI 临时夹具完成注册、shadow、active 和工件校验；未执行真实模型或外部数据评分。
- 验证：后端 `PYTHONPATH=src uv run python -m unittest discover -s tests`（152 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过。
- 遗留问题：P3-07 LightGBM、完整走步成交/收益指标、概率校准、冠军/挑战者门禁和 Web 展示仍未实现；影子评分目前只支持离线常数基线 CLI，尚未自动接入新批次；不把常数基线或未校准分数称为胜率。

### 2026-08-10 独立影子评分底座

- 完成内容：新增 `shadow.py`、`score_selection_shadow.py` 和 `ShadowScoreStore`；只接受不含客观标签的候选/`FeatureSnapshot`，只允许 shadow 生命周期模型；输出独立不可变评分和报告，记录模型、特征、输入快照、追踪政策、评分时间、失败项，并固定标记未校准且不用于决策。
- 完成内容：训练基线 manifest 增加模型类型、随机种子、标签字段和 Python 运行环境；模型注册加载前继续校验工件哈希和元数据。
- 实际验证：影子评分 CLI 临时夹具成功注册模型、进入 shadow、生成一条评分报告；过期特征作为候选级失败保留在报告，不写入评分。
- 验证：后端 `PYTHONPATH=src uv run python -m unittest discover -s tests`（156 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过。
- 遗留问题：影子评分尚未接入导入批次/API/前端；LightGBM、概率校准模型和完整走步收益指标仍未实现。

### 2026-08-10 模型比较门禁和人工晋升

- 完成内容：新增 `evaluation.py` 的版本化评估证据；基线 `metrics.json` 改为带模型/特征/政策/源哈希和指标字典的标准报告。
- 完成内容：新增 `comparison.py` 和 `compare_selection_models.py`；比较策略显式声明指标方向、绝对边界、最大相对退化和校准要求；比较报告内容寻址、不可覆盖并记录候选/冠军评估哈希。
- 完成内容：`ModelRegistryStore.promote` 现在必须引用通过的比较报告；报告被篡改、来自未来、候选/冠军/政策/评估哈希不匹配或 active 冠军未先人工退役时拒绝晋升；回滚入口保持不变。
- 实际验证：比较 CLI 临时夹具成功生成通过报告并完成人工 active；单元测试覆盖缺失指标、相对退化、校准门禁、报告篡改和生命周期顺序。
- 遗留问题：当前只有常数基线的 Log Loss/Brier 等基础指标；信号量、净收益、回撤、换手、特征稳定性、最差折表现和真正概率校准仍需合格真实样本与完整走步数据。

### 2026-08-10 Web 视觉和响应式布局收敛

- 完成内容：依据项目新增 design-system Skill，集中定义浅色语义颜色、间距、圆角、阴影和层级 token；将按钮、卡片、输入框、选择器、提示、徽章和对话框基础视觉提取为语义组件类。
- 完成内容：异步保存/执行按钮在 `data-saving=true` 时保持控件不透明并显示等待指针；对话框关闭按钮改用明确的 `focus-visible` 焦点环，避免状态切换造成大块区域闪烁。
- 完成内容：页面旧的 HSL 颜色声明已替换为项目级语义 token，保留浅色主题和现有状态语义，不新增暗色模式或依赖。
- 完成内容：页面容器扩展至 1280px，Hero 标题避免桌面孤字换行；状态卡增加主色边界，研究/管理区保持清晰卡片层级；移动端标题、表单标题区和数据栅格转为可读的单列布局。
- 实际验证：使用 `http://localhost:5175/` 生成桌面和 390px 视口截图；确认桌面标题单行、移动端无横向溢出、主要按钮和输入控件焦点/尺寸稳定；通过 Chrome DevTools 390px 指标确认 `document.scrollWidth=375`、无横向溢出，标题和状态卡均在容器内。
- 验证：前端 `corepack pnpm typecheck`、`corepack pnpm build`、`corepack pnpm lint` 通过。
- 遗留问题：页面仍是浅色主题，尚未增加完整视觉回归测试或 Storybook；不新增暗色模式，除非产品需求改变。

### 2026-08-10 A 股主色和日期/下拉控件优化

- 完成内容：将主要操作色从绿色改为 A 股涨势红；盈利/候选徽章同步使用涨势红，风险错误仍使用独立 danger 语义，信息与焦点保持蓝色。
- 完成内容：继续使用原生 `date`/`datetime-local` 控件，统一 44px 高度、浅色配色、日历图标 hover 和焦点状态；Radix Select 的触发器、弹层、选项、滚动按钮和分隔线改为语义组件样式。
- 验证：前端 `corepack pnpm typecheck`、`corepack pnpm build`、`corepack pnpm lint` 通过；使用 `http://localhost:5175/` 实际展开下拉弹层并生成桌面截图，确认红色主按钮、日期图标和选项高亮显示正常；390px 视口 `scrollWidth=390`，无横向溢出；`git diff --check` 通过。

### 2026-08-10 Tushare 主源和参考数据验证

- 完成内容：发现 Tushare SDK 默认旧 HTTP 端点在当前代理下返回 502 且静默为空；新增官方 HTTPS Data API 客户端，保留 HTTP/API 错误和可重试语义，移除不再使用的 Tushare SDK 可选依赖；交易日历同步失败时改为结构化错误输出。
- P1-08：`000001.SZ` 的 2024-01-02—2024-01-05 Tushare `daily` 返回 4 行并通过质量门，快照为 `snapshot-ef353d8c0f609437e9e3`。
- P1-09：同一区间 Tushare 与 BaoStock 均返回 4 行，OHLCV/成交额交叉校验通过。
- REF-01：确认研究范围只需要沪深主板；`stock_basic` 现在请求 `market=主板` 并本地限制 SSE/SZSE，排除北交所、创业板和科创板。此前宽范围验证快照已清理，后续已重新生成 3193 条主板快照；`trade_cal` 因当前 Token 频率限制未完成，目标日历文件未创建。
- 验证：后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -v`（163 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过；Token 内容扫描命中 0 个文件。
- 遗留问题：等待 Tushare `trade_cal` 配额窗口后重试；阶段 0 尚未最终退出。本轮代码和文档改动尚未提交。

### 2026-08-10 参考数据重试

- 完成内容：真实执行主板 `stock_basic` 同步，生成 3193 条记录；市场全部为 `主板`，交易所分布为 SSE 1699 条、SZSE 1494 条。参考快照为 `tushare-reference-278819ba7c8d5c92fe37`，股票池快照为 `universe-d8b8d8f30a3f50c43fd9`。
- 验证：真实执行 `sync_tushare_calendar.py --start-date 2024-01-01 --end-date 2026-08-10 --exchange SSE` 返回 Tushare 错误 40203（当前 Token 限制 5 次/月），退出码 2，交易日文件未创建；确认没有写入半成品文件。
- 遗留问题：`trade_cal` 配额恢复前不能生成交易日文件或标记阶段 0 完成；本轮代码和文档改动尚未提交。

### 2026-08-10 趋势交易 A1 买入规则

- 完成内容：新增不可变 `EntryExecutionPolicy` 和 `entry-limit-chase-v1`；收盘后信号在后续 3 个交易日有效，支持日线触达近似、开盘价成交和 1% 追价上限；执行窗口由显式交易日历生成。
- 完成内容：回测结果和成交记录携带 `entry_policy_id`；高开超过追价上限时跳过当日而不是终止整个有效窗口；低开时按实际开盘价成交。
- 验证：新增执行窗口和低开成交测试；回测相关 11 项测试通过，Ruff/MyPy 通过。
- 遗留问题：A2 胜负、持有期、费用和止盈止损规则尚未冻结；本轮代码和文档改动尚未提交。

### 2026-08-10 收窄股票池到沪深主板

- 完成内容：根据用户确认，`stock_basic` 固定请求 `market=主板`，并再次限制交易所为 SSE/SZSE；过滤创业板、科创板和北交所。同步 manifest 记录市场范围。
- 验证：新增参考数据测试覆盖主板保留和其他板块过滤；后端 163 项测试通过，Ruff/MyPy 通过。
- 遗留问题：Tushare `trade_cal` 仍需等待当前 Token 的 5 次/月频率窗口后重新执行真实验证；主板 `stock_basic` 已完成真实同步，此前宽范围本地快照已清理。

### 2026-08-10 趋势交易 A2 胜负和成本规则

- 完成内容：默认固定结果政策为 `outcome-fixed-10-net-positive-v1`；持有 10 个可交易日后收盘退出，不启用止盈止损，净收益大于/等于/小于 0 分别为胜/平/负。
- 完成内容：未入场和行情不足继续分别记录 `no_entry`/`unresolved`；结果保存 `outcome_policy_id`；JSONL 数据集升级为 `selection-dataset-v2`。
- 完成内容：默认成本政策升级为 `a-share-main-board-cost-v1`，按 100 股整手向下取整，佣金 0.03%（最低 5 元）、卖出印花税 0.05%、滑点 0；不足一手拒绝完成结果。
- 验证：后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（170 项通过，1 项环境相关跳过）；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy src tests scripts` 通过。
- 遗留问题：下一项全市场收盘后增量行情尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 全市场单日增量行情同步

- 完成内容：新增 `MarketDailyRequest`、Tushare 单日全市场 `daily(trade_date=...)` 适配、`market_daily_sync.py` 和 `sync_tushare_daily.py`；仅保留显式沪深主板股票池中的证券。
- 完成内容：快照清单记录 `request_kind`/`universe_snapshot_id`；已批准的交易日/股票池组合幂等跳过；缺失证券为警告，覆盖率低于 90% 拒绝发布；非交易日安全跳过。
- 验证：全市场同步、过滤换算、质量覆盖率、拒绝重抓和幂等测试通过；后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（176 项通过，1 项环境相关跳过）；Ruff/MyPy 通过；显式 CLI 非交易日返回 `status=skipped`。
- 遗留问题：`trade_cal` 真实文件仍受 Tushare 配额限制；多日调度、复权因子、全量历史补齐和条件胜率查询尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 全市场多交易日增量编排

- 完成内容：`sync_tushare_daily.py` 支持交易日历范围，逐日复用单日同步；已批准快照幂等跳过；拒绝乱序/重复日期和未来股票池。
- 验证：新增范围幂等、重复日期、未来股票池测试；后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（178 项通过，1 项环境相关跳过）；Ruff/MyPy 通过。
- 遗留问题：`trade_cal` 配额恢复前不能执行真实多日行情同步；复权因子、全量历史补齐和条件胜率查询尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 交易日历增量缓存

- 完成内容：新增 `TradeCalendarCache` 和覆盖范围 manifest；已覆盖范围不请求 Tushare，左/右缺口增量查询，保存 `trade_cal` 参考响应快照并原子更新交易日文件。
- 完成内容：`sync_tushare_calendar.py` 改为可重复执行；检测 manifest/文本篡改、缺失、重复和乱序时阻止消费。
- 验证：新增缓存缺口、幂等和篡改拒绝测试；后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（180 项通过，1 项环境相关跳过）；Ruff/MyPy 通过。
- 遗留问题：Tushare `trade_cal` 配额恢复前仍不能完成真实初始填充；复权因子、全量历史补齐和条件胜率查询尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 复权因子与研究特征输入

- 完成内容：新增 `AdjustmentFactor` 契约、Tushare `adj_factor` 适配、同日已批准日线绑定、因子快照存储/质量门/幂等同步；全市场日线 CLI 在日线批准后继续同步复权因子。
- 完成内容：新增研究专用前复权变换；`compute_daily_features` 可显式接收因子和因子快照血缘，原始未复权行情、成交量和成交额不修改。
- 验证：新增因子适配、覆盖率、同日快照绑定、幂等、因子存储和前复权特征测试；后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（187 项通过，1 项环境相关跳过）；Ruff/MyPy 通过。
- 遗留问题：`trade_cal` 配额恢复前仍不能完成真实初始填充；历史批量因子补齐、自动成熟和条件胜率查询尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 已批准行情自动成熟队列

- 完成内容：成熟任务增加 `approved_snapshots` 模式；新增 `queue_selection_maturity_tasks.py`，按显式交易日历、固定股票池和已批准全市场快照创建幂等任务。
- 完成内容：快照模式执行直接重放本地行情，校验快照状态、来源、交易日、创建时间和股票池，不重新请求供应商；旧 provider 模式保持兼容。
- 验证：新增连续交易日门槛、队列重复扫描和快照模式供应商零调用测试；后端 `PYTHONPATH=src uv run python -m unittest discover -s tests -q`（188 项通过，1 项环境相关跳过）；Ruff/MyPy 通过。
- 遗留问题：`trade_cal` 配额恢复前仍不能完成真实初始填充；历史批量因子补齐、条件胜率和买入价分析查询尚未实现；本轮代码和文档改动尚未提交。

### 2026-08-10 条件胜率与历史入场价分析

- 完成内容：新增 `selection_analysis.py` 和 `GET /v1/selection-batches/{batch_id}/candidates/{candidate_id}/analysis`；按同公式、早于当前选股日且在批次导出时间前已知的结果构建历史队列；支持追踪政策筛选。统计默认至少 100 个完成样本才输出胜率，入场价分析只返回历史实际入场价的描述统计；研究页候选卡片增加按需分析面板。
- 验证：`cd backend && uv run ruff format --check . && uv run ruff check . && uv run mypy src tests scripts`；`PYTHONPATH=src uv run python -m unittest discover -s tests -q`（190 项通过，1 项环境相关跳过）；`cd frontend && corepack pnpm typecheck && corepack pnpm build && corepack pnpm lint`；`git diff --check`，均通过。
- 遗留问题：`trade_cal` 真实填充仍受 Tushare 当前 Token 频率限制；历史批量因子补齐、LightGBM、概率校准和推荐工作台仍未实现。

### 2026-08-10 历史批量复权因子补齐

- 完成内容：新增批量因子同步报告和 `backfill_tushare_adjustment_factors.py`；显式读取交易日历范围，只处理已有已批准全市场日线快照；缺少日线时不请求供应商并报告阻断，因子质量拒绝、供应商失败和已批准/跳过状态均逐日记录。
- 验证：后端 193 项测试通过（1 项环境相关跳过）；Ruff、MyPy、前端 typecheck/build/lint 和 `git diff --check` 通过；CLI `--help` 检查通过。
- 遗留问题：真实 `trade_cal` 和批量 Tushare 因子运行仍受当前 Token 配额限制；LightGBM、概率校准和推荐工作台仍未实现。

### 2026-08-10 LightGBM 候选模型

- 完成内容：新增 `ml` 可选依赖、`lightgbm_training.py` 和 `train_selection_lightgbm.py`；固定确定性 LightGBM 配置，复用特征 JSONL 和时间切分，输出模型文本、未校准分区指标、预测明细及不可覆盖 manifest；默认路径不加载 LightGBM，不影响原始策略流程。
- 验证：默认后端 195 项测试通过（1 项环境相关跳过）；Ruff、MyPy、CLI 帮助检查通过；显式 `uv run --extra ml` 安装 LightGBM 4.7.0 后完成真实小样本训练与工件写入；前端 typecheck/build/lint 和 `git diff --check` 通过。
- 遗留问题：完整走步成交/收益指标、概率校准、LightGBM 模型注册/影子评分、批次自动接入和 Web 展示仍未实现。

### 2026-08-10 走步验证指标补齐

- 完成内容：走步报告升级为 `selection-walk-forward-v2`；`validate_walk_forward.py` 增加可选 `--outcomes`，按候选/选股日关联客观结果并输出结果覆盖率、执行率、平均净收益、最大回撤、换手和市场状态平均收益；最终测试行仍不参与折评估，结果源哈希写入报告。
- 验证：后端 196 项测试通过（1 项环境相关跳过）；Ruff、MyPy、CLI 帮助检查、前端 typecheck/build/lint 和 `git diff --check` 通过。
- 遗留问题：概率校准、模型批次自动接入、Web 评分和完整模型运营仍未实现。

### 2026-08-10 概率校准

- 完成内容：新增 `calibration.py` 和 `calibrate_selection_probabilities.py`；实现 validation-only PAVA Isotonic 校准、test 隔离应用、默认 100 样本/双类别/双分数门槛、前后 Log Loss/Brier、校准模型和不可覆盖输出。
- 验证：后端 199 项测试通过（1 项环境相关跳过）；Ruff、MyPy、CLI 帮助检查、前端 typecheck/build/lint 和 `git diff --check` 通过。
- 遗留问题：模型注册、批次自动接入、Web 评分、影子运行和完整不确定性展示仍未实现。

### 2026-08-10 LightGBM 影子推理

- 完成内容：模型注册扩展支持 baseline/LightGBM 元数据和运行时工件；影子评分支持 LightGBM 单候选概率、可选同源 Isotonic 校准分数、工件哈希和校准器一致性校验；新增 `score_selection_shadow.py --calibration`。
- 验证：后端 200 项测试通过（1 项环境相关跳过）；Ruff、MyPy、注册/影子 CLI 帮助检查、前端 typecheck/build/lint 和 `git diff --check` 通过。
- 遗留问题：模型批次自动接入、Web 评分和完整模型运营仍未实现。

### 2026-08-10 模型证据 API 与前端展示

- 完成内容：新增只读模型证据服务和 `GET /v1/selection-batches/{batch_id}/candidates/{candidate_id}/model-scores`；候选页按需加载模型影子证据，展示模型版本、生命周期、原始/校准分数、样本量、适用日期和失败原因，固定标记不具权威性且不用于决策。
- 验证：后端 202 项测试通过（1 项环境相关跳过）；Ruff、MyPy、API 回归、前端 typecheck/build/lint 和 `git diff --check` 通过。
- 遗留问题：模型批次自动接入、完整推荐工作台和阶段 0 `trade_cal` 真实配额验证仍未完成。

### 2026-08-10 交易日历本地复用收口

- 完成内容：`backfill_selection_history.py` 在未显式提供 `--sessions-file` 时改用 `TradeCalendarCache`，默认持久化到 `data/calendars/trade-cal-{exchange}.txt` 及 manifest；覆盖范围内重复运行不再请求 Tushare，范围缺口才增量请求并保存参考快照。
- 验证：交易日历缓存测试、Ruff、MyPy 和 `git diff --check` 通过。

### 2026-08-10 P4 历史股票池、推荐工作台与模型漂移

- 完成内容：股票元数据新增上市日期，Tushare 股票池 CLI 支持 L/D/P 历史状态按 `--as-of` 过滤；新增特征漂移 PSI/均值变化报告、不可变存储和 `report_selection_drift.py`；新增推荐证据聚合 API/前端、active 模型显式评分入口和手工持仓订单/成交/复盘只读台账。
- 验证：后端 210 项测试通过（1 项环境相关跳过）；Ruff、MyPy、前端 typecheck/build/lint 通过。
- 遗留问题：完整历史成分连续档案、退市生命周期主数据、证券用户覆写/更丰富复盘录入仍未完成；Tushare `trade_cal` 真实配额验证仍被 40203 阻塞。

### 2026-08-10 股票主数据用户覆写

- 完成内容：新增独立、追加式 `SecurityOverride` 存储和 `/v1/universe/overrides`；时间点股票搜索只在显示层应用有效的用户名称覆写，不修改 Tushare 原始参考快照或股票池成员资格。
- 验证：股票搜索/覆写 API、Universe 快照测试、Ruff 和 MyPy 通过。
- 遗留问题：完整股票主数据持续历史档案和订单/成交/复盘录入界面仍未完成。

### 2026-08-10 持仓台账录入工作台

- 完成内容：持仓工作区新增手工订单、成交和复盘录入表单；订单/成交/复盘仍分别写入本地追加式账本，持仓继续只由成交推导，不连接券商。
- 验证：前端 typecheck、build、lint 通过；后端持仓 API 回归测试通过。
- 遗留问题：完整股票主数据历史档案仍属于 P4-01/P4-05 未完成项。

### 2026-08-11 证券主数据和双时间点搜索

- 完成内容：新增 `SecurityMasterRecord`、`SecurityMasterSnapshot`、不可变 `SecurityMasterStore`；Tushare `stock_basic` 扩展标准化元数据字段，历史同步保存 L/D/P 记录并把 `membership_date`、主数据快照 ID 写入股票池快照。
- API/前端：新增 `GET /v1/universe/master?effective_date=...&known_at=...`，查询严格阻止未来有效日期；持仓工作区新增按代码、名称、行业和控制人搜索证券主数据的时间点表单。
- 验证：后端 211 项测试通过（1 项环境相关跳过）；Ruff、MyPy、CLI help、前端 typecheck/build/lint 通过。
- 遗留问题：Tushare `trade_cal` 真实填充仍受 40203 月度频率限制；连续历史主数据需要在配额允许后按观察时间运行同步。

### 2026-08-11 交易日历配额重试

- 实际验证：`TUSHARE_TOKEN` 已设置，显式执行 `sync_tushare_calendar.py --start-date 2024-01-01 --end-date 2026-08-11 --exchange SSE --output data/calendars/trade-cal-SSE.txt` 仍返回 Tushare 错误 40203（月度 5 次频率限制），退出码 2，未生成目标日历文件。
- 结论：REF-01/P1-10 继续保持阻塞；不使用自然日或其他来源伪造交易日历。

### 2026-08-11 备用交易日历缓存

- 完成内容：新增 BaoStock + AKShare 交易日历源、原始响应快照、双源一致性校验和 `sync_backup_calendar.py`；缓存 manifest 记录 `source_providers` 与来源快照 ID，不把备用来源冒充 Tushare `trade_cal`。
- 实际验证：`cd backend && export PYTHONPATH=src && uv run python scripts/sync_backup_calendar.py --root data --start-date 2024-01-01 --end-date 2026-08-11 --exchange SSE --output data/calendars/trade-cal-sse.txt` 成功生成 631 个交易日，缓存 ID 为 `calendar-5dc7698e9739a85ac100`；BaoStock 与 AKShare 差异为 0。
- 幂等验证：相同命令再次执行返回 `skipped=true`、`fetched_ranges=[]`、631 个交易日；缓存文件行数为 631。
- 代码验证：后端 216 项测试通过、1 项环境相关跳过；Ruff format/check、MyPy、备用脚本 `--help` 通过。前端本轮未修改，未重复运行前端检查。
- 遗留问题：Tushare `trade_cal` 原生接口仍因 40203 未验证；备用缓存已可供历史补数、全市场同步和成熟队列复用。

### 2026-08-11 全市场单日行情与复权因子真实验证

- 实际验证：使用备用交易日历、已批准主板股票池 `universe-d8b8d8f30a3f50c43fd9`，执行 `sync_tushare_daily.py --session-date 2026-08-10 --calendar-file data/calendars/trade-cal-sse.txt --universe-snapshot-id universe-d8b8d8f30a3f50c43fd9 --universe-root data --market-root data/market --minimum-coverage 0.90`。
- 结果：Tushare `daily` 返回 3193/3193 行、覆盖率 100%，快照 `snapshot-844cde11ae0c5b85f937` 已批准；`adj_factor` 返回 3193/3193 行、覆盖率 100%，快照 `snapshot-b42e9d0261edad6fa089` 已批准。
- 幂等验证：相同命令再次执行，日线和复权因子均返回 `skipped=true`，未重复请求供应商。
- 遗留问题：尚未为候选批次自动组装特征快照并接入模型批次评分；Tushare `trade_cal` 原生端点仍因 40203 未完成验证。

### 2026-08-11 交易日历阻塞状态更正

- 更正：`backend/data/calendars/trade-cal-sse.txt` 已由 BaoStock + AKShare 交叉校验生成，覆盖 631 个交易日，且已被全市场行情同步成功消费；交易日历不存在运行阻塞。
- 保留事项：Tushare `trade_cal` 仍未完成独立供应商端点验证，原因是 40203 月度频率限制；该事项降级为 REF-01 的供应商溯源补验，不再阻塞阶段 0 或后续本地数据流程。

### 2026-08-11 前端错误提示和日期选择器体验

- 完成内容：将研究、数据管理和持仓工作区的重复错误提示收敛为可关闭的吸顶 `ErrorAlert`；日期/日期时间字段收敛为 `CalendarInput`，增加统一日历按钮、清空按钮、焦点反馈和日期范围约束；移除旧的日期专用 CSS 和重复提示 JSX。
- 实际验证：使用捆绑 Node.js 运行前端 `pnpm typecheck` 和 `pnpm build`，均通过；`git diff --check` 通过。通过本地页面实测桌面布局和 390px 移动视口，移动端 `document.documentElement.scrollWidth=375`、`clientWidth=375`，无横向溢出；日期按钮可聚焦对应输入框，数据管理页显示日期范围辅助说明。
- 环境记录：受限 Windows shell 中 `corepack` 不可用且 Vite 启动子进程会返回 `spawn EPERM`；改用工作区捆绑 Node.js/Pnpm，并在受控权限下完成构建和页面检查。
- 遗留问题：无本次前端改动新增的功能阻断；Tushare `trade_cal` 原生端点的既有供应商溯源补验仍按当前状态文档保留。

### 2026-08-11 文件上传控件体验

- 完成内容：将通达信文件 input 改为可视化上传卡片，增加上传图标、支持格式说明、文件名和文件大小展示、重新选择按钮及清除操作；保留原生文件 input、键盘可达性、原有格式限制和导入逻辑。
- 实际验证：前端 `pnpm typecheck`、`pnpm lint` 通过；通过本地页面在 390px 视口验证 `scrollWidth=375`、`clientWidth=375`，长文件名可换行；选择文件后“更换文件”和“清除已选文件”出现，清除后文件 input 值恢复为空；`git diff --check` 通过。
- 遗留问题：无本次上传控件改动新增的功能阻断。

### 2026-08-11 设计一致性和 Python 未使用代码复核

- 前端：持仓订单/成交/复盘表单补齐可见标签和稳定 `id`，原生交易所/方向选择器统一使用 `native-select`；保存时按钮显示“保存中…”并标记 `aria-busy`；`AlertDescription` 改用语义化 `<p>`。
- Python：Ruff 的 F 系列检查未发现未使用导入或局部变量；跨 `src`、`scripts`、`tests` 的定义引用扫描确认 `feature_row_from_snapshot`、`write_feature_dataset` 无仓库内引用，已从 `ml/selection_training.py` 删除；其余低频定义为测试类、协议/领域模型或 CLI/API 入口，未删除。
- 验证：后端 `unittest discover` 216 项通过、1 项跳过；Ruff、MyPy、前端 `pnpm typecheck`、`pnpm build` 和 `git diff --check` 通过。受限沙箱首次单测因 Windows 临时目录权限失败，受控权限重跑通过。
- 遗留问题：当前会话未找到实际可读取的 `design-system/SKILL.md`，本次复核以仓库已有语义 token、基础组件和历史设计决策为准；后续若补充正式技能文件，可再做一次 token/组件覆盖率审查。

### 2026-08-11 技术文档与使用文档全面更新

- 完成内容：新增 `docs/使用文档.md`，逐项说明 28 个后端 CLI 的 input、HTTP API 请求参数和前端表单字段；增加日期/代码/Decimal 输入约定、状态枚举、目录结构、故障排查和推荐操作流程。
- 行情边界：明确交易日历只在缓存覆盖范围外请求；BaoStock + AKShare 只在备用缓存缺口时同时请求并交叉校验；股票池同步每次请求 Tushare；全市场日线/复权因子只有在命中同源、同日、同股票池的 approved 快照时跳过；单票 provider 模式不预查本地快照；`approved_snapshots` 成熟任务只读本地 Parquet。
- 技术规格：重写 `docs/系统规格说明.md` 以匹配当前模块、数据契约、缓存/证据模型、API 和半手动研究边界；README 增加文档入口和远程/本地行为摘要。公式目录与策略版本登记也已在使用文档中拆开说明。
- 实际验证：`git diff --check` 通过；28 个后端脚本逐一执行 `--help` 通过；`PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q` 通过（216 项，1 项跳过）；`uv run ruff format --check .` 通过（155 个文件已格式化）；`uv run ruff check .` 通过；`uv run mypy src tests` 通过（127 个源文件）。
- 环境记录：uv 用户缓存目录在受限沙箱中无读取权限，CLI 和测试使用受控权限执行；未修改项目源码或运行真实行情网络验证。
- 遗留问题：Tushare `trade_cal` 原生端点的 40203 月度频率限制仍是既有供应商溯源补验事项；本次文档更新未新增功能阻断。

### 2026-08-11 通达信 `名称(5)` 行情表兼容回归

- 现象：上传 `81120260811.txt` 调用 `/api/v1/selection-batches` 返回 `INVALID_TDX_EXPORT`，提示第 2 行没有交易所。
- 根因：该文件是 GB18030 编码的通达信制表符行情表，但名称列为 `名称(5)`；旧识别逻辑只匹配 `名称(4)`，因此落入普通表格解析分支。
- 修复：通达信行情表识别任意 `名称(n)` 版本化表头，并保留“代码列在首列 + `涨幅%`”的结构约束；普通表格仍拒绝没有交易所或代码后缀的六位代码。
- 实际验证：针对性导入测试 9 项通过；使用用户样例实际解析出 `002773.SZ`、`001298.SZ`、`002021.SZ`、`603061.SH`、`603819.SH` 共 5 个候选。用户样例只作为外部验证输入，未复制进仓库。
- 实际验证：通达信导入针对性测试 9 项通过；使用用户样例直接调用本地 `/v1/selection-batches` 返回 HTTP 201 和 5 个正确证券标识；Ruff 检查 `backend/src`、`backend/scripts`、`backend/tests` 通过；MyPy 检查 `backend/src` 的 77 个源文件通过；受控权限下后端完整单测 230 项通过、1 项跳过。将测试目录一并纳入 MyPy 时仍有 3 个既有测试文件共 10 个类型错误，本次未修改这些无关测试类型问题。
- 遗留问题：无本次导入修复新增的功能阻断。

### 2026-08-11 前台人工标记说明和延后买入流程

- 完成内容：候选股票区域新增四种标记的可见说明和按钮悬浮说明；标记按钮保持短标签“候选/观察/放弃/无效”，辅助文案解释其含义；批次列表和批次详情将选股日期设为主视觉，策略名降为辅助信息；补充“今天选中、明天不买、后天买”的操作指引，要求后续实际成交通过持仓台账记录。
- 前端细节：修复 Badge 基础类未显式设置 `display` 的问题，统一为 `inline-flex` 并禁止标签文本换行，避免徽章出现块级拉伸效果。
- 规则边界：原始标签仍须在下一交易日前冻结，用于还原当时判断；后续成交日期、价格和数量属于手工交易台账，不覆盖原始标签。
- 实际验证：前端 `pnpm typecheck`、`pnpm lint` 通过；受控权限下 `pnpm build` 通过（Vite 处理 1874 个模块）；`git diff --check` 通过。说明面板在 640px 以下单列排列，避免移动端文字和按钮拥挤。
- 遗留问题：实际成交仍需用户在持仓台账单独录入；本次未把成交日期并入冻结标签模型。

### 2026-08-11 可复用人工评审标签 Web 切片

- 完成内容：前端评审对话框按 `position`、`pattern`、`price_volume`、`sector` 展示活跃可复用标签；支持同分类输入后创建并立即选中，提交只发送 `reusable_tag_ids`。数据管理新增标签目录，支持改名、归档和恢复；候选列表展示 Annotation 内嵌的历史标签快照。
- 文档：`docs/使用文档.md` 增加创建、选择、归档/恢复、改名和 ML 隔离说明；`docs/系统规格说明.md`、`docs/current-state.md`、`docs/decisions.md` 同步当前事实与决策。
- 实际验证：后端完整 `PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q` 通过（306 项，1 项跳过）；后端 `uv run ruff check .` 通过；`uv run mypy src` 通过（87 个源文件）；前端 `corepack pnpm typecheck`、`corepack pnpm build`、`corepack pnpm lint` 通过；目标标签/API/存储/ML 边界文件 `uv run ruff format --check ...` 通过（9 个文件已格式化）；`git diff --check` 通过。
- 遗留问题：完整后端 `uv run ruff format --check .` 仍报告 9 个既有未格式化文件；`uv run mypy src tests scripts` 仍报告 10 个既有测试类型错误，未为标签切片扩大修改范围；不执行提交或推送。

### 2026-08-11 人工评审、候选交易关联和多用途数据集

- 完成内容：新增追加式 `SelectionAnnotation`、固定 taxonomy v1、独立候选排除事件和观察池状态投影；评分/观点/决策必填，复查可在后续交易日追加，旧 `ManualSelectionLabel` 保留只读兼容。
- 完成内容：候选详情新增人工评审对话框和时间线摘要；实际买入通过候选关联的原子订单/成交接口记录，交易成功后切换到持仓工作区。
- 完成内容：固定前向表现支持 T+1/T+3/T+5/T+10、最大涨幅、收盘路径最大回撤和可选目标收益达成；新增逐评审人工判断、买入择时和持仓 session 级卖出择时数据集及 `export_selection_ml_datasets.py`。
- 时间边界：择时数据集必须传入显式交易日历和评估截止时间；未来成交不可提前进入标签；未关联候选的卖出订单会阻断卖出择时数据，避免错误归因。
- 实际验证：后端完整 `unittest discover` 250 项通过、1 项跳过；本轮新增后端测试覆盖 4 个机器学习数据集行为；后端源代码 MyPy 通过；前端 `pnpm typecheck`、`pnpm build` 通过；`git diff --check` 通过。29 个后端 CLI 的 `--help` 逐一执行通过。
- 完成内容：新增 `training_datasets.py` 与 `prepare_selection_training_dataset.py`，冻结四种训练标签契约、源行哈希、稳定样本 ID、标签可见时间和 `purged` 分区；四类数据集不再共享标签字段。
- 实际验证：新增训练数据集测试 6 项；后端完整 `unittest discover` 256 项通过、1 项跳过；Ruff 和 MyPy 通过；训练准备 CLI `--help` 通过。
- 完成内容：新增 `training_label_baseline.py` 与 `train_selection_label_baseline.py`，支持四用途独立的 `constant_label_distribution` 基线；人工决策三分类，其余用途二分类；输出模型、指标、逐行预测和 manifest。
- 实际验证：新增基线测试 6 项；后端完整 `unittest discover` 262 项通过、1 项跳过；Ruff、MyPy 和基线 CLI `--help` 通过。
- 完成内容：新增 `training_feature_join.py` 与 `join_selection_training_features.py`，用途标签升级为 v2 并保留精确 `observation_at`；显式快照按证券/观察日/评审时点选择唯一安全最新特征，未来、特征集不一致和歧义快照失败，purged 标签不连接。
- 实际验证：新增特征连接测试 5 项；后端完整 `unittest discover` 267 项通过、1 项跳过；Ruff 和 MyPy 通过；特征连接 CLI `--help` 通过。
- 完成内容：完成 SG-ML-02D；`training_feature_join.py` 为每个用途保存不可覆盖的连接 manifest、原始标签分区、标签 split manifest 和 readiness；新增 `training_feature_baseline.py` 与 `train_selection_feature_baseline.py`，严格校验用途/标签契约、特征集与列、样本 ID、源哈希、分区哈希和连接失败后，使用 train 标签分布运行独立特征基线。
- 实际验证：后端完整 `unittest discover` 273 项通过、1 项跳过；Ruff、MyPy 通过；全部后端 CLI 的 `--help` 通过；`git diff --check` 通过。
- 完成内容：完成 SG-ML-02E；新增 `training_feature_lightgbm.py` 与 `train_selection_feature_lightgbm.py`。训练入口只消费已验证用途特征 split，候选成功率/买入择时/卖出择时使用 binary，人工决策使用 multiclass；固定单线程配置，仅 train 拟合，validation/test 评估，`purged` 和连接失败阻断，模型/预测/评估/manifest 输出不可覆盖且绑定完整用途血缘。
- 实际验证：新增 LightGBM mock 测试 4 项；后端完整 `unittest discover` 277 项通过、1 项跳过；源代码 MyPy（83 个文件）、Ruff、全部 CLI `--help` 和 `git diff --check` 通过；`uv run --extra ml` 可加载 LightGBM 4.7.0。
- 完成内容：完成 SG-ML-02F；新增共享无依赖 `isotonic.py`、用途感知 `training_feature_lightgbm_calibration.py` 和 `calibrate_selection_feature_probabilities.py`。二分类只校准正类并取补集，人工决策三分类按 one-vs-rest PAVA 后归一化；只用 validation 拟合，test 隔离评估；读取训练输出时校验模型/运行工件/指标/预测/manifest 哈希、用途、标签契约、特征列、分区计数和 `purged` 证据，校准时间不得早于预测标签可用时间；旧二分类校准复用共享 PAVA 且回归保持兼容。
- 实际验证：新增用途校准测试 4 项；后端完整 `unittest discover` 281 项通过、1 项跳过；源代码 MyPy（85 个文件）、目标脚本 MyPy、Ruff 和全部相关 CLI 帮助通过；`uv run --extra ml` 实际三分类 LightGBM 训练、输出读取和校准写入通过；`git diff --check` 通过。
- 完成内容：完成 SG-ML-02G；新增用途感知独立注册表 `training_feature_registry.py` 和 CLI `register_selection_feature_model.py`。注册入口重新读取并校验完整训练目录，可选校准目录必须与训练模型、预测、manifest、用途、标签契约、类别和特征列精确匹配；复制模型、运行工件、指标、预测和 manifest 全部证据，记录 SHA-256、标签/特征/源数据血缘，注册固定为 `challenger`，不可覆盖。
- 实际验证：新增注册测试 5 项，覆盖二分类未校准、三分类校准、校准源不匹配、注册时间边界和副本篡改；后端完整 `unittest discover` 286 项通过、1 项跳过；新增校准输出读取和完整哈希/分区/类别/时间校验；Ruff、MyPy、注册 CLI `--help` 和 `git diff --check` 通过；`uv run --extra ml` 实际三分类训练、校准、独立注册和回读通过。
- 完成内容：完成 SG-ML-02H；新增用途模型追加式 `challenger → shadow → retired` 生命周期、`transition_selection_feature_model.py`、`training_feature_shadow.py`、`score_selection_feature_shadow.py` 和独立 shadow 证据存储。shadow 输入显式绑定用途/标签契约且拒绝结果标签；二分类/三分类 raw 概率和注册同源校准概率均校验模型/特征/时序血缘；候选级特征失败保留在报告，运行库或注册工件错误阻断整次运行，结果固定 `used_for_decision=false`。
- 实际验证：新增用途 shadow 测试 4 项，覆盖人工生命周期授权、二分类 raw 分数、结果标签拒绝、候选级特征失败、三分类注册校准和生命周期时间倒退；后端完整 `unittest discover` 290 项通过、1 项跳过；Ruff、MyPy、全部 CLI `--help`、`git diff --check` 通过。
- 遗留问题：用途模型尚未接入用途感知比较门禁、API/前端、推荐工作台、批次自动组装或交易；当前 shadow 仅支持显式离线 CLI，继续保持人工确认。

### 2026-08-11 ML-02I/ML-02J 走步验证与用途感知比较

- 完成内容：新增 `training_feature_walk_forward.py` 与 `validate_selection_feature_walk_forward.py`，实现四用途独立走步验证。显式 folds 内折内 train-only 训练，按 `label_available_at` 清除训练数据；校验用途/标签契约/类别/特征列、注册训练校准特征时间和完整血缘哈希。`purged` 永不消费，final test 仅记录边界和计数，不参加训练、校准、指标或比较；报告保存到 `models/feature-registry/walk-forward-reports/`，不可覆盖。
- 完成内容：新增 `training_feature_comparison.py` 与 `compare_selection_feature_models.py`，只读取独立用途注册证据及 ML-02I 报告，按 validation 行数聚合 raw 概率指标；比较政策显式声明指标方向、绝对阈值、最大相对退化、参考模型和校准要求。缺失指标、用途/类别/特征列、split/manifest/source/注册/报告哈希或时间边界不一致均形成阻断；比较报告保存到 `models/feature-registry/comparisons/`，内容不可变，仅允许字节相同幂等重试。
- 边界：比较仅产生人工审阅证据，不迁移 `challenger → shadow → retired`，不自动晋升、推荐、交易、API/UI 或新批次评分；不复用旧 `walk_forward.py`、`comparison.py`、`registry.py` 契约，可复用人工标签仍排除在所有 ML 路径之外。
- 实际验证：ML-02J 针对性测试 6 项通过；ML-02I/ML-02J 及相关注册、shadow、LightGBM 测试共 24 项通过；后端完整 `PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q` 为 322 项通过、1 项跳过；相关文件 `ruff format --check`、`ruff check` 通过；后端 `uv run mypy src` 通过（89 个源文件）；两个新增 CLI `--help` 通过；前端 `corepack pnpm typecheck`、`build`、`lint` 通过；`git diff --check` 通过。
- 遗留问题：完整 Ruff format 检查仍有既有 9 个未格式化文件；`mypy src tests scripts` 的既有测试类型错误仍未扩大修复；Tushare 原生 `trade_cal` 独立溯源验证仍受 Token 配额限制。工作树保留大量既有未提交改动，未执行 add/commit/push。

### 2026-08-13 桌面研究工作台 UI-01/UI-02

- 完成内容：补齐手动 UI 任务跟踪并实现 `desktop-shell` 与 `daily-signals-detail`。前端新增固定左导航、顶部运行状态栏、中央工作区、响应式 token 和 420–520px 右侧 Stock Detail Drawer；工作台、今日信号、观察池、持仓、交易记录、策略和分析接入 `/v1/workbench/*` 只读 API。
- 完成内容：统一股票详情抽屉提供行情/判断/信号/交易/分析五个 Tab；行情缺失明确显示不可用/不完整；判断支持 1–5 分、观点、操作意图、四类固定标签和备注，POST 只追加 `SelectionAnnotation`；保留现有手工台账录入入口，未把信号自动转换为交易。
- 实际验证：`cd frontend && npm run typecheck` 通过；`npm run build` 通过（Vite 转换 1874 个模块）；`npm run lint` 通过；`git diff --check` 通过；此前后端工作台/组合针对性测试 12 项通过、目标 Ruff 和 MyPy 通过。
- 后续完成：统一 Drawer 的交易 Tab 接入用户显式确认 BUY/SELL；BUY 使用候选关联的原子成交接口，SELL 必须选择结构化原因并使用原子确认交易接口；缺少成交价不提交，交易成功后刷新详情与工作台投影。
- 后续完成：工作台默认按未判断、观察复评、其他已判断、已买入排序；新增“连续处理待办”入口；观察池新增复评数量、判断变化和准备买入摘要，并列出最近发生变化的追加判断。
- 实际验证：`cd frontend && npm run typecheck`、`npm run build`、`npm run lint` 和 `git diff --check` 通过；未执行 add/commit/push。
- 后续修复：桌面壳层迁移后旧研究分支没有可见入口，导致通达信导入表单在 UI 中不可达；新增左侧“导入数据”路由，恢复通达信文件导入、批次列表、手工候选、成熟任务和标注流程。
- 实际验证：`cd frontend && npm run typecheck`、`npm run build`、`npm run lint` 和 `git diff --check` 通过。
- 后续修正：导入数据页的旧候选标记入口容易与新 Annotation 评审混淆，现改为只读候选和历史标签展示；新的人工判断、复评和交易记录统一从工作台股票详情进入，未删除后端旧标签数据/API。
- 实际验证：`cd frontend && npm run typecheck`、`npm run build`、`npm run lint` 和 `git diff --check` 通过。
- 后续调整：按用户要求移除导入数据页的批次成熟操作区和前端成熟任务请求；已有成熟结果、任务账本和后端兼容接口不删除，继续作为历史只读证据保留，后续改用全市场主板行情同步流程。
- 实际验证：`cd frontend && npm run typecheck`、`npm run build`、`npm run lint` 和 `git diff --check` 通过。
- 后续完成：导入数据页新增“同步收盘行情”面板；新增 `POST /v1/market-sync/main-board`，按交易日读取批准的沪深主板 universe，校验主板代码范围后同步 Tushare 日线和同源复权因子；已批准快照幂等跳过，非交易日或非主板股票池拒绝。
- 实际验证：前端 typecheck、build、lint；后端目标 Ruff、MyPy、工作台/组合 API 测试 8 项；`git diff --check` 均通过。
- 后续修复：发现本地交易日历缓存只到 2026-08-11，导致 2026-08-13 的“同步今日主板行情”被错误阻断；同步接口不再以本地日历缓存作为当前日期硬门槛，改为直接请求明确日期并由 Tushare 返回和行情质量门判断是否为交易日。历史批处理仍继续使用缓存日历。
- 实际验证：后端目标 Ruff、MyPy、工作台/组合 API 测试 8 项；前端 typecheck、build、lint；`git diff --check` 均通过。
- 后续修复：主板收盘同步改为从该股票池最后一个已批准日线快照之后开始，逐个补齐至用户选择日期的工作日；因此漏掉前一交易日时，点击“同步今日主板行情”会先补缺口再同步今天，已有快照幂等跳过。
- 实际验证：后端目标 Ruff、MyPy、工作台/组合 API 测试 8 项；前端 typecheck、build、lint；`git diff --check` 均通过。
- 后续完成：同步面板现在展示本次从最后已批准日期到目标日期的逐日明细，包括日期、已批准/未批准和已存在跳过状态；可直接检查漏掉的日期是否被补回，而不是只看总数。
- 实际验证：前端 typecheck、build、lint；后端目标 Ruff、MyPy；`git diff --check` 均通过。
- 后续修复：当目标日期已经存在批准快照时，补缺同步不再返回 `no missing weekday sessions through the requested date` 错误；现在返回该日期的“已有快照，已跳过”明细，并同时显示同源复权因子状态。只有目标日期确实没有批准快照且没有可补工作日时才报错。
- 实际验证：后端目标 Ruff、MyPy；前端 typecheck；`git diff --check` 均通过。
- 后续完成：放大工作台股票列表、持仓/交易列表的股票代码和辅助信息字号，并放大状态标记；导入数据页候选股票名称、源字段和状态徽章同步放大，改善高密度桌面列表可读性。
- 实际验证：`cd frontend && npm run typecheck`、`npm run build`、`npm run lint` 和 `git diff --check` 通过。
- 后续修复：主板行情同步不再只从最后快照向后补。现在以目标日期向前最近 10 个自然日为检查窗口，逐日检查同一主板股票池的批准快照，发现缺失工作日就补同步；因此即使 13 号已同步、12 号遗漏，也能再次点击同步发现并补回 12 号。目标日期已有快照时仍返回已跳过明细。
- 实际验证：后端目标 Ruff、MyPy、工作台/组合 API 测试 8 项；前端 typecheck、build、lint；`git diff --check` 均通过。
- 遗留问题：真实 Tushare 网络同步尚未执行；未执行 add/commit/push。

### 2026-08-13 前端视觉收敛收尾

- 完成内容：完成浅色金融研究台 token、中文系统字体、字号/字重/行高/间距、卡片/表格/Drawer 和 A 股红涨绿跌、独立错误、蓝色信息、琥珀待处理等语义收敛；补充导航 aria-label/title、数字样式 hook 和 confirmed KPI 状态。
- 实际验证：`cd frontend && corepack pnpm typecheck`、`corepack pnpm lint`、`corepack pnpm build`、`git diff --check` 均通过；Python CSS 变量扫描 used=103/defined=130、undefined=[]；权重仅 500/600/700；未发现外部字体、渐变或玻璃拟态。Vite 本地 5175 返回 HTTP 200；Chrome headless 在 390/1024/1440 视口检查无页面级横向溢出，字体为中文系统栈，body 为 14px。
- 说明：无后端 API 时页面显示既有 JSON 错误提示，属于数据服务不可用，不是本次视觉改造引入的业务变更；浏览器 automation 因禁止访问 localhost 未使用。未执行 git add/commit/push。

### 2026-08-13 前端布局滚动收尾

- 完成内容：应用壳固定为 `100dvh`，body/#root 高度与 body `overflow: hidden`，主内容改为独立纵向滚动；顶部状态栏固定 60px（移动端 72px），Drawer layer 从状态栏底部开始，Drawer 内部保持 body 滚动，避免详情覆盖顶部导航。
- 实际验证：`cd frontend && corepack pnpm typecheck`、`corepack pnpm lint`、`corepack pnpm build`、`git diff --check` 通过；Chrome CDP 在 1440 视口确认 bar top=0 height=60、drawer layer top=60 height=784、document scrollHeight=clientHeight=844；此前 390/720/1024/1440 视口均确认整体无滚动条。未执行 git add/commit/push。

### 2026-08-16 前端引入真实 ESLint 与仓库卫生收口

- 完成内容：发现并修复 `lint` 脚本与 `typecheck` 完全相同且无 ESLint 配置的问题；新增 `frontend/eslint.config.js`（ESLint 10 flat config + typescript-eslint 8.67 recommended + react-hooks 7 recommended + browser globals），`lint` 脚本改为 `eslint .`。
- 完成内容：`typescript` 从 7.0.2 降为 `~6.0.3`。typescript-eslint 8.67 对 TS 7.0 硬性报错（peer 范围 `>=4.8.4 <6.1.0`）；按 TS 官方并行发布策略，6.x 是与 7.0 同语言版本的 JS API 线，类型检查语义等价。安装后 `pnpm peers check` 无问题。
- 完成内容：按 ESLint 结果清理死代码——删除 2026-08-13 起被 `{false && ...}` 禁用的成熟任务面板 JSX、三个无调用点的任务函数（createAndRunMaturityTask/refreshMaturityTasks/runMaturityTask）、相关 10 个状态、`MaturityTask`/`MaturityTaskStatus`/`Provider` 类型、`providerLabels`、4 个模块级 helper、`batchReloadToken`（setter 已无调用方，令牌永不递增）和 `formatNotional`；删除未使用导入（Database、WalletCards、PortfolioReview、SecurityMasterRecord、SecurityMasterResponse、portfolio-workspace 本地 `toShanghaiDateTime`）。`App.tsx` 4557 → 4078 行。后端成熟任务账本、CLI 和兼容 API 未做任何改动。
- 基线决策：`react-hooks/set-state-in-effect` 降为 `warn`（配置内有注释），既有数据加载/表单重置 effect 模式行为正确但被新规则反对，待 App.tsx 按领域拆分时逐步重构；`rules-of-hooks` 保持 error；`exhaustive-deps` 维持插件默认 warn。
- 仓库卫生：`.gitignore` 新增 `frontend/test-results/`（Playwright 输出）与 `.agents/`（本地安装的 agent skills，非项目代码）；删除根目录遗留空目录树 `nonexistent-task-test/`（仅空目录、无文件）；修复 `docs/current-state.md` 一处 `+- ` diff 残留；补齐 `AGENTS.md` 常用命令（后端测试/lint/mypy、真实数据源验证、前端 typecheck/lint/build/test:visual 及 fnm PATH 提示）。
- 实际验证：`cd frontend && corepack pnpm lint` 通过（0 error、21 warning，全部为已声明 warn 基线）；`corepack pnpm typecheck` 通过；`corepack pnpm build` 通过（Vite 10.66s）；`git diff --check` 通过；`corepack pnpm peers check` 无问题。后端无改动，未运行后端测试；视觉回归未运行（本轮改动为删除永不渲染的代码与 lint 基线，构建与类型检查已覆盖）。未执行 git add/commit/push。

### 2026-08-22 四用途 ML application workflow 迁移与文档收口

- 完成内容：将校准、注册、生命周期、shadow、走步和比较六个 CLI 接入 `backend/src/stratgauge/application/ml/evidence.py`；与既有五个训练服务共同形成显式 application service 边界。保留参数、退出语义、文件路径、不可变工件、SHA-256、manifest 和审计证据；保留 validation-only 校准、`final-test` 隔离、shadow 的 `used_for_decision=false` 以及人工注册/生命周期/比较边界。新增 `tests/application/test_ml_evidence.py` 六项边界回归。更新 `docs/current-state.md`、`docs/decisions.md`、`docs/系统规格说明.md` 和 README；没有新增可复用 pitfalls。
- 实际验证：`cd backend && PYTHONPATH=src uv run python -m unittest tests.application.test_ml_training tests.application.test_ml_evidence tests.ml.test_artifacts tests.ml.test_training_datasets tests.ml.test_training_feature_join tests.ml.test_training_label_baseline tests.ml.test_training_feature_baseline tests.ml.test_training_feature_lightgbm tests.ml.test_training_feature_lightgbm_calibration tests.ml.test_training_feature_registry tests.ml.test_training_feature_shadow tests.ml.test_training_feature_walk_forward tests.ml.test_training_feature_comparison -q`（68 项通过）；六个 evidence CLI `--help` 命令均通过；`cd backend && PYTHONPATH=src uv run python -m unittest discover -s tests -t . -q`（400 项通过、1 项跳过）；`uv run ruff check .`（通过）；`uv run ruff format --check .`（326 个文件已格式化）；`uv run mypy`（327 个源文件通过）；`git diff --check`（通过，仅 Windows 换行转换提示）。
- 遗留问题：训练、校准、注册、shadow、走步和比较仍需真实、足量且通过 readiness 的离线证据后由人工 CLI 分阶段运行；未执行真实 provider、commit 或 push。

### 2026-08-20 只读成熟结果列表文档收口

- 完成内容：在 `docs/使用文档.md` 说明导入数据页成熟结果列表的位置、只读边界、跨全部历史批次范围、归档/排除证据、六类状态、`pending`/`no_entry`/`unresolved` 与训练标签的区别，以及 `GET /v1/selection-maturity/page` 的 `status`、`page`、`page_size` 参数和分页/汇总契约。在 `docs/decisions.md` 记录列表与 ML readiness 统计口径一致、按候选与 tracking policy 保留最新结果及不恢复 Web 成熟执行入口的决策；在 `docs/current-state.md` 更新当前事实和最近一次更新。
- 实际验证：`git diff --check -- docs/使用文档.md docs/decisions.md docs/current-state.md docs/session-log.md` 通过（仅 Windows 换行警告）；使用 `grep` 核对新增文档包含 `GET /v1/selection-maturity/page`、六类状态、归档/排除、`pending`、`no_entry`、`unresolved` 和只读边界。
- 遗留问题：工作区仍包含本轮及此前未提交改动；未执行 git add/commit/push。

### 2026-08-27 观察池表现与雪球日线外链

- 完成内容：观察池 `/v1/workbench/watchlist/page` 的前端展示新增观察起始时间、起点价和到最新批准收盘价的累计涨跌幅；缺失/晚到证据显示不可用原因，不补零。详情抽屉新增 `CODE.EXCHANGE` 到雪球 `EXCHANGE+CODE` 的只读日线 K 线外链，并补充窄屏表格内滚动和安全链接属性回归。
- 文档：同步更新 `docs/current-state.md`、`docs/decisions.md`、`docs/使用文档.md` 和 `docs/系统规格说明.md`；保留既有 readiness 盘点及未提交文档内容。
- 实际验证：后端完整测试 410 项通过、1 项跳过；后端 Ruff check、format check、MyPy 通过。前端 `corepack pnpm typecheck` 和 `corepack pnpm build` 通过；`corepack pnpm lint` 退出成功，0 error、20 条既有 React hooks warning；`corepack pnpm exec playwright test tests/visual/watchlist.spec.ts` 1 项通过；完整 `corepack pnpm test:visual` 7 项通过、3 个未修改主板行情同步截图基线差异；`git diff --check` 通过，仅有 Windows 换行转换警告。
- 遗留问题：完整视觉回归中的 3 个主板行情同步截图基线差异仍待独立处理；工作区包含本轮代码/文档和既有未提交改动，未执行 commit/push。
