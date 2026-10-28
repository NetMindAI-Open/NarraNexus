# views.push

## Description
Push a view onto the stack of a root view.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `trigger_id` | string | yes | Exchange a trigger to post to the user. |
| `view` | string | yes | A [view payload](/reference/surfaces/views). This must be a JSON-encoded string. |

## Example
```python
slack_cli("views.push", {"trigger_id": "...", "view": "..."})
```
