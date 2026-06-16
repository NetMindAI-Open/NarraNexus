"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-11-25
@description: JobModule (JobTrigger) prompt definitions
"""

# ============================================================================
# Task info section template
# Used for the task info part of _build_execution_prompt()
#
# Placeholder description:
# - {title}: Job title, from job.title
# - {description}: Job description, from job.description
# - {created_str}: Creation time (formatted), from job.created_at
# - {current_time_str}: Current time (formatted)
# - {execution_user_id}: Execution identity user_id
# - {user_id}: Task creator user_id, from job.user_id
# ============================================================================
JOB_TASK_INFO_TEMPLATE = """#### Task Information
- **Title**: {title}
- **Description**: {description}
- **Created at**: {created_str}
- **Current time**: {current_time_str}
- **Execution identity**: {execution_identity}
- **Task creator**: {task_creator}"""

# ============================================================================
# Related entities section template
# Used in _build_execution_prompt() when entities_info exists
#
# Placeholder description:
# - {entity_lines}: Formatted entity info lines, dynamically built by _build_execution_prompt()
# ============================================================================
JOB_ENTITIES_SECTION_TEMPLATE = """
#### Related People/Entities

{entity_lines}
"""

# ============================================================================
# Current progress section template
# Used in _build_execution_prompt() when narrative_summary exists
#
# Placeholder description:
# - {narrative_summary}: Narrative summary
# ============================================================================
JOB_PROGRESS_SECTION_TEMPLATE = """
#### Current Progress

{narrative_summary}
"""

# ============================================================================
# Dependency section template
# Used in _build_execution_prompt() when dependent Job outputs exist
#
# Placeholder description:
# - {dep_parts}: Formatted dependency task outputs, dynamically built by _build_execution_prompt()
# ============================================================================
JOB_DEPENDENCIES_SECTION_TEMPLATE = """
#### Prerequisite Task Results

The following are the execution results of prerequisite tasks this task depends on. Please refer to this information when executing this task:

{dep_parts}
---
"""

# ============================================================================
# Job execution main prompt template
# Used by _build_execution_prompt() to assemble the final prompt
#
# Placeholder description:
# - {task_info_section}: Task info section, formatted by JOB_TASK_INFO_TEMPLATE
# - {entities_section}: Related entities section, can be empty string
# - {narrative_section}: Current progress section, can be empty string
# - {dependency_section}: Dependency section, can be empty string
# - {payload}: Execution instructions, from job.payload
# - {related_entity_id}: Target user ID, from job.related_entity_id
# - {extra_requirement}: Extra requirement line (when context info exists), can be empty string
# ============================================================================
JOB_EXECUTION_PROMPT_TEMPLATE = """You are executing a background scheduled task. The user may not be online right now, but your message will appear in their chat history for them to read later.

{task_info_section}
{entities_section}
{narrative_section}
{dependency_section}
#### Execution Instructions
{payload}

#### Execution Context
- **Target entity**: {related_entity_id}
- Your Narrative, memory, and chat history are loaded for this entity
- When you call `send_message_to_user_directly`, the message will appear in the chat history with this entity — the owner will see it when they open this conversation

#### Important Requirements
1. Complete all steps required for the task (search, analyze, organize, etc.)
2. **After completing the task, you MUST use `send_message_to_user_directly` to send the final report to the user**
3. The content sent should be the final report — do not include your thinking process
4. The content should be complete, valuable, and clearly formatted (use Markdown)
5. Send exactly ONE message with the final report. Do NOT send intermediate progress updates
{extra_requirement}
"""


# ============================================================================
# ONGOING Job chat analysis prompt
# Used by _job_lifecycle.update_ongoing_jobs_from_chat() to check if a chat
# interaction satisfies an ONGOING job's end_condition.
#
# Placeholder description:
# - {job_id}: Job ID
# - {title}: Job title
# - {description}: Job description
# - {payload_preview}: First 500 chars of payload
# - {end_condition}: The end condition text
# - {iteration_count}: Current iteration count
# - {max_iterations}: Maximum iterations or "No limit"
# - {user_query}: The user's query text
# - {chat_content_preview}: First 1000 chars of agent response
# ============================================================================
ONGOING_CHAT_ANALYSIS_PROMPT = """Analyze if the current chat interaction satisfies the end condition of an ONGOING job.

## Job Information

**Job ID**: {job_id}
**Title**: {title}
**Description**: {description}
**Payload**: {payload_preview}...

**End Condition**: {end_condition}

**Current Iteration**: {iteration_count}
**Max Iterations**: {max_iterations}

## Current Chat Interaction

**User Query**: {user_query}

**Agent Response**: {chat_content_preview}...

## Your Task

Determine if this chat interaction indicates that the job's end condition has been met.

For example, if the end condition is "customer shows purchase intent or explicit rejection":
- Customer says "I'll buy it" -> end condition MET
- Customer says "No thanks, I don't need it" -> end condition MET
- Customer asks "What's the price?" -> end condition NOT MET (still interested, continuing conversation)

## Return Fields

1. **job_id**: "{job_id}"

2. **is_end_condition_met**: true/false - Does this interaction satisfy the end condition?

3. **end_condition_reason**: Detailed explanation of why the condition is/isn't met

4. **should_continue**: true/false - Should the job continue?
   - false if end_condition is met
   - false if max_iterations reached (current: {iteration_count}, max: {max_iterations})
   - true otherwise

5. **progress_summary**: 1-2 sentence summary of what happened in this interaction

6. **process**: 2-3 concise descriptions of actions taken
"""
