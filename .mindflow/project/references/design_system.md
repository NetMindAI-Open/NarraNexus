# NarraNexus 前端设计系统(Design System)

> **When to read**:写任何前端 UI(新组件、新页面、改样式)之前;做 UI code review 时;
> 讨论"这里该用哪个组件/哪档圆角/哪个颜色"时。
>
> **本文档的性质**:成文化 "Nordic archive" 设计语言。原始版本(2026-08-11)
> 只描述**现有**视觉、零像素变化;§2.6 / §2.6.1 / §2.7(2026-08-30/31)是**例外**——
> 它们随一次实际的视觉改动同 commit 写入,记录 agent 回复退出表面阶梯这件事。
> 所有规范值从 2026-08-11 对 `feat/chat-ui-v4` 分支的全量审计中提炼
> (数据见附录),原则是**从现状主流用法里定标准,把少数派归拢**——
> 文档本身零像素变化;按文档收敛存量代码是独立的后续 PR。
>
> 维护:改 `index.css` 的 token 定义、或新增/裁撤设计系统组件时,同一 commit 更新本文。

---

## 0. 一页速查(TL;DR)

| 要做的事 | 规范 |
|---|---|
| 用颜色 | 只用**语义层** token(§2),禁止 `--color-red-500` 直连、禁止 hex |
| 中性底色 | 按 §2.5 四层表面阶梯取层;一屏 ≤4 层;同一功能件跨页面同层 |
| 聊天区 | 用户消息是气泡;**agent 的一轮是文档,不取层、不加框**(§2.6);运行脚手架同理(§2.6.1) |
| 圆角 | 只用 `var(--radius-*)`(§3),禁止 Tailwind `rounded-sm/md/lg/xl/2xl`;正圆用 `rounded-full` |
| 嵌套框圆角 | **内层圆角 = 外层圆角 − 间距**,内层永远 ≤ 外层(§3.2) |
| 字号 | 按 §4 阶梯取档,不再发明 `text-[12.5px]` 这类中间值 |
| 图标 | 只用 lucide-react(线性),尺寸三档 `h-3 / h-3.5 / h-4`,禁与实心图标混排(§5) |
| 选组件 | 查 §6 决策表;新界面**优先 `components/nm/`**,`ui/` 只用于表中标注的存留组件 |
| 新组件 API | shadcn 式约定:`variant` / `size` props + `cn()` 合并 + 组合优先(§7) |
| 明暗主题 | 永远经 token,token 自动切换;hex/调色板直连在暗色下必然出错(§8) |

---

## 1. 设计语言:Nordic archive

整个前端是一种"北欧档案馆"语言:纸(paper)与墨(ink)的中性底色、
极细的发丝线(hairline)分割、**克制的小圆角**(最大 12px)、
DM Mono 大写宽字距的小标签、少量语义色点缀。它刻意**不是**
圆润的 SaaS 风(大圆角、彩色渐变、重阴影)。

三个身份色贯穿所有"人/agent"语境,只此三个:

| token | 值 | 语义 |
|---|---|---|
| `--color-carbon` | `#E8704A` | 碳基 = 人类(用户) |
| `--color-silicon` | `#3D7EC4` | 硅基 = agent |
| `--color-overlap` | `#8E5CB8` | 人机重叠(混合场景) |

每个身份色配套 `-soft`(浅底)与 `-hair`(描边)变体。**用户**气泡右缘 carbon
条 + carbon 头像环,是这个语言里最固定的语法。

**agent 一侧的对应写法(左缘 silicon 条 + silicon 头像环)已于 2026-08-30
退役** —— 见 §2.6。silicon 仍是 agent 的身份色,继续用于头像环、状态点、
工具名等一切**指代 agent 的元素**;它不再用来给 agent 的回复画边框,因为
那个回复已经不是一个"框"了。

---

## 2. 颜色 token:三层结构,只准用上两层

`index.css` 里的颜色分三层,**依赖方向只能向下**:

