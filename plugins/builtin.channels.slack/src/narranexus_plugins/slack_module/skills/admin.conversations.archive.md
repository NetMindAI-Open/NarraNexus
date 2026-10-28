# admin.conversations.archive

## Description
Archive a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to archive. |

## Example
```python
slack_cli("admin.conversations.archive", {"channel_id": "..."})
```
