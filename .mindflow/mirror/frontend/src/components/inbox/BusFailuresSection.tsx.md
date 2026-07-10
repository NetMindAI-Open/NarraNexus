---
code_file: frontend/src/components/inbox/BusFailuresSection.tsx
last_verified: 2026-07-03
---
# BusFailuresSection.tsx — parked bus-failure recovery surface (upstream #52)

Lists the agent's poison-threshold-parked bus messages
(GET /api/agents/{id}/bus-failures) with a retry action (POST .../retry
clears the failure row; next poll re-delivers). Renders nothing when
clean — zero noise on the happy path. Opening the section consumes the
matching unread notices (source.type == message_bus_failure) via
/api/notices: the notice exists to bring the owner here, so viewing IS
the read. Hosted at the top of AgentInboxPanel ("messages your agent
failed to process" belongs with the inbox) rather than a new drawer tab —
keeps AtomicTabId/tab-strip untouched.
