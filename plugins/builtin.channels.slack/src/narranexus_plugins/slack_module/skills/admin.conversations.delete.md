# admin.conversations.delete

## Description
Delete a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to delete. |

## Example
```python
slack_cli("admin.conversations.delete", {"channel_id": "..."})
```
