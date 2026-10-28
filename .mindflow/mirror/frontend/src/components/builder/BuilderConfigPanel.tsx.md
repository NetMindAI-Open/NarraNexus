---
code_file: frontend/src/components/builder/BuilderConfigPanel.tsx
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 (评审六轮) — 五轮承诺的测试这次真的在；清槽提前到 early-return 之前

五轮那条 commit 里 `builderConfigPanel.test.tsx` 没进去（脚本中断），mirror 与 PR 描述却写了
「测试补……」——空头承诺比没承诺更贵。本轮补三条：名称被拒仍写认知且错误可见（`&&` 短路与
分槽互抹两个哨兵）、两个写入方都失败显示两行、字段改回原值后过期错误消失。
`setSlotError(slot, null)` 提到两个 commit 的函数开头（🟢#4）：原来在 early-return 之后，
名称写失败后把输入框改回原值再 blur，那行红字会一直挂着。`setSaving` 不跟着提前——
early-return 分支不走 `finally`，提上去会让页脚「保存中」永不复位。

## 2026-09-04 (评审五轮) — 两笔冲刷都跑；错误按写入方分槽

四轮那版 `flushed = (await commitName()) && (await commitAwareness())` 会短路：名称被后端拒时
认知那一笔**连试都不试**，而它唯一的副本在组件 state 里，面板一卸载就没了。现在两笔顺序都
跑、再合并判定。同时 `error` 拆成 `errors[name | awareness | install]`：共享一个字符串时，
后一个写入方的 `setError(null)` 会抹掉前一个的失败，用户按了「完成」却一条错误都看不到。
`install` 有自己的槽，仍不参与 Done 的判定。（这里原写「测试补……」，五轮 commit 里那条测试
实际没进去，六轮补上。）

## 2026-09-04 (评审四轮) — 冲刷失败则不结束

评审 🟡#16：`finish()` 的 `finally` 无条件 `finishStudio`，而两个 commit 都吞异常只写本地
`error`；结束会让面板当场卸载（错误行随之消失）并收起抽屉——用户看到干净关闭、以为存上了，
实际最后那次编辑没落库且 studio 不可恢复。现在 `commitName` / `commitAwareness` 返回是否已
持久化（未改 / 空名 early-return 算成功），`finish` 两笔都跑完再判定（五轮起不再 `&&` 短路），只在两者都成功时 `finishStudio`；失败就把
用户留在能看见错误行的地方，「完成」可再点。判据用返回值而不是 `error` state（`install()`
的失败也写同一个 state，不能让它卡死 Done）。

## 2026-09-04 (评审三轮) — 「完成」不再碰抽屉

评审 🔴#13：`finish()` 里 `finishStudio` + `requestPanel('builder')` 两句同步调用被 React 19
批成同一次 commit；passive effect 按声明顺序先跑 `useStudioLifecycle`（`setDrawerTab(null)`
排队），再跑 `pendingPanel` effect —— 它的 updater 拿到的 `prev` 已是 `null`，toggle 不成立、
返回 `'builder'`，抽屉停在一个切换器里都不存在的 Builder tab 上、内容空白。每次点完成都命中。
现在 `finish()` 只「冲刷字段 → finishStudio」，抽屉由 lifecycle 的「非 open 非 resumable
→ 丢 tab」分支收起，与 X / 切 tab / 切 agent 同一条路。`useUIStore` 在本组件已无用处，
import 与 deps 一并删。两条错误行改为两个显式 `<p>`（同文案时 `key={line}` 会重复）。
09-03 那条「完成必须同时关抽屉」的动机（flag 不响应式）已随 store 消失，结论随之作废。

## 2026-09-04 (评审二轮) — 「完成」= `finishStudio`；两条错误都显示；空名回滚输入框

- Done 调 `finishStudio`（结束、不可恢复），与抽屉 X 的「收起」（可恢复）区分。
- 手改错误与模型写失败**各一行**：`error` 只在下一次手改 commit 才清，单槽位会让一条旧的
  手改错误永久遮住之后所有模型写失败。
- 空名 blur 不提交之外，`setName(agent.name)` 让输入框回到真名；否则界面看起来像「名字被
  清掉了」，与这条修复要表达的语义正好相反。

## 2026-09-03 (评审修订) — 推荐与错误改为订阅 store；空名不提交

- `recommendations` 从渲染期直读 sessionStorage 改为 `useStudioStore(selectRecommendations)`。
  修的是「只推荐 skill、不改文本的那一轮，面板永远不出现建议」—— 直读没有订阅者，
  面板只在 name / awareness 变化时才重渲染。
- 新增一行 `applyError`：模型驱动写入的失败（如 description 超长 422）此前落在一个
  没人渲染的 state 里，用户只看到「名字一直没变」。与手改的 `error` 合成同一条提示。
- `commitName` 对空名 / 全空白**不提交**，与模型路径 `mergeAgentDraft` 同一把尺子。
- `closeStudio` 来自 store，不再 import [[../../lib/builderSession.ts]]。

## 2026-09-03 (修正) — 「完成」必须同时关抽屉

改版时我把关抽屉那一步删了，还在注释里写了「抽屉的关闭由打开它的那方负责」——
**并没有那一方**。后果：`closeStudio` 只清了 sessionStorage 里的开关，而开关不是
响应式的，这个面板原地不动。用户看到的是「完成点了没反应」，再点一次还是一样。

