# evermemos/

HTTP client package for the optional EverMemOS external memory service.

## Directory role

`evermemos/` contains the client-side integration with EverMemOS — an optional external service for long-term memory storage and semantic retrieval. The directory is intentionally narrow: it holds only the HTTP client implementation and nothing else. All orchestration logic (when to write, how to blend EverMemOS results with local Narrative memory) lives in the callers, not here.

## Key file index

| File | Role |
|---|---|
| `client.py` | `EverMemOSClient` and `get_evermemos_client()` factory — HTTP write and search operations |

## Collaboration with external directories

- **`narrative/_narrative_impl/`** — historically the primary caller; migrated here to decouple the HTTP client from narrative orchestration.
- **`utils/__init__.py`** — does not re-export EverMemOS symbols; callers must import directly from `xyz_agent_context.utils.evermemos.client`.
- **EverMemOS service** — a separate running process (default `http://localhost:1995`) that this package calls over HTTP. It is not part of this repository.
