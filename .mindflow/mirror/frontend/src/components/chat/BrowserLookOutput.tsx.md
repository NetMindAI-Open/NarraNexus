---
code_file: frontend/src/components/chat/BrowserLookOutput.tsx
last_verified: 2026-09-24
stub: false
---

# browser_look 输出行

截图直接给了模型、不落库，所以没有缩略图可放；这一行说清看了什么（图片尺寸、非整屏时的裁剪区、
页面标题，失败则红字原因），并给「打开浏览器」入口（`openPanel` 打开而非切换，与登录通知一致）。
沿用通用输出行的版式：agent 文档里的过程行不取层（design_system §2.6.1），只用语义 token 与 lucide
线性图标；原始输出折叠在一次点击后（铁律 #16）。已在真实前端亮/暗两主题下目视验收（2026-09-24）。
