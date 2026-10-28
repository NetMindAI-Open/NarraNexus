---
code_file: src/narranexus/platform/browser/_browser_impl/read.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

selector 经 `limits.check_selector` 校验（与 act / look 同一上限）。

# 可操作快照与分页

固定 DOM 函数提供普通 access 权限下的读取能力。模型只能给 JSON 参数，不能注入
程序。调用前由 BrowserSession 校验当前页面权限，不依赖 full_cdp_access。

可见字段、按钮和链接提供可直接用于 act 的 selector；优先唯一 id/CSS.escape，
重复或无 id 使用祖先及 nth-of-type 路径。不改 DOM、不滚动或抢焦点。
标签来自 aria 引用、原生 label 等；字段返回当前值和状态、select 选项，但密码值
始终省略。控件列表取消旧的静默数量截断。定位范围与 act 一致：主文档，不穿透
iframe/shadow DOM；页面改动后重新读取，不承诺旧路径跨 DOM 变化仍有效。

selector 缩小到子树并包含根元素本身。正文按 Unicode 码点分页，offset 是非负安全
整数，limit 在 1..20000；total/offset/next_offset 支持连续读取。数据每次来自当前
DOM，不是冻结快照。错误 selector、越界参数或超出当前长度的 offset 显式失败。
