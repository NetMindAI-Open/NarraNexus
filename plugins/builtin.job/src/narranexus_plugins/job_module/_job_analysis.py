"""
@file_name: _job_analysis.py
@author: NetMind.AI
@date: 2025-11-25
@description: Job execution result analysis helper functions

Pure functions extracted from JobModule for:
- Extracting Agent Loop execution traces
- Extracting context information
- Building Job analysis prompts
"""

from typing import List, Dict, Any

from loguru import logger


def extract_execution_trace(agent_loop_response: List[Any]) -> str:
    """
    Extract execution trace from agent_loop_response

    agent_loop_response contains all responses during Agent Loop execution:
    - ProgressMessage: tool calls, thinking process, completion markers
    - AgentTextDelta: text output deltas

    Args:
        agent_loop_response: Agent Loop response list

    Returns:
        Formatted execution trace string
    """
    if not agent_loop_response:
        return "No execution trace available."

    trace_items = []
    tool_calls = []
    thinking_items = []

    for item in agent_loop_response:
        if hasattr(item, 'title') and hasattr(item, 'details'):
            title = getattr(item, 'title', '')
            details = getattr(item, 'details', {})

            if 'tool' in title.lower():
                tool_name = details.get('tool_name', 'unknown')
                arguments = details.get('arguments', {})
                args_str = str(arguments)[:200] + "..." if len(str(arguments)) > 200 else str(arguments)
                tool_calls.append(f"- Tool: {tool_name}\n  Args: {args_str}")

            elif 'output' in title.lower():
                output = details.get('output', '')
                output_preview = output[:300] + "..." if len(output) > 300 else output
                if tool_calls:
                    tool_calls[-1] += f"\n  Output: {output_preview}"

            elif 'thinking' in title.lower():
                thinking = details.get('thinking', '')
                thinking_preview = thinking[:200] + "..." if len(thinking) > 200 else thinking
                thinking_items.append(f"- {thinking_preview}")

    if tool_calls:
        trace_items.append("### Tool Calls")
        trace_items.extend(tool_calls)

    if thinking_items:
        trace_items.append("\n### Agent Thinking")
        trace_items.extend(thinking_items[:3])

    if not trace_items:
        return "No tool calls or significant actions recorded."

    return "\n".join(trace_items)


def extract_context_info(ctx_data: Any) -> str:
    """Extract key information from ctx_data"""
    if not ctx_data:
        return "N/A"

    if hasattr(ctx_data, 'model_dump'):
        data = ctx_data.model_dump(exclude_none=True)
    elif hasattr(ctx_data, '__dict__'):
        data = {k: v for k, v in ctx_data.__dict__.items() if v is not None}
    else:
        return str(ctx_data)[:500]

    for key in ['chat_history', 'extra_data']:
        if key in data and data[key]:
            data[key] = f"[{len(data[key])} items]" if isinstance(data[key], list) else "[...]"

    return "\n".join(f"- {k}: {str(v)[:200]}" for k, v in data.items())


