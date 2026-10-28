# views.publish

## Description
Publish a static view for a User.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user_id` | string | yes | `id` of the user you want publish a view to. |
| `view` | string | yes | A [view payload](/reference/surfaces/views). This must be a JSON-encoded string. |
| `hash` | string | no | A string that represents view state to protect against possible race conditions. |

## Example
```python
slack_cli("views.publish", {"user_id": "...", "view": "..."})
```
