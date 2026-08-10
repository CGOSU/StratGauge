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
