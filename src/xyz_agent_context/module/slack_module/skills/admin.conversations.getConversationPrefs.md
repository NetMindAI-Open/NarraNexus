# admin.conversations.getConversationPrefs

## Description
Get conversation preferences for a public or private channel.

## Required scope
`admin.conversations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to get preferences for. |

## Example
```python
slack_cli("admin.conversations.getConversationPrefs", {"channel_id": "..."})
```
