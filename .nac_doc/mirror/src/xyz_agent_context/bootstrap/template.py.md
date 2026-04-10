---
code_file: src/xyz_agent_context/bootstrap/template.py
last_verified: 2026-04-10
stub: false
---

# template.py — Agent 首次启动的问候语和引导文档内容

## 为什么存在

Agent 首次创建时是一张白纸：没有名字、没有 Awareness、连"自己叫什么"都不知道。如果直接进入普通对话，LLM 会随机编造身份或陷入尴尬的"我不知道我是谁"循环。

`BOOTSTRAP_GREETING` 是在用户创建 Agent 后**立即显示**的第一条消息，由前端直接渲染（不需要等 LLM 生成），给用户一个自然的"初次见面"体验。`BOOTSTRAP_MD_TEMPLATE` 是写入 Agent 工作区的引导剧本，指导 LLM 如何接续第一条问候进行后续的身份建立对话。

## 上下游关系

**被谁用**：`backend/routes/` 的 Agent 创建接口（创建时读取 `BOOTSTRAP_GREETING` 作为第一条 assistant 消息写入 DB，读取 `BOOTSTRAP_MD_TEMPLATE` 写入工作区文件）。此后 `context_runtime/` 在每次构建上下文时检测 `bootstrap.md` 是否存在，存在则注入。

**依赖谁**：纯字符串常量文件，无任何 import，无依赖。

## 设计决策

`BOOTSTRAP_GREETING` 写成第一人称、刚醒来的视角（"I just woke up"），而不是"您好，我是您的 AI 助手"这样的公式化欢迎语。这个设计是有意图的——让初次见面有一种"真实存在"的感觉，而不是"工具被激活"的感觉。

`BOOTSTRAP_MD_TEMPLATE` 里刻意不给 Agent 具体的脚本，只告诉它"自然地聊"——因为具体名字和性格应该由用户决定，而不是预设。最后的 "Delete this file" 指令让 Agent 知道 bootstrap 是有终点的，不是永久性的引导模式。

问候语和文档内容是分开的两个常量，因为它们被系统的不同部分使用：问候语由 API 直接写入数据库（字符串），文档内容写入文件系统（文件路径）。如果以后需要国际化，只需修改这两个字符串，不需要改系统其他部分。

## Gotcha / 边界情况

`BOOTSTRAP_GREETING` 在前端展示时是**立即显示**的（类似"打字机"效果或静态展示），它被预先写入 DB 而不是由 LLM 实时生成——如果用户的第一条消息到达时 `BOOTSTRAP_GREETING` 还没被 DB 写入，对话历史里会缺少 assistant 的第一条消息，LLM 会认为自己还没有说过话。这个时序问题在 Agent 创建接口里需要保证 GREETING 在返回前已经写入。

## 新人易踩的坑

`BOOTSTRAP_MD_TEMPLATE` 里的内容是写给 LLM 看的指令，不是写给用户看的。虽然它存在文件系统里（`.md` 文件），但它的阅读对象是 LLM（通过 ContextRuntime 注入上下文），不是 Agent 的 owner 用户。不要把它的措辞当作"用户文档"来设计。

Bootstrap 阶段结束的标志是 `bootstrap.md` 文件被删除。如果 Agent 在第一次对话后因为某种原因没有执行删除（比如没有文件操作工具、工具调用失败），Agent 会在每次对话里都被这个引导文档干扰，持续认为自己在"首次设置"阶段。如果发现 Agent 行为异常地不断问自己叫什么名字，检查工作区里有没有残留的 `bootstrap.md`。