```
① 语义表面层  --nm-paper / --nm-ink / --nm-card / --nm-hairline / --nm-elev-* …
   (以及等价别名:--text-primary/secondary/tertiary、--bg-*、--border-*、--rule)
② 语义状态层  --color-success / --color-warning / --color-error / --color-info
              --color-carbon / --color-silicon / --color-overlap (+ -soft/-hair)
③ 调色板原语  --color-gray-50…950 / --color-red-* / --color-yellow-* / --color-green-* / --color-blue-*
```

**规则**:
- 组件代码(tsx / className / style)只允许出现 ①、② 两层。
- ③ 层只供 `index.css` 内部定义 ①② 时引用。`--color-red-500` 表达不了
  "这是错误色"的意图,暗色主题重定义时也只有语义层能整体翻转。
- **hex 颜色值在 tsx 里零容忍**(与铁律 #1 的 token 纪律同源)。唯一豁免:
  数据可视化的系列色(图表多系列配色),必须集中定义成常量并注释豁免理由。
- `--text-*` / `--bg-*` / `--border-*` 是映射到 `--nm-*` 的**别名层**
  (同一来源,不是第二套调色板),两者都合法;同一文件内保持一致即可。

> 现状债(收敛 PR 处理,写新代码不许再加):`--color-red-500` 直连 75 处、
> `--color-yellow-500` 直连 61 处、tsx 内 hex 32 处(清单见附录)。
> (2026-08-11 收敛后:调色板直连与非豁免 hex 已清零。)

### 2.5 表面层级(surface ladder)

中性底色是一个**封闭的四层阶梯**,组件按职责取层,不按局部好看取色
(2026-08-11 Owner 对照 team/单聊双截图定案——此前两个 composer 各选各的
灰,就是因为没有这张表):

| 层 | token | 职责 |
|---|---|---|
| **L0 画布** | `--nm-paper`(=`--bg-deep`) | app 背景、侧栏、页脚 |
| **L1 内容面** | `--nm-card`(=`--bg-primary`) | 主区、卡片、弹窗 |
| **L2 沉底/搁起** | `--nm-paper-warm` / `--nm-raised` | hero 卡、wells、rest 态按钮填充 |
| **选中/悬停纱** | `--nm-row-active` / `--nm-paper-warm`(hover) | 列表行选中、hover 洗色 |

L2 的两个 token 只差 1-2 个色阶,**按交互角色分工、不得混用**
(2026-08-11 审计:悬停洗色曾 61:15 分裂在两个 token 上):

- **悬停洗色(控件/菜单)** → `--nm-paper-warm`
- **选中/激活的持久填充**(segmented 选中项、开启态菜单按钮)→ `--nm-raised`
- **列表行与可选卡片**(侧栏导航行、agent/team 行、radio 卡片如导出页的
  ChoiceCard):**同一条等差阶梯** `空白 < --nm-row-hover(悬停) <
  --nm-row-active(选中)`,全 app 一致(2026-08-18 Owner 定案——此前
  侧栏上下两段各一套色系;导出卡片甚至阶梯倒挂:选中最浅、悬停最深)。
  阶梯必须**单调**:选中永远不能比悬停浅。

  规范值(与 `index.css` 同步,**改 token 必须同 commit 改这张表**):

  | token | 亮色 | 暗色 |
  |---|---|---|
  | `--nm-row-hover` | `rgba(42,38,32,0.04)` | `rgba(242,239,232,0.07)` |
  | `--nm-row-active` | `rgba(42,38,32,0.07)` | `rgba(242,239,232,0.12)` |

  暗色档位比亮色高:同样的 alpha 在深底上感知强度更低(2026-08-18 Owner
  实屏反馈"暗色 hover 基本看不出来",暗色两档由 0.05/0.09 提到 0.07/0.12)

**规则**:
1. **一屏可辨识的中性底 ≤ 4 层**(行业惯例 3-4;Material 3 / Apple HIG /
   shadcn 的 background/card/muted 同构)。
2. **输入面与其容器必须差一层**:card 容器里的表单件用 warm filled
   (`nm/form` 现状);坐在 L1 内容面上的 **Composer 用 card 白底 +
   hairline 边框、聚焦换 ink 边**(`Composer.tsx` 现状)。同层叠同层
   (白上白、warm 上 warm)靠不住边框救。
3. **同一功能件跨页面必须同层**:单聊和团队房的 composer 是同一个功能件,
   必须长得完全一样——用户对输入框的肌肉记忆是全局的(Slack/Discord/
   Linear 的 composer 在一切房间形态里一致)。任何"这个页面想要更深一点"
   都是在消费层级预算,先查这张表。

### 2.6 消息语域:谁是气泡,谁是文档(2026-08-30)

聊天区有两种截然不同的东西,**它们不在同一个层级体系里**:

| | 语域 | 表面 |
|---|---|---|
| **用户消息** | 一条**发出去的话** | 气泡:`--nm-paper-warm` 填色 + hairline + 右缘 carbon 条,贴内容宽度 |
| **agent 的一轮** | 页面的**正文** | **不取层**:直接坐在 L0/L1 底色上,无填色、无边框、无圆角、满宽 |

**为什么 agent 一轮不再取层。** 它不是"一条消息",而是这一屏要读的**文档**
——markdown 标题、列表、表格、代码块是页面本身的内容,叙述句和工具行是它的
时序骨架。把这份文档装进一个气泡,等于给正文加一个永远存在的外框:既消耗
一层表面预算(§2.5 规则 1),又让内部的 code/table 面(它们自己要取 L2)
陷入 §3.2 的嵌套圆角问题,还把可读宽度压到 85%。

**直接后果(不是可选项)**:
- agent 一轮上**不出现** `background` / `border` / `border-radius`。要区分
  层次用**排版**(字号、行高、间距)和 §2.7 的三档,不用表面。
- **turn 之间靠节奏分隔**,不靠框:用户消息上方留一档额外间距
  (`mt-6`),用户气泡本身是每一轮的锚点。**刻意不用分隔线**——一轮一条
  横线在长对话里会堆成流水账,而右对齐的用户气泡已经是足够强的起点。
- 错误的一轮也**不是**实心红面板:改为左缘 `--color-error` 细条 + 正常
  ink 正文,徽章领在块首。满宽实心红是警报不是阅读;而且那个实心填色正是
  当初把补救按钮挤出气泡外的原因(见 MessageBubble mirror「红底压红字」)。

#### 2.6.1 运行脚手架也不取层(2026-08-31 修订)

§2.6 立下「agent 一轮不取层」之后,输入框上方**仍有一个带边框的过程面板**
(相位行 + plan,`rounded-lg + border + nm-paper + shadow`)。Owner 验收时
直接指出这个残留:同一屏里 turn 已经无框,下面却坐着一个终端小窗,**两种
语域并存**,§2.6 只执行了一半。

修订把规则推到运行脚手架上:

| | 位置 | 表面 |
|---|---|---|
| **运行序章**(相位 / ops / 计时) | 直播文稿开头 | **不取层**,与叙述同一列 |
| **plan** | 钉在 composer 上方(必须不滚走) | **不取层**,只允许一条 `border-t` hairline 做分隔 |

**hairline 是这里唯一的例外**,理由与 §2.6 拒绝 turn 分隔线不冲突:plan
是一个**常驻的、位置固定的**区域,不随对话增长复制;而 turn 分隔线会一轮
一条堆成流水账。**一条固定的线 ≠ 每轮一条线。**

**mono 字形与 `»`/✓/`○▶` 记号保留**:它们读起来像机器脚手架而不是 agent
说的话,而它们确实就是脚手架。去掉的是**框**,不是这套字形语言。

**信息一个都不许丢**(铁律 #16):拆框时 ops 计数与计时必须跟着搬,不能
借着「简化」把它们蒸发掉。

**团队成员卡是明确的例外**:`TeamMemberPanel` **保留**卡片边框。它是名册
里的一行展开,同屏并列多个成员,框正是把成员彼此分开的东西;单聊只有一个
agent、一列动线,那里的第二个框只会和 turn 抢注意力。

### 2.7 过程的三档(agent 文档内部)

同一份文档里有三种东西,按**该不该读**分档,全部走既有 ink 阶梯,不新增
token:

| 档 | 内容 | 处理 |
|---|---|---|
| **答案** | reply / native_output | `--text-primary`,markdown 正文排版 |
| **进度** | agent 每次调用工具前写的叙述(`monologue`) | `--text-secondary`,随流常显,12px `Milestone` 记号 |
| **推理** | provider CoT | `--nm-ink50`,**默认折叠**成一行「已思考 ▸」 |

**推理默认折叠没有例外**(2026-08-31 补):**任何**路径都不自动展开它——
包括读者刚点开的那个历史抽屉。点开一轮的过程是要看**结论**(叙述句 + 工具
行),不是要读草稿纸;CoT 比其余内容加起来还长,自动展开等于把读者真正来看
的东西埋掉。曾经为「用户已经花过一次点击」加过一个 `defaultOpen`,已整条
删除。

**推理默认折叠是这一档存在的前提。** 2026-08-30 的视觉验收结论是"完全看不
出效果":当时叙述只比 CoT 亮一档(ink70 vs ink50)且埋在大段 CoT 中间,
提亮再多也没用——信号淹没不是对比度问题,是版面问题。折叠 ≠ 丢弃,展开后
一字不少(铁律 #16)。

用户偏好 `interimNarration`(Settings → Personalization)**只控制"进度"档的
色调**,关掉 = 回落推理档观感;它**不改变版面**。

---

## 3. 圆角:一套标尺,八个档位

### 3.1 档位表(`index.css:79-86`,这是唯一权威标尺)

| token | 值 | 用途 |
|---|---|---|
| `--radius-xs` | 2px | chip、行内代码、微型控件 |
| `--radius-sm` | 3px | 小控件(按钮、输入框、badge) |
| `--radius-md` | 4px | 默认表面(卡片内的分区、下拉) |
| `--radius-lg` | 4px | 气泡、卡片 |
| `--radius-xl` | 6px | 大面板(侧栏卡、抽屉内容) |
| `--radius-2xl` | 8px | 模态框 |
| `--radius-3xl` | 10px | hero 表面 |
| `--radius-4xl` | 12px | 上限,不再更大 |

**规则**:
- 写法一律 `rounded-[var(--radius-sm)]`;正圆(头像、圆点)用 `rounded-full`。
- **禁止 Tailwind 自带标尺** `rounded-sm/md/lg/xl/2xl`——它们的值
  (4/6/8/12/16px)与本项目同名 token **不同值**(如 Tailwind `lg`=8px,
  token `--radius-lg`=4px)。审计显示 Tailwind 标尺用量(~149 处)反超
  token(~176 处 token 总量中圆角类 ~100 处),这正是"重叠的框圆角
  各说各话"的直接根源。
- 局部方向裁切(`rounded-tr-none` 等)允许,基数仍须来自 token。

### 3.2 嵌套规则(解决"重叠框圆角"的公式)

内外两层圆角框叠放时,同心圆角要成立:

```
内层圆角 = max(0, 外层圆角 − 两层间距(padding))
```

且**内层永远不大于外层**。例:`--radius-2xl`(8px)的模态框内,
padding 16px 的内容卡片圆角应取 0~2px(`--radius-xs` 或直角),
而不是跟外层同档。同档嵌套(外 4px 内 4px 带 padding)会产生
"内框角比外框尖"的错视,就是目前被指出的问题。

---

## 4. 字体与字号

### 4.1 三个字族(`index.css:20-22`)

| token | 字体 | 用途 |
|---|---|---|
| `--font-display` | Space Grotesk | 大标题、hero 数字 |
| `--font-sans` | Inter | 正文、表单、对话内容 |
| `--font-mono` | DM Mono | **系统语言**:标签、状态、时间戳、代码、terminal 流水 |

mono 小标签的固定语法:大写 + `tracking-[0.10em]`~`[0.22em]` 宽字距
(参照 `ui/Badge`、`BracketSectionLabel`)。

### 4.2 字号阶梯(从审计分布提炼,整数 px)

| 档 | 写法 | 用途 | 现用量 |
|---|---|---|---|
| 9px | `text-[9px]` | 版本号、气泡时间戳等最小 mono 注记 | 32 |
| 10px | `text-[10px]` | mono 小标签、次要元信息(**最常用档**) | 239 |
| 11px | `text-[11px]` | 行内状态文字、紧凑列表次行 | 102 |
| 12px | `text-xs` / `text-[12px]` | 辅助正文、按钮文字 | 28+ |
| 13px | `text-[13px]` | 紧凑正文 | 24 |
| 14px | `text-sm` | 标准正文(气泡内容) | 大量 |
| 16px+ | `text-base/lg/xl/2xl/3xl` | 标题阶梯,用 Tailwind 标尺 | ~15 |

**规则**:只取表中档位。**不再发明中间值**——`text-[12.5px]`(11 处)、
`text-[13.5px]`(8 处)、`text-[10.5px]`、`text-[9.5px]`、`text-[11.5px]`
属于历史漂移,收敛 PR 就近归档。

---

## 5. 图标

- **唯一图标库:lucide-react**(线性/stroke 风格)。禁止混入实心(filled)
  图标、emoji 当图标、或其它图标库——"左侧栏有的实心有的线性"这类混排
  是本文要杜绝的第一号问题。确需实心视觉(如选中态)用**语义色填充的
  几何形**(圆点 `StatusDot`、方块)表达,不换图标库。
- 尺寸三档,与字号档对齐:

| 档 | 写法 | 语境 |
|---|---|---|
| 12px | `h-3 w-3` | 行内小图标(10-11px 文字旁) |
| 14px | `h-3.5 w-3.5` | 列表行、按钮内(12-13px 文字旁) |
| 16px | `h-4 w-4` | 标准控件图标(14px 文字旁) |

  更大(`h-5`~`h-8`)只用于空态插图、头像位、hero;不用于行内。
- 同一行内图标与文字**同色**(继承 `currentColor`),需要弱化就弱化整行,
  不单独给图标另配灰度。
- `fill` 属性只允许 `none`/`transparent`/`currentColor`;`stroke-width`
  用 lucide 默认(2),小于 12px 的场景可降到 1.5,不出现第三种值。

### 5.1 唯一例外:第三方产品身份用真 mark

(2026-08-27 Owner 定案。此前 `ChannelBrandIcons` / `ModelBrandIcons` 已经先破了
§5,这里把边界写清楚,而不是留一条谁都在违反的禁令。)

**允许**:当 UI 要指认一个**第三方产品**(某个 LLM 厂商、某个 IM 渠道、某个
可导入的 agent 工具)时,用它自己的品牌 mark,而不是灰色线性剪影——一列 29 行
全是同一个机器人图标,用户读到的是噪音;换成各自的 mark 才读得出"这些是我的
工具"。落地文件只有三个:`components/icons/{Model,Channel,Framework}BrandIcons.tsx`。

**仍然禁止**:UI 语义图标(关闭、展开、警告、状态…)一律 lucide;不得为了
"好看"给自家功能造 mark;不得引入第三方图标库当通用图标集。

规则:

1. **只用厂商自己的资产**,不自造、不改色重画。优先 Simple Icons(CC0)的
   path data + 品牌自己的 hex;没有收录的从其官网/官方仓库取。
2. **一律 vendored,不连运行时 CDN**。DMG 必须离线可用(铁律 #7);远程
   `<img>` 等于把"用户装了哪些工具"漏给第三方;对方换图我们的 UI 会静默变样。
   raster 资产放 `public/{channel,framework}-logos/`。
3. **深色可见性**:写死 `#000000` 的黑色 mark 在暗色纸上等于隐形(OpenAI mark
   就是这个情况),必须换成 `var(--nm-ink)` 之类的主题变量。
4. **没有可用 glyph 时用字母牌**:品牌色底 + 该品牌 wordmark 的首字母
   (Hermes 就是这样)。这是"诚实的占位",不是伪造 logo;等厂商出 glyph 直接替。
5. 尺寸仍走 §5 的三档;raster mark 加 1px 发丝线描边 + 1px 内边距,避免深色
   圆章糊进背景。

---

## 6. 组件选型:nm/ 是现在,ui/ 是存留

现状是**两代设计系统并存**:`components/nm/`(NM primitives,v4 重构的
主体)与 `components/ui/`(第一代)。两边有大量同名重叠(Badge、Button、
Dialog、Textarea、StatStrip 双份)——这是视觉不一致的制度性来源。

**总规则:新界面一律先查 `nm/`;`ui/` 只允许用于下表"存留"列的组件。**
两边都有的,以 `nm/` 为准;`ui/` 侧同名件冻结(不加功能、不进新代码)。

### 6.1 按用途查表

| 我要… | 用 | 不要用 |
|---|---|---|
| 状态/计数小徽章 | `nm/StatusBadge`(带语义色)或 `nm/Badge` | `ui/Badge`(冻结,存量不动) |
| 可删除/可点的标签胶囊 | `nm/Chip`(species 身份色)/ `nm/Tag`(中性) | 自造 span |
| 按钮 | `nm/Button` / `nm/IconButton` / `nm/SplitButton` | `ui/Button`(冻结) |
| 模态框 | `nm/Dialog` / `nm/ConfirmDialog` | `ui/Dialog`(冻结) |
| 抽屉/底部弹层 | `nm/Drawer` / `nm/Sheet` | 自造 fixed 定位层 |
| 悬浮小提示 | `ui/tooltip.tsx`(Radix,**存留**) | title 属性 |
| 悬浮面板 | `ui/popover.tsx`(Radix,**存留**) | 手写 document 监听 |
| 滚动容器 | `ui/scroll-area.tsx`(**存留**) | 裸 overflow-y-auto(嵌套滚动场景) |
| Markdown 渲染 | `ui/Markdown`(**存留**,唯一实现) | — |
| 头像 | `nm/RingAvatar` / `AvatarWithStatus` / `GroupAvatar` / `AvatarStack` | 自造圆形 div |
| 表单件 | `nm/form.tsx`(TextInput/Select/Checkbox/Radio/Toggle/Slider/FormField) | `ui/Input`(冻结) |
| 卡片/表面 | `nm/PaperCard` / `RaisedPanel` / `SunkenWell` | `ui/Card`(冻结) |
| 状态点/加载 | `nm/StatusDot` / `Spinner` / `Skeleton` / `ProgressBar` | 自造 animate-pulse div |
| 空态 | `nm/BracketEmptyState` | 居中灰字 div |
| 区块标签 | `nm/BracketSectionLabel` | 手写 uppercase span |
| 数据统计 | `nm/KPITile` / `StatStrip` / `ChartCard` | `ui/KPICard`(冻结) |
| 提示横幅/toast | `nm/Toast` / `ConnectionBanner` | window.alert(桌面端不可见) |

> 表中"冻结"= 存量代码继续工作,但新代码不引用;何时迁移、是否合并
> 由收敛 PR 逐个处理。`ui/BetaBadge`、`ThemeToggle`、`LanguageToggle`、
> `FeedbackButton` 等无 nm 对应物的照常使用。

### 6.2 图表与可视化

`dataviz` 场景(图表、网络图)的系列色是 hex 零容忍的唯一豁免区,
但必须:集中成常量表、光暗两套、不与语义 token 混用。现有
`NexusNetworkGraph` / `JobDependencyGraph` 的内联 hex 属收敛对象。

---

## 7. 新组件 API 约定(借 shadcn 的"形",不换"神")

技术栈本就是 shadcn 模式(Radix primitives + Tailwind v4 + `cn()`),
新组件遵守同一套约定:

- **`variant` + `size` props**,默认值放解构里;枚举值用语义词
  (`default/accent/success/warning/error/outline`),不用视觉词(`blue/big`)。
- 样式合并一律 `cn()`(clsx + tailwind-merge),`className` 透传放最后,
  让调用方能覆盖。
- `forwardRef` + `...props` 透传原生属性(`ui/Badge` 是范本——`title`、
  `aria-*` 无需声明即可用)。
- **组合优先于配置**:复杂件拆成可组合的子件(`Dialog/DialogContent/
  DialogFooter` 形制),不做 20 个 props 的上帝组件。
- 颜色、圆角、字体只引 token;组件内不出现魔法值。
- 动效用既有 token(`--motion-fast/medium/slow`、`--ease-paper`)与
  `animate-fade-in / slide-up / scale-in` 既有类;不新增 keyframes,
  除非同时登记进 `index.css` 动效区并更新本文。

---

## 8. 明暗主题

- 所有 `--nm-*` / 语义 token 在 `[data-theme]` 下整体重定义,
  **经 token 的颜色自动正确**;hex 与调色板直连在暗色下必然出错
  (`index.css:64` 的注释记录过 `--accent-primary` 光暗反转的教训)。
- 阴影用 `--nm-elev-1/2/3`(带主题变体),不手写 box-shadow。
- 新增表面若两主题需要不同处理,在 token 层加,不在组件里分支。

---

## 9. PR 自查清单(UI 改动)

- [ ] 颜色全部来自 ①② 层 token,无 hex、无 `--color-<hue>-<n>` 直连
- [ ] 中性底按 §2.5 阶梯取层;输入面与容器差一层;同一功能件跨页面同层
- [ ] 改聊天区:agent 一轮没有 background / border / radius(§2.6);运行脚手架
      (相位 / plan)同样不取层,plan 只允许一条 hairline(§2.6.1);过程三档
      走既有 ink 阶梯(§2.7)
- [ ] 圆角全部 `var(--radius-*)` 或 `rounded-full`,无 Tailwind 圆角标尺
- [ ] 嵌套框满足"内 ≤ 外 − 间距"
- [ ] 字号在 §4 阶梯上,无新造中间值
- [ ] 图标 lucide、尺寸三档、无实心混排
- [ ] 组件来自 §6 决策表,没有绕过表格自造轮子、没有引用"冻结"件
- [ ] 新组件符合 §7 API 约定
- [ ] 光暗两主题都看过(至少切一次)

---

## 附录:审计数据(2026-08-11,feat/chat-ui-v4 @ 08d5a627)

**圆角写法分布**(次数):`rounded-full` 99 / `rounded-lg` 84(Tailwind,8px)/
`rounded-[var(--radius-sm)]` 47 / `rounded-xl` 31(Tailwind,12px)/
`rounded-[var(--radius-md)]` 23 / `rounded-md` 20 / `rounded-[var(--radius-xs)]` 15 /
`rounded-[var(--radius-lg)]` 15 / `rounded-sm` 7 / `rounded-2xl` 7 / 其它零星。
→ Tailwind 标尺与 token 标尺约 149 : 100 并行。

**字号分布**(px 档,次数):10px 239 / 11px 102 / 9px 32 / 12px 28 / 13px 24 /
12.5px 11 / 13.5px 8 / 11.5px 7 / 10.5px 6 / 9.5px 1 / 8px 3 / 其余为 Tailwind 标题档。

**图标尺寸分布**:`h-3` 194 / `h-4` 187 / `h-3.5` 134 / `h-8` 38 / `h-5` 32 /
`h-6` 29 / `h-7` 22。lucide 引用文件数 94。

**颜色**:tsx 内 hex 32 处,分布在 9 个文件(OneKeyOnboard、
ArtifactDownloadMenu、BusFailuresSection、InnerThoughtCard、SlackConfig、
TeamManagementModal、ArenaProvisioningModal、NexusNetworkGraph、
JobDependencyGraph——后两个属图表豁免区,需集中化);
`--color-red-500` 直连 75 处、`--color-yellow-500` 直连 61 处。

**组件重叠**:`Badge`、`Button`、`Dialog`、`Textarea`、`StatStrip` 在
`nm/` 与 `ui/` 双份;徽章类共 5 件(ui/Badge、nm/Badge、nm/Chip、nm/Tag、
nm/StatusBadge)。

**动效**:`animate-spin` 113 / `animate-pulse` 29 / `animate-fade-in` 26 /
`animate-slide-up` 12 / 其余零星——现有类已覆盖全部需求,无需新增。
