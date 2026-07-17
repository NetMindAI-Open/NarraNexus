---
code_file: tests/repository/test_mcp_repository_headers.py
last_verified: 2026-07-15
---

# test_mcp_repository_headers.py — MCP 自定义请求头存储/校验守卫

锁定 2026-07-15 headers 支持的数据层行为：`mcp_urls.headers` JSON 列经
add/get/list 往返不失真；update 整组覆盖/None 清空；
`validate_mcp_sse_connection` 把自定义头合并在 SSE 基线头之上发出，无
headers 时保持匿名基线。httpx 用假 client 捕获请求头，不出网。
