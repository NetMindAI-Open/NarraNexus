# exceptions.py

Custom exception hierarchy for the agent context package — typed errors with rich context that prevent silent swallowing.

## Why it exists

Before this hierarchy, errors from module hooks (e.g., a `JobModule` that fails to load jobs) were either silently caught and logged as generic `Exception`, or raised as bare `RuntimeError` with no context about which module or hook was involved. `exceptions.py` establishes a typed hierarchy so that callers can distinguish module-level failures from lower-level database or network errors, and so that log entries carry structured information (module name, hook name, cause) rather than just a message string.

## Upstream / Downstream

**Raised by:** `module/hook_manager.py` (wraps exceptions from `hook_data_gathering` and `hook_after_event_execution`), individual module implementations, and `agent_runtime/` when pipeline steps fail.

**Caught by:** `agent_runtime/` (to decide whether to abort the run or skip a module), `backend/routes/` (to map typed errors to HTTP status codes), and test code that asserts specific failure modes.

**Re-exported from:** `utils/__init__.py` as `AgentContextError`, `ModuleError`, `DataGatheringError`, `HookExecutionError`.

**Depends on:** nothing — pure Python, no imports.

## Design decisions

**`AgentContextError` as the base class.** All custom exceptions inherit from `AgentContextError`. Callers that want to catch any application error can catch this base without caring about the specific subtype.

**`cause` parameter and `from e` chaining.** Each exception accepts an optional `cause` exception and formats it into the message string. Callers should also use `raise NewError(...) from original_exception` to preserve the full traceback chain. The `cause` attribute is also available for programmatic inspection.

**`context` as `**kwargs`.** Free-form keyword arguments are accepted and included in both the formatted message and the `to_dict()` output. This allows callers to attach arbitrary diagnostic fields (e.g., `user_id`, `agent_id`, `hook_name`) without needing a dedicated parameter for each.

**`to_dict()` for structured logging.** The method returns a dict with `error_type`, `message`, `cause`, `cause_type`, and any context kwargs. This is intended for passing to `logger.error(exc.to_dict())` rather than formatting the exception as a string.

**Narrow hierarchy, not deep.** Only three levels exist: base → `ModuleError` → `DataGatheringError` / `HookExecutionError`. This is intentional — a deep hierarchy creates friction when catching errors at an intermediate level. New exception types should be added only when a caller genuinely needs to distinguish them.

## Gotchas

**`context` is stored as `self.context` but also embedded in the formatted message.** Callers that read `exc.context` can access the raw dict. Callers that just print the exception see a formatted summary. These two paths can differ in detail level.

**New-contributor trap.** The constructor calls `super().__init__(self._format_message())` which means `str(exc)` returns the formatted message with cause and context already included. If you log `logger.error(str(exc))` and also log `exc.to_dict()`, you will double-log the cause information.
