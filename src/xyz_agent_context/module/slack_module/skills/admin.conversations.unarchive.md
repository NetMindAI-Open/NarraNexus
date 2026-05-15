# admin.conversations.unarchive

## Description
Unarchive a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to unarchive. |

## Example
```python
slack_cli("admin.conversations.unarchive", {"channel_id": "..."})
```
