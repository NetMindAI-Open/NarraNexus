---
code_file: src/narranexus/platform/browser/_browser_impl/limits.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

工具说明与本模块常量一致地以字符数表述上限；钉住两者的测试移到 `tests/module/test_browser_module_integration.py`
（`browser` fixture 所在处），不再跨测试模块导入 fixture。

# 模型参数的长度上限，一处定义

浏览器其他数据通路都有上限（流上的用户输入 64 KiB、截图像素/字节、页面正文分页），唯独 agent
**写进页面**的参数没有（review I8）。每个参数都会被嵌进一条 CDP `Runtime.evaluate` 消息：模型跑飞
或把整页内容粘进表单字段，就会在服务所有 agent 工具的 MCP host 里变成一帧几 MB 的消息。

上限对真实使用很宽松：表单文本对齐流的 64 KiB 输入上限；selector 4096 字符，足够生成的深层
`nth-of-type` 路径。`act` / `read` / `look` 都通过 `check_selector` 校验，`run` 通过 `check_text`，
所以四个工具不会各自漂移。超限是 ValueError，由各自的错误包装转成给 agent 的可执行提示；工具说明
里写着同样的数字，`tests/browser/test_limits.py` 把两者钉在一起。
