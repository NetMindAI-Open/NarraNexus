---
code_file: backend/routes/notices.py
last_verified: 2026-07-03
---
# notices.py — user-scope system notices (inbox_table read side)

Read side of `inbox_table` (upstream #52 follow-up). Writers
(`MessageBusTrigger._notify_permanent_failure`) had been dropping
SYSTEM_NOTICE rows nobody could ever see — the table had zero consumers
until 2026-07-03. GET / lists the current user's notices + unread count;
POST /{id}/read marks one read with 404 masking foreign rows (same policy
as agents_bus_failures.py). Mounted at /api/notices and added to
QUOTA_BYPASS_PREFIXES: the primary notice class is "your provider is
broken/exhausted" — exactly the users the quota gate would 402 on the
mark-read POST. Distinct from inbox.py (/api/agent-inbox), which reads
message-bus channel tables per AGENT; this is per USER.
