# stars.add

## Description
Adds a star to an item.

## Required scope
`stars:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel to add star to, or channel where the message to add star to was posted (used with `timestamp`). |
| `file` | string | no | File to add star to. |
| `file_comment` | string | no | File comment to add star to. |
| `timestamp` | string | no | Timestamp of the message to add star to. |

## Example
```python
slack_cli("stars.add", {})
```
