# reactions.get

## Description
Gets reactions for an item.

## Required scope
`reactions:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel where the message to get reactions for was posted. |
| `file` | string | no | File to get reactions for. |
| `file_comment` | string | no | File comment to get reactions for. |
| `full` | boolean | no | If true always return the complete reaction list. |
| `timestamp` | string | no | Timestamp of the message to get reactions for. |

## Example
```python
slack_cli("reactions.get", {})
```
