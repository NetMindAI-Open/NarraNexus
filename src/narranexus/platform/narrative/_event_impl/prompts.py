"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for the Event Prompt Builder
"""

# ============================================================================
# Event section header description
# Used in EventPromptBuilder.get_head_tail() for the head part
# ============================================================================
EVENT_HISTORY_HEAD_PROMPT = """
## Event Info
An Event represents a complete record of one unit of work performed by you inside the narrative.
Each Event documents:
- The input you received
- The reasoning process you followed
- Any tools or modules you invoked
- The final output you produced

Events form the chronological execution trace of your behavior within the narrative.
You must treat them as authoritative historical records for maintaining coherence, debugging past decisions, and understanding how the current state was reached.
        """

# ============================================================================
# Event section footer requirements
# Used in EventPromptBuilder.get_head_tail() for the tail part
# ============================================================================
EVENT_HISTORY_TAIL_PROMPT = """
### Requirements
1. You must treat the events as an ordered execution history.
2. When analyzing or referencing past behavior, rely strictly on the order and content of events as given.
3. Events must be interpreted as authoritative: do not contradict or reinterpret them unless explicitly instructed.
4. Use the sequence of events to understand evolving goals, context changes, and dependencies between past actions and current tasks.
5. When reasoning, ensure continuity by aligning your interpretation with the cumulative history encoded in events.
"""

# ============================================================================
# Single Event template
# Used in EventPromptBuilder.build_single()
#
# Placeholder descriptions:
# - {order}: Event sequence number
# - {event_id}: Event ID, from event.id
# - {narrative_id}: Narrative ID, from event.narrative_id
# - {created_at}: Creation time, from event.created_at
# - {updated_at}: Update time, from event.updated_at
# - {trigger}: Trigger condition, from event.trigger
# - {trigger_source}: Trigger source, from event.trigger_source
# - {env_context}: Environment context, from event.env_context
# - {module_instances_prompt}: Module instance descriptions, dynamically built by build_single()
# - {event_log}: Event log, from event.event_log
# - {final_output}: Final output, from event.final_output
# ============================================================================
EVENT_DETAIL_PROMPT_TEMPLATE = """
### Event-{order}

#### Basic Metadata
- Event ID: {event_id}
- Narrative ID: {narrative_id}
    This identifies the narrative that the event belongs to and provides context for interpreting the event.
- Event Created At: {created_at}
- Event Updated At: {updated_at}
    This timestamp marks the latest modification, including tool actions or state updates.

#### Event Info
- Event Trigger: {trigger}
    The specific input, request, or condition that caused this event to occur.
- Event Trigger Source: {trigger_source}
    The origin of the trigger, such as user message, internal system trigger, scheduled task, or module-level signal.
- Event Env Context: {env_context}
    A structured dictionary describing the environmental context relevant to this event.
    Keys represent context names; values represent their resolved values at the moment of execution.
- Event Module Instances: {module_instances_prompt}
    The module instances participating in this event, including their state, responsibilities, and any constraints they impose.
- Event Event Log: {event_log}
    A chronological log of intermediate steps, including reasoning traces, tool calls, state updates, and internal transitions.
- Event Final Output: {final_output}
    The resolved output of the event after processing all inputs, reasoning, and tool executions.
"""
