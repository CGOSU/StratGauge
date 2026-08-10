# Styling Patterns（样式与组件规范）

目标
- 制定样式组织、命名、主题化和组件化实践，降低样式冲突、提高可维护性与可替换性。

适用场景
- 组件库（Button、Input、Card、Modal）
- 应用级样式（Variables、Reset、Base）
- 主题与设计 Token 的实现

要点
1. 使用 Design Tokens（设计代币）
   - 以 CSS 变量为实现载体：颜色、间距、字体尺寸、圆角、阴影、z-index
   - 例：--radius-sm, --font-size-base, --shadow-1
2. 命名约定
   - 组件驱动（Component-first）：.Button, .Card, .Modal
   - 状态类：.is-active, .is-disabled, .is-open（避免过多耦合）
   - 若需要可选 BEM：.Card__header, .Card__body
3. 组织方式
   - 基础（reset / base）
   - tokens（variables）
   - utilities（spacing helpers）
   - components（独立、封装）
   - pages（按页面的少量覆盖）
4. 主题与变体
   - 实现 light/dark 两套 tokens（或通过变量覆盖切换）
   - 组件接收 variant 参数（primary / secondary / ghost）
5. 状态样式（交互）
   - hover / focus-visible / active / disabled 必须明确
   - focus-visible 使用明显的 focus-ring（颜色 & outline-offset）
6. 动效（Motion）
   - 使用 CSS 过渡与 transform 优化性能（避免动画触发 layout）
   - 过渡时长建议：微交互 120–180ms，中等 200–350ms，重要内容 400–600ms
7. 可访问性
   - 动态内容提供 aria-live 或相应角色
   - 颜色不作为唯一信息来源（错误状态同时有文本/图标）
8. Shadow / Elevation
   - 建立固定阴影等级：
     - shadow-0: none
     - shadow-1: 0 1px 4px rgba(2,6,23,0.08)
     - shadow-2: 0 6px 20px rgba(2,6,23,0.12)
   - 使用变量或 CSS mixin 统一引用

组件样式示例（Button）
```css
.Button{
  --bg:var(--color-surface);
  --fg:var(--color-text);
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:8px 14px;
  border-radius:10px;
  border:0;
  cursor:pointer;
  transition:transform 160ms ease, box-shadow 160ms ease, background-color 120ms;
}
.Button--primary{
  background:var(--color-primary);
  color:var(--color-on-primary);
  box-shadow:var(--shadow-1);
}
.Button--primary:active{ transform:translateY(1px); }
.Button--ghost{
  background:transparent;
  color:var(--color-primary);
  border:1px solid rgba(0,0,0,0.06);
}
.Button.is-disabled{ opacity:0.4; pointer-events:none; }
```

检查清单（实施时）
- [ ] design tokens 是否完整并被引用？
- [ ] 组件是否只依赖 tokens 而非硬编码色值/间距？
- [ ] 是否为交互状态（hover/focus/active/disabled）提供样式？
- [ ] 是否为关键交互提供动效并避免 layout 动画？
- [ ] 是否为高对比度或无障碍模式提供 fallback？

风格建议
- 优先使用语义变量（--color-primary）而非具体颜色文字；
- 在组件库内将样式封装并导出轻量的 utility 类供页面布局使用；
- 文档化每个 token 的用途与示例，便于跨工程复用。
