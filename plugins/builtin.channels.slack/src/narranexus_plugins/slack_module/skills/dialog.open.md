# dialog.open

## Description
Open a dialog with a user

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `dialog` | string | yes | The dialog definition. This must be a JSON-encoded string. |
| `trigger_id` | string | yes | Exchange a trigger to post to the user. |

## Example
```python
slack_cli("dialog.open", {"dialog": "...", "trigger_id": "..."})
```
