---
code_file: frontend/src/components/chat/team/__tests__/TeamMessageProcess.test.tsx
last_verified: 2026-07-31
stub: false
---

# TeamMessageProcess.test.tsx

钉住四条契约:渲染即出现入口但**不**发请求;首次展开才 `getEventLog` 且参数
正确;开-关-开跨切换只打一次请求(3s 轮询的房间里,一次 per-render 请求就是
请求风暴);空 timeline 降级为 noProcess 文案而不是空白。
