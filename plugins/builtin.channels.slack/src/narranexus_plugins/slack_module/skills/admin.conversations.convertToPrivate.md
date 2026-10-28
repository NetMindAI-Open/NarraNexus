# admin.conversations.convertToPrivate

## Description
Convert a public channel to a private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to convert to private. |

## Example
```python
slack_cli("admin.conversations.convertToPrivate", {"channel_id": "..."})
```
