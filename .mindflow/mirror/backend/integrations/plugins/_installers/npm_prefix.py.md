---
code_file: backend/integrations/plugins/_installers/npm_prefix.py
last_verified: 2026-08-28
stub: false
---

# npm_prefix.py — `npm install --prefix <nodejs>` 策略,驱动 Claude CLI

## 为什么存在

Claude Code 插件除了 pip wheel,还需要一个**独立于 SDK 自带二进制**的
Claude CLI（`cli_binary.py` 的设计原因:2.1.220 相比 SDK 自带的 2.1.56 有
更好的 prompt cache 行为)。这个文件是唯一知道"怎么把这个 CLI 装进用户
可写的 node 目录、怎么反查已装版本"的地方。

## 上下游关系

- **被谁用**：`service.PluginService.__init__` 实例化一个
  `NpmPrefixInstaller` 挂在 `self._installers["npm"]`;只有 `claude_code`
  这一个插件的 npm `InstallComponent` 会走到它（`codex_cli` 没有 npm
  component)。
- **依赖谁**：`narranexus.platform.agent_framework.plugin_paths` 的
  `claude_cli_path` / `node_prefix`（落点单一真值)。

## 设计决策

- `detect` 版本探测复用了和 `cli_binary._probe_version` 几乎一样的正则
  `(\d+\.\d+\.\d+)` + `subprocess.run(..., timeout=...)` 模式,但**故意
  不 import `cli_binary` 里的实现**——`cli_binary.py` 探测的是"agent loop
  实际会启动哪个二进制"（面向运行时,fail-open 到 SDK 自带二进制),这里
  探测的是"插件商店该不该显示已装/待更新"（面向安装状态,不涉及任何
  fallback 决策)。两者语义不同,共享代码的收益（省几行正则)不值得把两个
  不同关注点耦合在一起。
- npm requirement 的版本解析用 `@(\d[\w.\-]*)$`（锚定在末尾且必须紧跟
  数字)——因为 scoped 包名本身也含 `@`（`@anthropic-ai/claude-code`),
  不能简单 split("@")取最后一段(那样"claude-code"本身如果带连字符数字
  也可能被误切)。用"末尾且紧跟数字"这个规则,是因为 npm 版本号规范上
  必须以数字开头,这一点可以放心依赖。
- 卸载直接 `shutil.rmtree(node_prefix())` 整棵目录,而不是只删
  `node_modules/@anthropic-ai/claude-code`——因为 `node_prefix()` 是本插件
  专属的落点（不和任何其他 npm 包共享),整棵删更简单也不会有遗留的
  `package-lock.json`/`.bin` 符号链接指向已删除的包。

## Gotcha / 边界情况

- **触发**：`claude --version` 输出格式不含形如 `X.Y.Z` 的版本号（例如
  二进制损坏只打印一行错误)→ **症状**：`_probe_version` 返回 `None`,
  `detect` 报告 `installed=False`（即使 `claude_cli_path()` 文件本身存在)
  → **根因**：`installed` 字段刻意绑定"版本可读",而不是"文件存在"——
  一个存在但跑不动的二进制对用户来说等同于没装,展示"未安装"比展示
  "已安装但版本未知"更诚实,也会引导用户重新安装而不是误以为能用。

## 相关约束

- `cli_binary.py` 的 docstring —— 2.1.56 vs 2.1.220 的行为差异是"为什么
  要单独装一个 npm CLI 而不是只依赖 SDK 自带二进制"这个问题的完整答案。

## 2026-08-28 补 — 卸载改精确 npm uninstall(不再整棵 rmtree)

预审(I2)指出原 uninstall 直接 `rmtree(node_prefix())` 会把整棵 `~/.narranexus/plugins/nodejs` 连锅端——今天只有 claude 用该 prefix 无副作用,但将来第二个 npm 插件共享该 prefix 会被误删,且与 pip 侧'按包精确删'不对称。改为 `npm uninstall --prefix <dir> <pkg>`(`_package_name` 剥掉 `@version`),让 npm 只删该包+prune 依赖+清 .bin shim。npm 对已不存在的包是 no-op,可重复调用。

## 2026-08-28 补(auto-review C2/N8) — target 参数 + detect 语义修正 + 探测超时降

三方法加 `target`(npm 恒=共享 node prefix)。detect **语义修正**:cli 文件存在即 `installed=True`(哪怕 `claude --version` 读不到→version=None,并 warn),只有文件不存在才 False——否则 PATH 缺 bundled node 会误报'未装',用户重装也修不好。探测超时 20→5s(detect 走 run_in_threadpool,但 20s 仍占线程+转圈)。
