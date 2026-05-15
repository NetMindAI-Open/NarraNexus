# stars.remove

## Description
Removes a star from an item.

## Required scope
`stars:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel to remove star from, or channel where the message to remove star from was posted (used with `timestamp`). |
| `file` | string | no | File to remove star from. |
| `file_comment` | string | no | File comment to remove star from. |
| `timestamp` | string | no | Timestamp of the message to remove star from. |

## Example
```python
slack_cli("stars.remove", {})
```
