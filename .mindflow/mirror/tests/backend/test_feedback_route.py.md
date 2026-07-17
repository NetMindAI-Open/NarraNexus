---
code_file: tests/backend/test_feedback_route.py
last_verified: 2026-07-10
stub: false
---

# test_feedback_route.py

钉住 POST /api/feedback 中继契约：转发参数（category/summary/source=web_ui/
user_id）、未知类别纠偏为 other、空文本/超长 422、接收端不可达时仍 200
{ok:true, delivered:false}。独立小 FastAPI app 挂载路由测试,不起完整后端。
