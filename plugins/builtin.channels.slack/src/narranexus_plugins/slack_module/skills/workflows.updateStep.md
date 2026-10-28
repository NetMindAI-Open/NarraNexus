# workflows.updateStep

## Description
Update the configuration for a workflow extension step.

## Required scope
`workflow.steps:execute`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `workflow_step_edit_id` | string | yes | A context identifier provided with `view_submission` payloads used to call back to `workflows.updateStep`. |
| `inputs` | string | no | A JSON key-value map of inputs required from a user during configuration. This is the data your app expects to receive when the workflow step starts. **Please note**: the embedded variable format is set and replaced by the workflow system. You cannot create custom variables that will be replaced at runtime. [Read more about variables in workflow steps here](/workflows/steps#variables). |
| `outputs` | string | no | An JSON array of output objects used during step execution. This is the data your app agrees to provide when your workflow step was executed. |
| `step_name` | string | no | An optional field that can be used to override the step name that is shown in the Workflow Builder. |
| `step_image_url` | string | no | An optional field that can be used to override app image that is shown in the Workflow Builder. |

## Example
```python
slack_cli("workflows.updateStep", {"workflow_step_edit_id": "..."})
```