现在 `finish()` 在提交之后同时做两件事：清开关 + `requestPanel('builder')`。
用 **toggle** 语义来关是安全的：本组件只在 `builder` 就是当前 tab 时才渲染，
所以这个 toggle 不可能误开别的东西。

（与之相对，**打开** studio 必须用 `openPanel`，见 [[uiStore.ts]]。开与关用不同
的 action，是因为 toggle 只有在「我确定自己就是当前那个」时才有确定语义。）

## 2026-09-03 (续) — 「完成」的可点/不可点状态

之前「完成」**没有任何禁用态**，永远可点。现在有了，但禁用条件刻意很窄：

| 状态 | 「完成」 |
|---|---|
| 正在冲刷本面板的字段写入（`finishing`） | 禁用 |
| skill 正在安装 / 正在 study | **可点**（Owner 明确要求） |
| 其它 | 可点 |

**为什么 study 不能挡**：study 在 agent 的 workspace 里跑几分钟，而且 studio
关掉之后它照样在跑。拿它当门禁等于把用户困在一个面板里，等一件根本不需要他在场
的事。

**顺带修掉一个吞点击**：textarea 的 `onBlur` 比按钮的 `onClick` 先触发，所以
「打完字直接点完成」时提交正在飞。现在「完成」会先 `await` 两个 commit（各自在
未变化时是 no-op）再关，保证最后敲的那段被写进去，而不是和关闭赛跑。

测试 `Done stays clickable while a skill is installing` 钉住这条 —— 把
`installing` 加进禁用条件会让它变红（验证过）。

## 2026-09-03 (续) — Skills/Channel 标为可选并压缩

- 两个 section 标题挂「可选」徽标。它们确实可以整段跳过，不说清楚的话面板读起来
  像一张必须填完才能用的清单。
- Skills 用 [[SkillsPanel.tsx]] 的 `compact` 变体，两行 chrome 收成一行。
- `EmbeddedSection` 现在要求**显式高度**：那两个面板是按整列抽屉写的
  （`flex-1 min-h-0` + 内层 ScrollArea），没有受限的父高度就会一路撑开，Skills
  单独就能把 Channel 顶出可视区。现值 240px / 300px，各自内部滚动。

## 2026-09-03 (改版，Owner 参考稿) — 去头像/描述，补 Skills 与 Channel

版式按 Owner 的参考稿走：

- **身份只剩名称。** 头像去掉 —— 全项目没有 agent 头像能力，侧边栏那个是按
  identity 生成的色块，画一个「更换头像」等于暗示一个不存在的功能。
- **描述字段去掉，但对话仍然会写它。** 它是**给机器读的**：别的 agent 靠它判断
  要不要把任务路由过来（见 [[builderProtocol.ts]] 的指令），人看的地方是 Agent
  Profile 页。所以从这个面板移除，不代表停止写入。
- **「指令」改叫「认知」**，对齐它真正写的那个字段（awareness），不再用同义词。
- **Skills / Channel 补上**，而且是**内嵌抽屉自己那两个 section**
  （`SkillsPanel section="skills"` / `AwarenessPanel section="channels"`）。

## 关于内嵌：这是对早先决定的推翻

初版刻意**不**内嵌那两个面板，理由是「一个 tab 一个 panel」的 IA 和 lazy chunk
成本。Owner 要求在 studio 里就能配完，所以推翻。**复用而不是重新实现**：自己再
写一份渠道状态读取或 skill 列表，那两个 tab 一改就会漂移。代价是这个 tab 会连带
拉它们的 chunk —— 已接受。

## 建议与真实配置是分开的两层

对话产出的**建议**（skill / channel）压在各自 section 上方，装与绑仍然要人点。
理由见 [[builderApply.ts]]：装 skill 会往 workspace 复制文件，模型改主意就会当着
用户的面装了又卸；绑渠道要凭证，凭证从用户直达后端，绝不进对话信封。

结构由 `components/bookmarks/__tests__/builderTab.test.tsx` 钉住，包括「没有头像、
没有描述字段」这两条否定断言。

# BuilderConfigPanel.tsx — 「对话把面板填好」的那半边

## 这里没有草稿

面板反映的是**真实 agent**。studio 跑在用户刚建出来的那个 agent 上，所以身份和
指令直接从 agent 读、直接写回去 —— 没有暂存区要对账，这两类字段也没有「应用」
这一步。这正是这条路径的意义。

## 为什么不内嵌那三个现成面板

`AwarenessPanel` / `SkillsPanel` / `IMChannelsSection` 本身就是抽屉的原子 tab。
把三个重面板叠进第四个里，既违反「一个 tab 一个 panel」的 IA（Owner
2026-06-11 定案），也让这个 tab 一次拉三份 lazy chunk。所以 studio 只呈现**对话
真正驱动的字段**，深度操作（配 skill 参数、贴 bot token）交回那些 tab。

## 三条约束

- **文本字段 blur 时才存**，不是每次按键。逐字符 PUT 会和模型对同一字段的写入
  竞争。
- **Skills / Channel 是推荐 + 人点**。理由见 [[builderApply.ts]]（装了又卸、
  凭证不能进模型）。渠道那一行的按钮是**跳到 Channels tab**，这里不收集任何
  凭证。
- **没有「放弃」**。面板里每个字段都已经写进 agent 了，没有可回滚的东西；
  「完成」只是离开 studio（[[builderSession.ts]]）。

## Gotcha

三个本地镜像 state 都有 effect 跟随服务端值 —— 这正是模型的写入能在面板里显现
的机制（`refreshAgents` / `refreshAwareness` 之后 store 变，effect 跟上）。
