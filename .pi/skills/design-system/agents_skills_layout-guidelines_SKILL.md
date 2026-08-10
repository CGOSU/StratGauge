# Layout Guidelines（布局规范）

目标
- 提供可复用的布局模式与断点规则，保证组件与页面在不同屏幕尺寸下具有一致的节奏与可读性。
- 与 styling-patterns 和 color-system 配合，形成一致的设计系统输出。

适用场景
- 页面骨架（Landing、Dashboard、Reader 页面）
- 组件容器（Card、Modal、List、Grid）
- 响应式排列与间距调整

要点
1. 移动优先（Mobile-first）
   - 从最小视口开始编写样式，向上逐步增强。
2. 栅格系统
   - 12 列栅格（可拆分为 4/8、6/6 等常用布局）
   - gutter（列间距）使用设计标度（见间距节）
3. 间距节奏（Spacing scale）
   - 基础尺度：4px 单位
   - 常用刻度（px）：4 / 8 / 12 / 16 / 24 / 32 / 40 / 56 / 72
   - 推荐 CSS 变量：--space-1:4px; --space-2:8px; ... 用于 margin/padding
4. 断点（示例）
   - xs: 0
   - sm: 640px （小屏手机横向、大屏手机）
   - md: 768px （平板竖屏）
   - lg: 1024px（桌面小）
   - xl: 1280px（大屏）
   - 使用示例（移动优先）：
     @media (min-width: 640px) { ... }
5. 容器宽度（示例）
   - container-sm: 640px
   - container-md: 768px
   - container-lg: 1024px
   - container-xl: 1280px
   - 页面居中：max-width + margin:0 auto
6. 文本流与可读性
   - 主内容宽度建议：60–75 字符（约 55–75ch）
   - 行高（line-height）：1.3–1.6，根据字号调整
7. 对齐与节奏
   - 保持垂直节奏（vertical rhythm）：基于同一 spacing scale 控制间距，避免孤立尺寸
   - 关键 UI 组件（Card、ListItem）高度成组化（small / medium / large）
8. 重叠与层级
   - 使用 elevation / z-index 体系（见 styling-patterns）
   - 避免无限嵌套与复杂 z-index 冲突，统一命名空间：--z-modal，--z-popover 等
9. 可访问性
   - 确保焦点顺序合乎语义，保留触控目标最小尺寸 44x44 px（苹果建议）
   - 动画不会干扰布局尺寸（避免 layout shift）

快速 CSS 变量与示例（片段）
```css
:root{
  /* 间距 */
  --space-1:4px;
  --space-2:8px;
  --space-3:12px;
  --space-4:16px;
  --space-5:24px;
  --space-6:32px;

  /* 栅格 */
  --grid-columns: 12;
  --container-max-width: 1280px;

  /* 阴影 / 层级 */
  --z-default: 0;
  --z-surface: 10;
  --z-modal: 1000;
}
.container{
  max-width:var(--container-max-width);
  margin:0 auto;
  padding:0 var(--space-4);
}
.row{
  display:flex;
  flex-wrap:wrap;
  gap:var(--space-4);
}
.col{
  flex:1 1 0;
  min-width:0;
}
@media(min-width:768px){
  .col-6{flex-basis:50%}
  .col-4{flex-basis:33.333%}
}
```

检查清单（实施时）
- [ ] 是否优先考虑移动端布局并使用断点增强？
- [ ] 间距是否仅来自 spacing scale？
- [ ] 容器宽度是否设置并居中？
- [ ] 组件是否遵循最小触控尺寸？
- [ ] 是否避免了跨组件的 z-index 冲突？

示例场景（Reader 页面）
- 顶栏：固定高度（60px），内边距 16px，logo 左对齐，工具按钮右对齐
- 主体：两栏布局（阅读区 2/3，侧边栏 1/3），手机下切换为单栏垂直堆叠
- 卡片：内边距 16px，圆角 12px，轻微阴影（elevation-1）
