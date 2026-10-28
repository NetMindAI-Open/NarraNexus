---
code_file: src/narranexus/platform/browser/_browser_impl/visual.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

selector 经 `limits.check_selector` 校验（与 act / read 同一上限）。

# 视觉观察的几何与预算

`browser_look` 的页面侧逻辑：固定的视口探测函数（URL、title、`performance.timeOrigin` 作为文档
身份、视口与滚动、DPR、裁剪区），参数只以 JSON 传入，调用方无法注入脚本。selector 必须恰好匹配
一个可见元素，裁剪区必须完全落在视口内——只截可见区域，想看下面的内容先滚动。

**输出预算**：长边 1568px、约 115 万像素。各家视觉模型超过这个量级都会自己缩图（Anthropic 1568 /
1.15MP，OpenAI high detail 短边 768），截更大只浪费带宽、内存和载荷。`capture_scale` 按实测的 Chrome
语义换算：输出像素 = CSS 尺寸 × clip.scale × DPR（2026-09-24 用真 Chrome、DPR=2 实测），先乘用户要求
的放大倍数再压回预算内。所以局部放大有效，整屏放大会被预算吃掉——工具说明因此提示「缩小区域来放大」。

**坐标映射不依赖上面的算术**：`png_dimensions` 从返回的 PNG 头读真实尺寸，`VisualObservation.point`
按 裁剪区 / 真实图片尺寸 换算回视口 CSS 像素。即使 Chrome 的取整或 DPR 行为变了，点击仍然准确。

`VisualObservation.matches` 比较 page_id、调用 scope、URL、文档身份与视口：滚动、缩放、刷新、跳转、
切标签或换 turn 都会让观察过期。过期观察在 session 层直接拒绝，不在变化后的页面上猜坐标。
