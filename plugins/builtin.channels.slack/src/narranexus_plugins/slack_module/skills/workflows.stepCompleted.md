# workflows.stepCompleted

## Description
Indicate that an app's step in a workflow completed execution.

## Required scope
`workflow.steps:execute`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `workflow_step_execute_id` | string | yes | Context identifier that maps to the correct workflow step execution. |
| `outputs` | string | no | Key-value object of outputs from your step. Keys of this object reflect the configured `key` properties of your [`outputs`](/reference/workflows/workflow_step#output) array from your `workflow_step` object. |

## Example
```python
slack_cli("workflows.stepCompleted", {"workflow_step_execute_id": "..."})
```