def build_job_analysis_prompt(
    current_time: Any,
    input_content: str,
    job_info: Dict[str, Any],
    execution_trace: str,
    final_output: str,
    ctx_data: Any,
) -> str:
    """
    Build Job analysis Prompt

    Provides different guidance for different job_type:
    - ONE_OFF: Completes after execution
    - SCHEDULED: Stays active after execution, awaits next trigger
    - ONGOING: Needs to determine if end_condition is met

    Args:
        current_time: Current time
        input_content: Job execution instruction
        job_info: Complete Job information
        execution_trace: Execution trace
        final_output: Agent output
        ctx_data: Context data

    Returns:
        Constructed Prompt string
    """
    job_type = job_info.get("job_type", "unknown")
    trigger_config = job_info.get("trigger_config", {})
    end_condition = trigger_config.get("end_condition")
    interval_seconds = trigger_config.get("interval_seconds")
    max_iterations = trigger_config.get("max_iterations")
    iteration_count = job_info.get("iteration_count", 0)
    previous_process = job_info.get("process", [])

    # Extract awareness info (if available)
    awareness_info = "N/A"
    if ctx_data and hasattr(ctx_data, 'extra_data') and ctx_data.extra_data:
        awareness = ctx_data.extra_data.get("awareness")
        if awareness:
            awareness_info = str(awareness)[:500]

    # v2 timezone protocol: display times in the job's frozen timezone, not UTC
    job_tz = (trigger_config or {}).get("timezone", "UTC")
    prompt = f"""
Analyze job execution results and determine the job status.

## Current Time ({job_tz})
Now, in the user's frozen timezone for this job.

## Job Information

**Job ID**: {job_info.get("job_id", "unknown")}
**Job Type**: {job_type}
**Title**: {job_info.get("title", "N/A")}
**Description**: {job_info.get("description", "N/A")}
**Payload**: {job_info.get("payload", "N/A")}

### Trigger Configuration
- **Timezone**: {job_tz}
- **End Condition**: {end_condition or "None"}
- **Interval (seconds)**: {interval_seconds or "N/A"}
- **Max Iterations**: {max_iterations or "No limit"}
- **Current Iteration**: {iteration_count}

### Time References ({job_tz})
- **Last run (local)**: {job_info.get("last_run_at_local", "N/A")}
- **Next fire (local, scheduled)**: {job_info.get("next_run_at_local", "N/A")}

### Previous Execution History
{chr(10).join(f"- {p}" for p in previous_process[-5:]) if previous_process else "No previous executions"}

## Current Execution

### Input (Job Instruction)
{input_content}

### Agent Output
{final_output if final_output else 'None'}

### Execution Trace
{execution_trace}

### Agent Awareness (if available)
{awareness_info}

## Status Determination Rules

"""

    if job_type == "ongoing":
        prompt += f"""
**For ONGOING Jobs:**

This job runs repeatedly until the end_condition is satisfied OR max_iterations is reached.

**End Condition**: "{end_condition or 'Not specified'}"

**Status Determination:**
1. Analyze the current execution output and agent awareness context
2. Determine if the end_condition has been MET based on the output
3. Status rules:
   - end_condition MET → "completed"
   - end_condition NOT MET → "active" (continue running)
   - execution error/exception → "failed"

**Note**: Refer to the Agent Awareness section above for specific guidance on how to evaluate the end_condition in this context. The scheduling (next_run_time) is computed deterministically from trigger_config after your decision — you only need to determine status.
"""
    elif job_type == "one_off":
        prompt += """
**For ONE_OFF Jobs:**

This job runs only once.

**Status Determination:**
- execution succeeded → "completed"
- execution failed → "failed"
"""
    elif job_type == "scheduled":
        prompt += f"""
**For SCHEDULED Jobs:**

This job runs on a schedule (interval: {interval_seconds}s, cron: {trigger_config.get("cron", "N/A")}).

**Status Determination:**
- execution succeeded → "active" (waiting for next run)
- execution failed → "failed"
"""

    prompt += """

## Your Task

**Your analysis will directly update the job's status in the database.**

Based on the execution results and context above, determine:
1. What is the current status of this job?
2. Should it continue running or is it done?

## Return Fields

1. **job_id**: The job ID from above (required)

2. **status**:
   - "completed": The task has reached a conclusion (regardless of success or failure outcome).
     No further action is needed on this job.
   - "active": The task is still in progress and needs to continue running.
   - "failed": A technical error or exception occurred during execution.

3. **process**: 2-5 concise action descriptions from this execution

4. **last_error**: Error description if failed, else null

5. **should_notify**: true if user should be notified, false if trivial

6. **notification_summary**: 1-2 sentence summary for user

**Do not** attempt to set `next_run_time`. Scheduling is computed from
`trigger_config` deterministically by the system after your decision.
"""

    return prompt
