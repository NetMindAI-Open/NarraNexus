---
code_file: backend/integrations/plugins/errors.py
last_verified: 2026-08-28
stub: false
---

# errors.py — 把 pip/npm 子进程失败翻译成结构化、可本地化的失败原因

## 为什么存在

`pip install` / `npm install` 失败时抛出的是一堆退出码和英文 stderr,直接
糊给用户毫无意义。本文件是这个子系统里唯一"猜用户在墙内还是权限不对还是
彻底断网"的地方——集中在一处,而不是让 `service.py` 里堆一坨正则。

## 上下游关系

- **被谁用**：`service.PluginService.install` 在捕获任何安装期异常时调用
  `classify_error`,把结果的 `message` 直接塞进返回给前端的事件里。
- **依赖谁**：`_installers.base.PluginInstallSubprocessError`——只有这一种
  异常类型携带了真正有诊断价值的 stdout+stderr 全文;其他异常类型（比如
  npm 二进制本身缺失导致的 `FileNotFoundError`）退化成走 `str(exc)` 匹配。

## 设计决策

- 三条已知模式（registry 慢、EACCES 权限、DNS/连接彻底不通）按"更具体先
  判"的顺序检查,而不是按字母序或添加顺序——同一段 npm 错误文本经常同时
  含糊地提到 timeout 和网络失败关键词,顺序错了会把"墙内慢"误判成
  "彻底断网",提示的修复建议（换镜像 vs 检查网络)会文不对题。
- 兜底 `unknown` 分支**不**尝试穷举所有可能失败原因——过度分类反而会让
  用户在遇到真正罕见的失败时被一条误导性的具体建议带偏,不如老实说"看详细
  日志"。

## Gotcha / 边界情况

- **触发**：当子进程的 stderr 和 stdout 都合并进
  `PluginInstallSubprocessError.output`（见 `_installers/base.py` 的
  `stream_subprocess`）后,如果 npm/pip 把错误信息包在非英文本地化输出里
  → **症状**：三组正则全部不命中,落到 `unknown` → **根因**：正则只匹配
  英文关键词（`EACCES`、`ENOTFOUND`、`timeout` 等）,这是刻意的权衡——npm/pip
  的英文错误文本本身就是事实上的标准输出格式,支持多语言本地化输出的收益
  远小于正则复杂度的代价。

## 设计决策（续）· 消息只用英文

`_MESSAGES` 的文案是纯英文,**没有**按最初派发指令写成中英双语——铁律 #1
明确写着"代码里不允许中文标识符/注释/字符串",且全仓 `backend/*.py` 里
grep 不出任何中文字符串字面量,没有先例支持在这里破例。`PluginError` 把
`kind` 和 `message` 分开返回正是为了让前端按 `kind` 做本地化展示（中文
UI 需要中文提示时,由前端的 i18n 层负责,不是后端字符串里塞双语)。

## 相关约束

- 铁律 #1 —— 代码只能英文,不含中文字符串;这条直接否决了原始派发指令
  里"中英双语提示"的字面要求,改为结构化 `kind` + 英文 `message`。


## 2026-08-28 补 — 新增 PluginBusyError

`PluginBusyError(RuntimeError)`(带 `plugin_id`):install/uninstall 共用一把锁,锁被占时拒绝并抛它;[[routes]] 映射成 HTTP 409。与 `classify_error`/`PluginError`(装失败分类)是两回事——前者是并发拒绝,后者是子进程失败归类。
