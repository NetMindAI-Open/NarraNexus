---
code_file: frontend/src/components/artifacts/__tests__/scrollContainment.test.tsx
last_verified: 2026-08-04
stub: false
---

# scrollContainment.test.tsx — ArtifactRenderer 滚动归属契约测试

## 为什么存在

云端 artifact 滑不动（2026-07-13 用户报告，Base recvpm05jsLg3o）的根因
是渲染器根节点 auto 高度、真溢出发生在 overflow-hidden 的列容器里被
静默裁掉。修复后的契约有两半，本测试都钉死：

1. **bounded=true（列内，默认）**：分发器包装盒 `h-full w-full
   overflow-auto overscroll-contain` 是唯一滚动容器（含宽表横向）；
   包装不含 absolute（弹窗缩放层内会逃逸）。
2. **bounded=false（ZoomModal）**：渲染树里**零**滚动容器——弹窗外层
   overflow-auto 统一承载，渲染器根节点保持 auto 高度（否则 h-full 对
   缩放层确定高度解析、内容钳成一屏）。第一版就是栽在这里被 review
   打回的，这条断言防复发。

测试位于 `artifacts/__tests__/`（不在 `renderers/__tests__/`）：被测
对象是分发器 ArtifactRenderer，渲染器只是穿透载体。

## 覆盖边界（重要）

jsdom 无布局引擎，断言只钉 className 与 DOM 形状：防得住「有人摘
class / 加回渲染器级 h-full / 弹窗丢 bounded={false}」，防不住「父链
某层丢确定高度使 h-full 退化成 auto」（正是原 bug 的成因形态）与真实
滚动行为。列/弹窗两条真实路径的滚动仍需手动回归：五种 artifact ×
桌面滚轮 + 移动端触摸模拟 + 弹窗 200% 缩放读长文。

## Mock 模式

沿用 renderers/__tests__/HtmlRenderer.test.tsx：mock
`@/services/artifactsApi` + `@/lib/tauri`（isTauri=false）；
fetchArtifactText 按 URL 含 csv 与否返回表格或 markdown 文本。
