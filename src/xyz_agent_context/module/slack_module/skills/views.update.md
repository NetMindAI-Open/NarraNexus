# views.update

## Description
Update an existing view.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `view_id` | string | no | A unique identifier of the view to be updated. Either `view_id` or `external_id` is required. |
| `external_id` | string | no | A unique identifier of the view set by the developer. Must be unique for all views on a team. Max length of 255 characters. Either `view_id` or `external_id` is required. |
| `view` | string | no | A [view object](/reference/surfaces/views). This must be a JSON-encoded string. |
| `hash` | string | no | A string that represents view state to protect against possible race conditions. |

## Example
```python
slack_cli("views.update", {})
```
