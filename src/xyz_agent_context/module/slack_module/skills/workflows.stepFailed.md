# workflows.stepFailed

## Description
Indicate that an app's step in a workflow failed to execute.

## Required scope
`workflow.steps:execute`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `workflow_step_execute_id` | string | yes | Context identifier that maps to the correct workflow step execution. |
| `error` | string | yes | A JSON-based object with a `message` property that should contain a human readable error message. |

## Example
```python
slack_cli("workflows.stepFailed", {"workflow_step_execute_id": "...", "error": "..."})
```
