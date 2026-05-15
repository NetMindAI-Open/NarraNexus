# reactions.remove

## Description
Removes a reaction from an item.

## Required scope
`reactions:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | Reaction (emoji) name. |
| `file` | string | no | File to remove reaction from. |
| `file_comment` | string | no | File comment to remove reaction from. |
| `channel` | string | no | Channel where the message to remove reaction from was posted. |
| `timestamp` | string | no | Timestamp of the message to remove reaction from. |

## Example
```python
slack_cli("reactions.remove", {"name": "..."})
```
