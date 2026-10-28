# admin.conversations.rename

## Description
Rename a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to rename. |
| `name` | string | yes | — |

## Example
```python
slack_cli("admin.conversations.rename", {"channel_id": "...", "name": "..."})
```
