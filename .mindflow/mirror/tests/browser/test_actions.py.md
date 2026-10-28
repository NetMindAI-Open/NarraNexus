---
code_file: tests/browser/test_actions.py
last_verified: 2026-09-23
stub: false
---

# 固定动作与控制边界

固定 click/fill/select/press/scroll 不依赖网站访问策略，旧单站禁止、跨轮次和策略变化不影响动作。
任意脚本仍拒绝；输入保持数据编码、参数校验、接管等待和取消时释放按键/鼠标的回归。
