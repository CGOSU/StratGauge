# Color System（色彩体系）

目标
- 提供一套可工程化的色彩体系（tokens + 示例），保持视觉一致性并兼顾可访问性。
- 配色灵感来自本项目现有实现（debug 按钮、sponsor 标题、控制台样式等），并扩展出语义色与主题变体。

设计原则
- 语义优先（primary / surface / text / accent / success / warning / danger）
- 可访问优先：对关键交互与正文文本保证 WCAG AA（或更高）对比度
- 简洁易用：把色彩封装为 CSS 变量，组件只引用变量

项目中已存在与参考的颜色（来源于仓库）
- 蛙绿色（debug 按钮）: #4CAF50
- Sponsor 标题（蓝）: #2563EB
- 控制台蓝（info）: #2196F3
- 警告黄: #ffb300
- 错误红: #f44336
- 静态灰: #6A7282
- 常用白: #FFFFFF

推荐色彩 Tokens（Light / Dark 主题）
:root (light)
```css
/* 语义色 */
--color-primary: #4CAF50;          /* 蛙绿色 */
--color-primary-600: #43A047;
--color-primary-700: #388E3C;

--color-accent: #2563EB;           /* sponsor 蓝 / 次级高亮 */
--color-accent-500: #2563EB;
--color-accent-400: #2b6df0;

--color-info: #2196F3;             /* 信息 */
--color-success: #4CAF50;          /* 成功 */
--color-warning: #ffb300;          /* 警告 */
--color-danger: #f44336;           /* 错误 */

--color-muted: #6A7282;            /* 静态/占位文本 */
--color-on-primary: #FFFFFF;       /* 在主色上的文字 */
--color-on-accent: #FFFFFF;

--color-bg: #FFFFFF;               /* 页面背景 */
--color-surface: #F8FAFF;          /* 表面 / 卡片 背景（轻微偏蓝，保持清爽）*/
--color-text: #0F172A;             /* 主文本（深色）*/
```

.dark-theme
```css
/* Dark 模式覆盖（示例） */
--color-bg: #0b1220;               /* 深色背景 */
--color-surface: #0f172a;
--color-text: #E6EEF8;             /* 浅色文本 */
--color-muted: #98A0B3;
--color-on-primary: #081013;       /* 若需要在深色主色上反转 */
```

语义配对建议（组件用法）
- 顶栏 / 重要按钮（Primary）
  - 背景：--color-primary (#4CAF50)
  - 文字：--color-on-primary (#FFFFFF)
  - hover：--color-primary-600 (#43A047)
  - active：--color-primary-700 (#388E3C)
- 次要按钮（Accent）
  - 背景：--color-accent (#2563EB)
  - 文字：--color-on-accent (#FFFFFF)
- 信息提示（Info）
  - 背景：rgba(33,150,243,0.08) 或 轻表面 + 左侧 4px 强色条
- 成功/警告/错误
  - success: #4CAF50
  - warning: #ffb300
  - danger: #f44336

配色示例（组合）
1. 主 → 次（CTA）
   - Button primary: bg #4CAF50 / text #FFFFFF
   - Button secondary: bg #2563EB / text #FFFFFF
2. 页面卡片
   - surface bg: #F8FAFF / border: rgba(15,23,42,0.04) / shadow: var(--shadow-1)
   - 标题色: --color-text (#0F172A)
   - 描述色: --color-muted (#6A7282)
3. 侧边工具条（暗背景）
   - bg: #0f172a; icon: #2563EB (accent) or #4CAF50 (success accents)
4. Subtitle / overlay（受项目启发）
   - 字幕常用白色 #FFFFFF，容器 backgroundOpacity 75%（见项目 migration script）

按用途列出的常见组合（推荐）
- 正文/背景： #0F172A on #FFFFFF
- 小字/背景： #6A7282 on #F8FAFF
- 强按钮： #FFFFFF on #4CAF50
- 链接 / 次要 CTAs： #2563EB on transparent / #FFFFFF on #2563EB
- 灾难性错误提示： #FFFFFF on #f44336（短文本），或 #f44336 border + 轻 background

可访问性（WCAG）
- 文本对比：正文文本对背景建议对比 >= 4.5:1（AA）；大文本 >= 3:1
- 示例检查（工具推荐）：使用 axe / Lighthouse / contrast checkers 验证组合
- 若某主要色对比不足，优先：
  1) 增强文字颜色（更深）
  2) 使用半透明遮罩（例如在图片背景上）
  3) 为重要文本提供 outline 或底色增强

CSS 变量模版（可直接引入）
```css
:root{
  --color-primary: #4CAF50;
  --color-accent: #2563EB;
  --color-info: #2196F3;
  --color-warning: #ffb300;
  --color-danger: #f44336;
  --color-muted: #6A7282;
  --color-bg: #FFFFFF;
  --color-surface: #F8FAFF;
  --color-text: #0F172A;
  --color-on-primary: #FFFFFF;
  --shadow-1: 0 4px 12px rgba(15,23,42,0.06);
}
```

调色方案建议（快速搭配）
- 清新配色（项目风格）
  - 主色：#4CAF50（蛙绿色）
  - 次色：#2563EB（深蓝）
  - 中性色：#6A7282（灰）
  - 背景：#FFFFFF / 表面 #F8FAFF
- 稳重配色（界面偏沉稳）
  - 主色：#2563EB（蓝）
  - 辅色：#4CAF50（点缀）
  - 中性色：#6A7282
- 警示/强调
  - 警告：#ffb300；错误：#f44336；成功：#4CAF50

实践建议（落地）
- 在组件库中强制使用 CSS 变量，禁止硬编码 hex 值（除非是图标/图片内置）。
- 在设计阶段列出关键视图的常用组合并用 contrast tool 验证。
- 为暗色模式准备替代 token 并做可反转检查（text-on-primary，primary-on-text 等）。
- 把这些 tokens 写入 tokens 文档并在 storybook / docs 中展示真实示例。

参考（来自仓库）
- debug 按钮样式（脚本）例子使用 #4CAF50
- sponsor 标题 SVG 使用 #2563EB
- 控制台颜色样式使用 #2196F3 / #ffb300 / #f44336 / #aaa

检查清单（发布前）
- [ ] 关键对比组合通过 WCAG 检查
- [ ] 主题切换（light/dark）下所有关键控件可用
- [ ] tokens 已集中管理并文档化

附注
- 本配色方案以仓库内现有颜色为灵感做了语义化、主题化拓展；在具体视觉稿或产品需求下可微调色阶以满足品牌诉求与可访问性要求。